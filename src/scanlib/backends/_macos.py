from __future__ import annotations

import struct
import tempfile
from collections.abc import Callable
from pathlib import Path

import ImageCaptureCore
import objc
from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop, NSURL

from .._types import (
    ColorMode,
    NoScannerFoundError,
    ScanAborted,
    ScanError,
    ScannerInfo,
    ScanOptions,
    ScanSource,
    ScannedDocument,
)

_ICC_SOURCE_MAP = {
    ImageCaptureCore.ICScannerFunctionalUnitTypeFlatbed: ScanSource.FLATBED,
    ImageCaptureCore.ICScannerFunctionalUnitTypeDocumentFeeder: ScanSource.FEEDER,
}

_SCAN_SOURCE_TO_ICC = {v: k for k, v in _ICC_SOURCE_MAP.items()}

_MM_PER_INCH = 25.4


def _read_png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width and height from PNG file bytes."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ScanError("Invalid PNG data")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _check_progress(
    progress: Callable[[int], bool] | None, percent: int
) -> None:
    """Call the progress callback; raise ScanAborted if it returns False."""
    if progress is not None and progress(percent) is False:
        raise ScanAborted("Scan aborted")


class _BrowserDelegate(ImageCaptureCore.NSObject):
    """Delegate for ICDeviceBrowser to collect discovered scanners."""

    def init(self):
        self = objc.super(_BrowserDelegate, self).init()
        if self is None:
            return None
        self.scanners = []
        self._done = False
        return self

    def deviceBrowser_didAddDevice_moreComing_(self, browser, device, moreComing):
        if device.type() & ImageCaptureCore.ICDeviceTypeMaskScanner:
            self.scanners.append(device)
        if not moreComing:
            self._done = True

    def deviceBrowser_didRemoveDevice_moreGoing_(self, browser, device, moreGoing):
        pass


class _SessionDelegate(ImageCaptureCore.NSObject):
    """Minimal delegate for opening a device session."""

    def init(self):
        self = objc.super(_SessionDelegate, self).init()
        if self is None:
            return None
        self._done = False
        self.error = None
        return self

    def device_didOpenSessionWithError_(self, device, error):
        if error:
            self.error = str(error)
        self._done = True

    def device_didCloseSessionWithError_(self, device, error):
        pass

    def didRemoveDevice_(self, device):
        pass


class _ScanDelegate(ImageCaptureCore.NSObject):
    """Delegate for ICScannerDevice to handle scan lifecycle."""

    def init(self):
        self = objc.super(_ScanDelegate, self).init()
        if self is None:
            return None
        self.scanned_url = None
        self.error = None
        self._done = False
        return self

    def deviceDidBecomeReadyWithCompleteSettings_(self, device):
        pass

    def device_didOpenSessionWithError_(self, device, error):
        if error:
            self.error = str(error)
            self._done = True

    def scannerDevice_didScanToURL_(self, device, url):
        self.scanned_url = url

    def scannerDevice_didCompleteScanWithError_(self, device, error):
        if error:
            self.error = str(error)
        self._done = True

    def device_didCloseSessionWithError_(self, device, error):
        pass

    def didRemoveDevice_(self, device):
        pass


def _run_until(
    done_flag,
    timeout: float,
    progress: Callable[[int], bool] | None = None,
) -> None:
    """Spin the current NSRunLoop until *done_flag._done* is True or *timeout* elapses.

    Raises :class:`ScanAborted` if *progress* returns ``False``.
    """
    run_loop = NSRunLoop.currentRunLoop()
    deadline = NSDate.dateWithTimeIntervalSinceNow_(timeout)
    while not done_flag._done:
        _check_progress(progress, -1)
        if NSDate.date().compare_(deadline) != -1:  # NSOrderedAscending = -1
            break
        run_loop.runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.5)
        )


def _discover_devices() -> tuple[list, object]:
    """Discover scanner devices. Returns (devices, browser)."""
    delegate = _BrowserDelegate.alloc().init()
    browser = ImageCaptureCore.ICDeviceBrowser.alloc().init()
    browser.setDelegate_(delegate)
    browser.setBrowsedDeviceTypeMask_(
        ImageCaptureCore.ICDeviceTypeMaskScanner
        | ImageCaptureCore.ICDeviceLocationTypeMaskLocal
        | ImageCaptureCore.ICDeviceLocationTypeMaskRemote
    )
    browser.start()
    _run_until(delegate, timeout=5.0)
    return delegate.scanners, browser


def _open_device_session(device: object) -> None:
    """Open a session on *device* and wait for it to complete."""
    delegate = _SessionDelegate.alloc().init()
    device.setDelegate_(delegate)
    device.requestOpenSession()
    _run_until(delegate, timeout=10.0)


def _get_device_sources(device: object) -> list[ScanSource]:
    """Get available scan sources from an ICC scanner device.

    Opens a session on the device if needed and waits for capability data.
    """
    opened_here = False
    try:
        if not device.hasOpenSession():
            _open_device_session(device)
            opened_here = True

        # The device may need time to populate capabilities after session opens.
        run_loop = NSRunLoop.currentRunLoop()
        deadline = NSDate.dateWithTimeIntervalSinceNow_(10.0)
        unit_types = device.availableFunctionalUnitTypes()
        while not unit_types:
            if deadline.timeIntervalSinceNow() <= 0:
                break
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.5)
            )
            unit_types = device.availableFunctionalUnitTypes()

        sources = []
        if unit_types:
            for unit_type in unit_types:
                mapped = _ICC_SOURCE_MAP.get(unit_type)
                if mapped is not None:
                    sources.append(mapped)
        return sources
    except Exception:
        return []
    finally:
        if opened_here:
            device.requestCloseSession()


class MacOSBackend:
    """macOS scanning backend using ImageCaptureCore."""

    def list_scanners(self) -> list[ScannerInfo]:
        devices, browser = _discover_devices()
        try:
            return [
                ScannerInfo(
                    name=dev.name(),
                    vendor=None,
                    model=dev.name(),
                    backend="imagecapture",
                    sources=_get_device_sources(dev),
                )
                for dev in devices
            ]
        finally:
            browser.stop()

    def scan(
        self, scanner: ScannerInfo | None, options: ScanOptions
    ) -> ScannedDocument:
        if scanner is None:
            scanners = self.list_scanners()
            if not scanners:
                raise NoScannerFoundError("No scanners found")
            scanner = scanners[0]

        devices, browser = _discover_devices()

        device = None
        for dev in devices:
            if dev.name() == scanner.name:
                device = dev
                break

        if device is None:
            browser.stop()
            raise NoScannerFoundError(f"Scanner {scanner.name!r} not found")

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                scan_delegate = _ScanDelegate.alloc().init()
                device.setDelegate_(scan_delegate)

                downloads_url = NSURL.fileURLWithPath_(tmp_dir)
                device.setDownloadsDirectory_(downloads_url)
                device.setDocumentName_("scanlib_scan")
                device.setDocumentUTI_("public.png")

                # Select scan source if specified
                if options.source is not None:
                    icc_type = _SCAN_SOURCE_TO_ICC.get(options.source)
                    if icc_type is not None:
                        unit_types = device.availableFunctionalUnitTypes()
                        if unit_types and icc_type in unit_types:
                            device.requestSelectFunctionalUnit_(icc_type)

                device.requestOpenSession()

                # Configure page size on the selected functional unit
                if options.page_size is not None:
                    fu = device.selectedFunctionalUnit()
                    if fu is not None:
                        width_pts = options.page_size.width / 10.0 / _MM_PER_INCH * 72
                        height_pts = options.page_size.height / 10.0 / _MM_PER_INCH * 72
                        fu.setScanArea_(((0, 0), (width_pts, height_pts)))

                _check_progress(options.progress, 0)

                device.requestScan()
                try:
                    _run_until(scan_delegate, timeout=120.0, progress=options.progress)
                except ScanAborted:
                    device.cancelScan()
                    device.requestCloseSession()
                    raise

                if scan_delegate.error:
                    err_lower = scan_delegate.error.lower()
                    if "cancel" in err_lower or "abort" in err_lower:
                        raise ScanAborted(f"Scan cancelled by device: {scan_delegate.error}")
                    raise ScanError(f"Scan failed: {scan_delegate.error}")

                if scan_delegate.scanned_url is None:
                    raise ScanError("Scan completed but no output file was produced")

                file_path = scan_delegate.scanned_url.path()
                png_data = Path(file_path).read_bytes()

            width, height = _read_png_dimensions(png_data)

            _check_progress(options.progress, 100)

            return ScannedDocument(
                data=png_data,
                width=width,
                height=height,
                dpi=options.dpi,
                color_mode=options.color_mode,
                scanner=scanner,
            )
        except (NoScannerFoundError, ScanError, ScanAborted):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
        finally:
            browser.stop()
