from __future__ import annotations

import math
import threading
from collections.abc import Iterator

import ImageCaptureCore
import objc
from Foundation import (
    NSDate,
    NSDefaultRunLoopMode,
    NSObject,
    NSRunLoop,
)

from .._types import (
    MM_PER_INCH,
    ColorMode,
    PageSize,
    ScanAborted,
    ScanError,
    ScannedPage,
    Scanner,
    ScannerDefaults,
    ScanOptions,
    ScanSource,
    check_progress,
)

_ICC_SOURCE_MAP = {
    ImageCaptureCore.ICScannerFunctionalUnitTypeFlatbed: ScanSource.FLATBED,
    ImageCaptureCore.ICScannerFunctionalUnitTypeDocumentFeeder: ScanSource.FEEDER,
}

_SCAN_SOURCE_TO_ICC = {v: k for k, v in _ICC_SOURCE_MAP.items()}

# ICScannerPixelDataType: 0=BW, 1=Gray, 2=RGB
_COLOR_MODE_TO_PIXEL_DATA_TYPE = {
    ColorMode.BW: 0,
    ColorMode.GRAY: 1,
    ColorMode.COLOR: 2,
}

# ICScannerTransferMode
_TRANSFER_MODE_MEMORY_BASED = 1


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


class _BrowserDelegate(NSObject):
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


class _ScanDelegate(NSObject):
    """Delegate for ICScannerDevice — a fresh instance is created per phase.

    Open phase uses ``_session_open``; close phase uses ``_session_closed``;
    scan phase accumulates band data via ``scannerDevice:didScanToBandData:``
    and signals completion through ``_done``.
    """

    def init(self):
        self = objc.super(_ScanDelegate, self).init()
        if self is None:
            return None
        self.completed_pages: list[tuple] = []
        self._current_bands: list[tuple] = []
        self._current_width = 0
        self._current_height = 0
        self._current_bpc = 0
        self._current_nc = 0
        self._current_pdt = 0
        self._rows_received = 0
        self._expected_height = 0
        self._progress = None
        self._last_pct = 0
        self.error = None
        self._done = False
        self._session_open = False
        self._session_closed = False
        return self

    def _finish_page(self) -> None:
        self.completed_pages.append((
            self._current_bands,
            self._current_width,
            self._current_height,
            self._current_bpc,
            self._current_nc,
            self._current_pdt,
        ))
        self._current_bands = []

    def device_didOpenSessionWithError_(self, device, error):
        if error:
            self.error = str(error)
            self._done = True
        else:
            self._session_open = True

    def scannerDevice_didScanToBandData_(self, device, data):
        start_row = data.dataStartRow()
        if start_row == 0 and self._current_bands:
            self._finish_page()
            self._rows_received = 0
            self._last_pct = 0

        raw = bytes(data.dataBuffer())
        self._current_bands.append(
            (start_row, data.dataNumRows(), data.bytesPerRow(), raw)
        )
        self._rows_received += data.dataNumRows()
        self._current_width = data.fullImageWidth()
        self._current_height = data.fullImageHeight()
        self._current_bpc = data.bitsPerComponent()
        self._current_nc = data.numComponents()
        self._current_pdt = data.pixelDataType()

        # Report progress immediately as each band arrives.
        if self._expected_height > 0 and self._progress is not None:
            pct = min(self._rows_received * 99 // self._expected_height, 99)
            if pct > self._last_pct:
                check_progress(self._progress, pct)
                self._last_pct = pct

    def scannerDevice_didCompleteScanWithError_(self, device, error):
        if error:
            self.error = str(error)
        self._done = True

    def device_didCloseSessionWithError_(self, device, error):
        self._session_closed = True

    def device_didReceiveStatusInformation_(self, device, info):
        pass

    def didRemoveDevice_(self, device):
        pass


def _assemble_image(
    bands: list[tuple[int, int, int, bytes]],
    width: int,
    height: int,
    bpc: int,
    nc: int,
    pdt: int,
) -> tuple[bytes, int, int, int, int]:
    """Assemble band data into raw pixel bytes.

    Returns (raw_pixels, width, height, color_type, bit_depth) where
    *raw_pixels* contains no PNG filter-byte prefix.
    """
    from _scanlib_accel import strip_alpha, trim_rows

    pixel_row_bytes = width * nc * max(bpc // 8, 1)

    # Allocate full image buffer
    full_buf = bytearray(height * pixel_row_bytes)

    # Sort bands by start row and copy into buffer
    for start_row, num_rows, bytes_per_row, raw in sorted(bands):
        for r in range(num_rows):
            row_idx = start_row + r
            if row_idx >= height:
                break
            src_off = r * bytes_per_row
            dst_off = row_idx * pixel_row_bytes
            full_buf[dst_off:dst_off + pixel_row_bytes] = (
                raw[src_off:src_off + pixel_row_bytes]
            )

    # Map pixel data type to color type and extract raw pixels.
    # The scanner may return extra channels (e.g. 4-component RGBX for
    # RGB mode); strip them.
    if pdt == 0:  # BW — 1-bit grayscale
        color_type = 0
        bit_depth = 1
        packed_row = (width + 7) // 8
        raw_pixels = trim_rows(bytes(full_buf), height, pixel_row_bytes, packed_row)
    elif pdt == 1:  # Gray — 8-bit grayscale
        color_type = 0
        bit_depth = 8
        raw_pixels = trim_rows(bytes(full_buf), height, pixel_row_bytes, width)
    else:  # RGB (2) and others — 8-bit RGB
        color_type = 2
        bit_depth = 8
        if nc > 3:
            # Strip extra channels (e.g. RGBX → RGB) via C extension
            raw_pixels = strip_alpha(bytes(full_buf), width, height, nc)
        else:
            rgb_row_bytes = width * 3
            raw_pixels = trim_rows(bytes(full_buf), height, pixel_row_bytes, rgb_row_bytes)

    return raw_pixels, width, height, color_type, bit_depth


def _run_until(
    done_flag,
    timeout: float,
) -> None:
    """Spin the current NSRunLoop until *done_flag._done* is True or *timeout* elapses."""
    run_loop = NSRunLoop.currentRunLoop()
    deadline = NSDate.dateWithTimeIntervalSinceNow_(timeout)
    # Report indeterminate progress once before waiting for band data.
    progress = getattr(done_flag, "_progress", None)
    if progress is not None:
        check_progress(progress, -1)
    while not done_flag._done:
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


def _make_invoker_cls() -> type:
    """Create the ObjC helper class for main-thread dispatch."""

    class _Invoker(NSObject):
        def init(self):
            self = objc.super(_Invoker, self).init()
            if self is None:
                return None
            self.func = None
            self.args = ()
            self.result = None
            self.error = None
            return self

        def invoke_(self, _sender: object) -> None:
            try:
                self.result = self.func(*self.args)
            except BaseException as exc:
                self.error = exc

    return _Invoker


_InvokerCls: type | None = None


class MacOSBackend:
    """macOS scanning backend using ImageCaptureCore.

    Thread-safe: a lock serialises access and calls from background
    threads are forwarded to the main thread via
    ``performSelectorOnMainThread:withObject:waitUntilDone:``.
    """

    def __init__(self) -> None:
        self._devices: dict[str, object] = {}
        self._open_sessions: set[str] = set()
        self._browser = None
        self._browser_delegate = None
        self._lock = threading.Lock()

    def _ensure_browser(self) -> _BrowserDelegate:
        """Start the device browser if not already running."""
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

    def _call(self, func, *args):
        """Execute *func* on the main thread, blocking until done."""
        if threading.current_thread() is threading.main_thread():
            return func(*args)

        global _InvokerCls
        if _InvokerCls is None:
            _InvokerCls = _make_invoker_cls()

        invoker = _InvokerCls.alloc().init()
        invoker.func = func
        invoker.args = args
        invoker.performSelectorOnMainThread_withObject_waitUntilDone_(
            "invoke:", None, True,
        )
        if invoker.error is not None:
            raise invoker.error
        return invoker.result

    def list_scanners(self) -> list[Scanner]:
        with self._lock:
            scanners = self._call(self._list_scanners_impl)
        for s in scanners:
            s._backend_impl = self
        return scanners

    def open_scanner(self, scanner: Scanner) -> None:
        with self._lock:
            return self._call(self._open_scanner_impl, scanner)

    def close_scanner(self, scanner: Scanner) -> None:
        with self._lock:
            return self._call(self._close_scanner_impl, scanner)

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> Iterator[ScannedPage]:
        with self._lock:
            pages = self._call(self._scan_pages_impl, scanner, options)
        yield from pages

    def _list_scanners_impl(self) -> list[Scanner]:
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
            )
            for dev in delegate.scanners
        ]

    def _open_scanner_impl(self, scanner: Scanner) -> None:
        device = self._devices.get(scanner.name)
        if device is None:
            raise ScanError(f"Scanner {scanner.name!r} not found")

        run_loop = NSRunLoop.currentRunLoop()

        # Retry opening the session.  Network scanners may refuse if the
        # previous session close hasn't fully propagated yet.
        last_error = None
        for _attempt in range(3):
            scan_delegate = _ScanDelegate.alloc().init()
            device.setDelegate_(scan_delegate)
            device.requestOpenSession()

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

            if scan_delegate._session_open:
                last_error = None
                break

            last_error = scan_delegate.error
            # Spin the run loop briefly before retrying
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(2.0)
            )

        if last_error:
            raise ScanError(
                f"Failed to open device session: {last_error}"
            )

        if not scan_delegate._session_open:
            raise ScanError("Timed out waiting for device session to open")

        # Wait for functional unit types to become available (up to 5s).
        func_deadline = NSDate.dateWithTimeIntervalSinceNow_(5.0)
        while not device.availableFunctionalUnitTypes():
            if func_deadline.timeIntervalSinceNow() <= 0:
                break
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.1),
            )

        self._open_sessions.add(scanner.name)
        scanner._sources = _read_sources_from_device(device)

        # Read maximum scan area per functional unit / source.
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

        original_fu_type = None
        try:
            fu = device.selectedFunctionalUnit()
            if fu is not None:
                original_fu_type = fu.type()
        except Exception:
            pass

        for source in remaining_sources:
            try:
                icc_type = _SCAN_SOURCE_TO_ICC.get(source)
                if icc_type is None:
                    continue
                device.requestSelectFunctionalUnit_(icc_type)
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

        # Restore the original functional unit.
        if original_fu_type is not None:
            try:
                fu = device.selectedFunctionalUnit()
                if fu is None or fu.type() != original_fu_type:
                    device.requestSelectFunctionalUnit_(original_fu_type)
                    deadline = NSDate.dateWithTimeIntervalSinceNow_(1.0)
                    while True:
                        fu = device.selectedFunctionalUnit()
                        if fu is not None and fu.type() == original_fu_type:
                            break
                        if deadline.timeIntervalSinceNow() <= 0:
                            break
                        run_loop.runMode_beforeDate_(
                            NSDefaultRunLoopMode,
                            NSDate.dateWithTimeIntervalSinceNow_(0.1),
                        )
            except Exception:
                pass

        scanner._resolutions = _read_resolutions(device)
        scanner._color_modes = [ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW]
        scanner._defaults = _read_defaults(device, scanner._sources)

    def _close_scanner_impl(self, scanner: Scanner) -> None:
        self._open_sessions.discard(scanner.name)
        device = self._devices.get(scanner.name)
        if device is not None:
            close_delegate = _ScanDelegate.alloc().init()
            device.setDelegate_(close_delegate)
            device.requestCloseSession()
            run_loop = NSRunLoop.currentRunLoop()
            deadline = NSDate.dateWithTimeIntervalSinceNow_(5.0)
            while not close_delegate._session_closed:
                if deadline.timeIntervalSinceNow() <= 0:
                    break
                run_loop.runMode_beforeDate_(
                    NSDefaultRunLoopMode,
                    NSDate.dateWithTimeIntervalSinceNow_(0.1),
                )

    def _scan_pages_impl(self, scanner: Scanner, options: ScanOptions) -> list[ScannedPage]:
        device = self._devices.get(scanner.name)
        if device is None:
            raise ScanError("Scanner is not open")

        if scanner.name not in self._open_sessions:
            raise ScanError("Scanner is not open")

        # Create a fresh delegate for each scan.
        scan_delegate = _ScanDelegate.alloc().init()
        device.setDelegate_(scan_delegate)

        try:
            device.setTransferMode_(_TRANSFER_MODE_MEMORY_BASED)

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

                # Pick bit depth: BW wants 1-bit, everything else 8-bit.
                # Query supported depths and fall back to 8 if the
                # preferred depth isn't available.
                preferred_bpc = 1 if options.color_mode == ColorMode.BW else 8
                supported_depths = fu.supportedBitDepths()
                if supported_depths and supported_depths.containsIndex_(preferred_bpc):
                    fu.setBitDepth_(preferred_bpc)
                elif supported_depths and supported_depths.count() > 0:
                    # Use the smallest supported depth >= preferred, or
                    # just the smallest available.
                    idx = supported_depths.indexGreaterThanOrEqualToIndex_(preferred_bpc)
                    if idx == 2**63 - 1:  # NSNotFound
                        idx = supported_depths.firstIndex()
                    fu.setBitDepth_(idx)
                else:
                    fu.setBitDepth_(8)

                if options.page_size is not None:
                    factor = _measurement_factor(fu.measurementUnit())
                    if factor is None:
                        factor = MM_PER_INCH * 10
                    w_val = options.page_size.width / factor
                    h_val = options.page_size.height / factor
                    fu.setScanArea_(((0, 0), (w_val, h_val)))
                else:
                    phys = fu.physicalSize()
                    fu.setScanArea_(((0, 0), (phys.width, phys.height)))

            # Pre-compute expected pixel height for progress reporting.
            expected_height = 0
            if fu is not None:
                scan_area = fu.scanArea()
                unit_factor = _measurement_factor(fu.measurementUnit())
                if unit_factor is None:
                    unit_factor = MM_PER_INCH * 10
                area_mm = scan_area.size.height * unit_factor
                expected_height = int(area_mm / (MM_PER_INCH * 10) * options.dpi)

            check_progress(options.progress, 0)

            # Spin the run loop briefly so the device can process the
            # configuration changes before we issue the scan request.
            run_loop = NSRunLoop.currentRunLoop()
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(1.0)
            )

            is_feeder = options.source == ScanSource.FEEDER
            all_pages: list[ScannedPage] = []

            while True:
                scan_delegate._done = False
                scan_delegate.error = None
                scan_delegate.completed_pages = []
                scan_delegate._current_bands = []
                scan_delegate._rows_received = 0
                scan_delegate._last_pct = 0
                scan_delegate._expected_height = expected_height
                scan_delegate._progress = options.progress

                device.requestScan()
                try:
                    _run_until(scan_delegate, timeout=120.0)
                except ScanAborted:
                    device.cancelScan()
                    raise

                if scan_delegate.error:
                    err_lower = scan_delegate.error.lower()
                    if "cancel" in err_lower or "abort" in err_lower:
                        raise ScanAborted(
                            f"Scan cancelled by device: {scan_delegate.error}"
                        )
                    raise ScanError(f"Scan failed: {scan_delegate.error}")

                # Flush the last (or only) page
                if scan_delegate._current_bands:
                    scan_delegate._finish_page()

                if not scan_delegate.completed_pages:
                    raise ScanError(
                        "Scan completed but no image data was received"
                    )

                for bands, w, h, bpc, nc, pdt in scan_delegate.completed_pages:
                    raw, width, height, color_type, bit_depth = (
                        _assemble_image(bands, w, h, bpc, nc, pdt)
                    )
                    all_pages.append(ScannedPage(
                        data=raw, width=width, height=height,
                        color_type=color_type, bit_depth=bit_depth,
                    ))

                if is_feeder:
                    break

                if options.next_page is not None and options.next_page(len(all_pages)):
                    continue
                break

            check_progress(options.progress, 100)
            return all_pages
        except (ScanError, ScanAborted):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
