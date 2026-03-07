from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

import ImageCaptureCore
import objc
from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop, NSURL

from .._types import (
    ColorMode,
    PageSize,
    ScanAborted,
    ScanError,
    ScannedPage,
    Scanner,
    ScannerDefaults,
    ScanOptions,
    ScanSource,
)
from ._util import MM_PER_INCH, check_progress

_ICC_SOURCE_MAP = {
    ImageCaptureCore.ICScannerFunctionalUnitTypeFlatbed: ScanSource.FLATBED,
    ImageCaptureCore.ICScannerFunctionalUnitTypeDocumentFeeder: ScanSource.FEEDER,
}

_SCAN_SOURCE_TO_ICC = {v: k for k, v in _ICC_SOURCE_MAP.items()}

# ICScannerPixelDataType: 0=BW, 1=Gray, 2=RGB, 3=Palette, 4=CMY, 5=CMYK, 6=YUV, 7=YUVK, 8=CIEXYZ
_COLOR_MODE_TO_PIXEL_DATA_TYPE = {
    ColorMode.BW: 0,
    ColorMode.GRAY: 1,
    ColorMode.COLOR: 2,
}

# ICScannerTransferMode
_TRANSFER_MODE_FILE_BASED = 0
_TRANSFER_MODE_MEMORY_BASED = 1


def _read_png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width and height from PNG file bytes."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ScanError("Invalid PNG data")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _measurement_factor(unit: int) -> float | None:
    """Return factor to convert from measurement unit to 1/10 mm, or None if unknown.

    ICScannerMeasurementUnit: 0=inches, 1=centimeters, 2=picas, 3=points.
    """
    if unit == 0:  # inches
        return MM_PER_INCH * 10
    elif unit == 1:  # centimeters
        return 100.0
    elif unit == 2:  # picas (1/6 inch)
        return MM_PER_INCH * 10 / 6.0
    elif unit == 3:  # points (1/72 inch)
        return MM_PER_INCH * 10 / 72.0
    return None


class _BrowserDelegate(ImageCaptureCore.NSObject):
    """Delegate for ICDeviceBrowser to collect discovered scanners."""

    def init(self):
        self = objc.super(_BrowserDelegate, self).init()
        if self is None:
            return None
        self.scanners = []
        self._done = False
        self._removed = []
        return self

    def deviceBrowser_didAddDevice_moreComing_(self, browser, device, moreComing):
        if device.type() & ImageCaptureCore.ICDeviceTypeMaskScanner:
            self.scanners.append(device)
        if not moreComing:
            self._done = True

    def deviceBrowser_didRemoveDevice_moreGoing_(self, browser, device, moreGoing):
        self._removed.append(device)


class _ScanDelegate(ImageCaptureCore.NSObject):
    """Delegate for ICScannerDevice to handle the full scan lifecycle."""

    def init(self):
        self = objc.super(_ScanDelegate, self).init()
        if self is None:
            return None
        self.scanned_urls = []
        self.error = None
        self._done = False
        self._session_open = False
        return self

    def device_didOpenSessionWithError_(self, device, error):
        if error:
            self.error = str(error)
            self._done = True
        else:
            self._session_open = True

    def scannerDevice_didScanToURL_(self, device, url):
        self.scanned_urls.append(url)

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
    progress=None,
) -> None:
    """Spin the current NSRunLoop until *done_flag._done* is True or *timeout* elapses."""
    run_loop = NSRunLoop.currentRunLoop()
    deadline = NSDate.dateWithTimeIntervalSinceNow_(timeout)
    while not done_flag._done:
        check_progress(progress, -1)
        if NSDate.date().compare_(deadline) != -1:  # NSOrderedAscending = -1
            break
        run_loop.runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.1)
        )


def _page_size_from_fu(fu: object) -> PageSize | None:
    """Read physical size from a functional unit, converting to 1/10 mm."""
    physical_size = fu.physicalSize()
    factor = _measurement_factor(fu.measurementUnit())
    if factor is None:
        return None
    return PageSize(
        width=math.ceil(physical_size.width * factor),
        height=math.ceil(physical_size.height * factor),
    )


def _read_sources_from_device(device: object) -> list[ScanSource]:
    """Read sources from a device that already has an open session."""
    unit_types = device.availableFunctionalUnitTypes()
    sources = []
    if unit_types:
        for unit_type in unit_types:
            mapped = _ICC_SOURCE_MAP.get(unit_type)
            if mapped is not None:
                sources.append(mapped)
    return sources


def _read_resolutions(device: object) -> list[int]:
    """Read supported resolutions from the selected functional unit."""
    try:
        fu = device.selectedFunctionalUnit()
        if fu is None:
            return []
        supported = fu.supportedResolutions()
        if supported is None:
            return []
        resolutions: list[int] = []
        idx = supported.firstIndex()
        while idx != 2**63 - 1:  # NSNotFound
            resolutions.append(int(idx))
            idx = supported.indexGreaterThanIndex_(idx)
        return sorted(resolutions)
    except Exception:
        return []


def _read_defaults(device: object, sources: list[ScanSource]) -> ScannerDefaults | None:
    """Read default settings from the selected functional unit."""
    try:
        fu = device.selectedFunctionalUnit()
        if fu is None:
            return None

        try:
            dpi = int(fu.resolution())
        except Exception:
            dpi = 300

        source = sources[0] if sources else None

        return ScannerDefaults(
            dpi=dpi,
            color_mode=ColorMode.COLOR,
            source=source,
        )
    except Exception:
        return None


class MacOSBackend:
    """macOS scanning backend using ImageCaptureCore."""

    def __init__(self) -> None:
        self._devices: dict[str, object] = {}
        self._delegates: dict[str, object] = {}
        self._browser = None
        self._browser_delegate = None

    def _ensure_browser(self) -> _BrowserDelegate:
        """Start the device browser if not already running.

        The browser is kept alive for the lifetime of the backend so that
        subsequent calls to :meth:`list_scanners` can return instantly when
        devices have already been discovered.
        """
        if self._browser is not None:
            return self._browser_delegate

        delegate = _BrowserDelegate.alloc().init()
        browser = ImageCaptureCore.ICDeviceBrowser.alloc().init()
        browser.setDelegate_(delegate)
        browser.setBrowsedDeviceTypeMask_(
            ImageCaptureCore.ICDeviceTypeMaskScanner
            | ImageCaptureCore.ICDeviceLocationTypeMaskLocal
            | ImageCaptureCore.ICDeviceLocationTypeMaskRemote
        )
        browser.start()

        self._browser = browser
        self._browser_delegate = delegate
        return delegate

    def list_scanners(self) -> list[Scanner]:
        delegate = self._ensure_browser()

        if not delegate._done:
            _run_until(delegate, timeout=5.0)

        # Purge removed devices
        for removed_dev in delegate._removed:
            self._devices.pop(removed_dev.name(), None)
        delegate._removed.clear()

        # Build scanner list from current devices
        for dev in delegate.scanners:
            if dev.name() not in self._devices:
                self._devices[dev.name()] = dev

        return [
            Scanner(
                name=dev.name(),
                vendor=None,
                model=dev.name(),
                backend="imagecapture",
                _backend_impl=self,
            )
            for dev in delegate.scanners
        ]

    def open_scanner(self, scanner: Scanner) -> None:
        device = self._devices.get(scanner.name)
        if device is None:
            raise ScanError(f"Scanner {scanner.name!r} not found")

        scan_delegate = _ScanDelegate.alloc().init()
        device.setDelegate_(scan_delegate)
        device.requestOpenSession()

        run_loop = NSRunLoop.currentRunLoop()

        # Wait for session to open (up to 10s)
        open_deadline = NSDate.dateWithTimeIntervalSinceNow_(10.0)
        while not scan_delegate._session_open:
            if scan_delegate._done:  # error during open
                break
            if open_deadline.timeIntervalSinceNow() <= 0:
                break
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.1),
            )

        if scan_delegate.error:
            raise ScanError(
                f"Failed to open device session: {scan_delegate.error}"
            )

        if not scan_delegate._session_open:
            raise ScanError("Timed out waiting for device session to open")

        # Wait for functional unit types to become available (up to 5s).
        # These are populated asynchronously after the session opens.
        func_deadline = NSDate.dateWithTimeIntervalSinceNow_(5.0)
        while not device.availableFunctionalUnitTypes():
            if func_deadline.timeIntervalSinceNow() <= 0:
                break
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.1),
            )

        self._delegates[scanner.name] = scan_delegate
        scanner._sources = _read_sources_from_device(device)

        # Read maximum scan area per functional unit / source.
        # Start with the currently selected FU (available immediately),
        # then switch to other sources and spin the run loop to let the
        # async selection complete.
        remaining_sources = set(scanner._sources)
        try:
            fu = device.selectedFunctionalUnit()
            if fu is not None:
                ps = _page_size_from_fu(fu)
                if ps is not None:
                    fu_source = _ICC_SOURCE_MAP.get(fu.type())
                    if fu_source is not None and fu_source in remaining_sources:
                        scanner._max_page_sizes[fu_source] = ps
                        remaining_sources.discard(fu_source)
        except Exception:
            pass

        for source in remaining_sources:
            try:
                icc_type = _SCAN_SOURCE_TO_ICC.get(source)
                if icc_type is None:
                    continue
                device.requestSelectFunctionalUnit_(icc_type)
                # Spin run loop to let the async selection complete
                deadline = NSDate.dateWithTimeIntervalSinceNow_(1.0)
                while True:
                    fu = device.selectedFunctionalUnit()
                    if fu is not None and fu.type() == icc_type:
                        break
                    if deadline.timeIntervalSinceNow() <= 0:
                        break
                    run_loop.runMode_beforeDate_(
                        NSDefaultRunLoopMode,
                        NSDate.dateWithTimeIntervalSinceNow_(0.1),
                    )
                fu = device.selectedFunctionalUnit()
                if fu is not None and fu.type() == icc_type:
                    ps = _page_size_from_fu(fu)
                    if ps is not None:
                        scanner._max_page_sizes[source] = ps
            except Exception:
                pass

        scanner._resolutions = _read_resolutions(device)
        scanner._color_modes = [ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW]
        scanner._defaults = _read_defaults(device, scanner._sources)

    def close_scanner(self, scanner: Scanner) -> None:
        device = self._devices.get(scanner.name)
        self._delegates.pop(scanner.name, None)
        if device is not None:
            device.requestCloseSession()

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> list[ScannedPage]:
        device = self._devices.get(scanner.name)
        if device is None:
            raise ScanError("Scanner is not open")

        scan_delegate = self._delegates.get(scanner.name)
        if scan_delegate is None:
            raise ScanError("Scanner is not open")

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                downloads_url = NSURL.fileURLWithPath_(tmp_dir)
                device.setDownloadsDirectory_(downloads_url)
                device.setDocumentName_("scanlib_scan")
                device.setDocumentUTI_("public.png")
                device.setTransferMode_(_TRANSFER_MODE_FILE_BASED)

                # Select scan source if specified
                if options.source is not None:
                    icc_type = _SCAN_SOURCE_TO_ICC.get(options.source)
                    if icc_type is not None:
                        unit_types = device.availableFunctionalUnitTypes()
                        if unit_types and icc_type in unit_types:
                            device.requestSelectFunctionalUnit_(icc_type)

                # Configure functional unit settings
                fu = device.selectedFunctionalUnit()
                if fu is not None:
                    fu.setResolution_(options.dpi)
                    pixel_type = _COLOR_MODE_TO_PIXEL_DATA_TYPE.get(options.color_mode)
                    if pixel_type is not None:
                        fu.setPixelDataType_(pixel_type)

                    if options.page_size is not None:
                        factor = _measurement_factor(fu.measurementUnit())
                        if factor is None:
                            factor = MM_PER_INCH * 10  # fallback: assume inches
                        w_val = options.page_size.width / factor
                        h_val = options.page_size.height / factor
                        fu.setScanArea_(((0, 0), (w_val, h_val)))

                check_progress(options.progress, 0)

                # Reset delegate for scan phase
                scan_delegate._done = False
                scan_delegate.error = None
                scan_delegate.scanned_urls = []

                device.requestScan()
                try:
                    _run_until(scan_delegate, timeout=120.0, progress=options.progress)
                except ScanAborted:
                    device.cancelScan()
                    raise

                if scan_delegate.error:
                    err_lower = scan_delegate.error.lower()
                    if "cancel" in err_lower or "abort" in err_lower:
                        raise ScanAborted(f"Scan cancelled by device: {scan_delegate.error}")
                    raise ScanError(f"Scan failed: {scan_delegate.error}")

                # Collect scanned files — either from the delegate callback
                # or by scanning the downloads directory (workaround for
                # macOS Sequoia where didScanToURL: may not fire).
                file_paths: list[Path] = []
                for url in scan_delegate.scanned_urls:
                    file_paths.append(Path(url.path()))

                if not file_paths:
                    # Fallback: check the downloads directory for files
                    file_paths = sorted(Path(tmp_dir).iterdir())

                if not file_paths:
                    raise ScanError("Scan completed but no output files were produced")

                pages: list[ScannedPage] = []
                for path in file_paths:
                    png_data = path.read_bytes()
                    width, height = _read_png_dimensions(png_data)
                    pages.append(ScannedPage(
                        png_data=png_data, width=width, height=height
                    ))

            check_progress(options.progress, 100)
            return pages
        except (ScanError, ScanAborted):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
