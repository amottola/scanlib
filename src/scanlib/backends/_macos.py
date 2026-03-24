from __future__ import annotations

import math
import threading
import time
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
    DISCOVERY_TIMEOUT,
    MM_PER_INCH,
    ColorMode,
    normalize_resolutions,
    FeederEmptyError,
    ScanArea,
    ScanAborted,
    ScanError,
    ScannedPage,
    Scanner,
    ScannerDefaults,
    ScanOptions,
    ScanSource,
    SourceInfo,
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
        self._done = threading.Event()
        self._removed = []
        return self

    def deviceBrowser_didAddDevice_moreComing_(self, browser, device, moreComing):
        if device.type() & ImageCaptureCore.ICDeviceTypeMaskScanner:
            self.scanners.append(device)
        if not moreComing:
            self._done.set()

    def deviceBrowser_didRemoveDevice_moreGoing_(self, browser, device, moreGoing):
        self._removed.append(device)


class _ScanDelegate(NSObject):
    """Delegate for ICScannerDevice — a fresh instance is created per phase.

    Open phase uses ``_open_event``; close phase uses ``_closed_event``;
    scan phase accumulates band data via ``scannerDevice:didScanToBandData:``
    and signals completion through ``_scan_done``.
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
        self.session_open = False
        self._aborted = False
        # Threading events for cross-thread signaling
        self._open_event = threading.Event()
        self._scan_done = threading.Event()
        self._closed_event = threading.Event()
        return self

    def _finish_page(self) -> None:
        self.completed_pages.append(
            (
                self._current_bands,
                self._current_width,
                self._current_height,
                self._current_bpc,
                self._current_nc,
                self._current_pdt,
            )
        )
        self._current_bands = []

    def device_didOpenSessionWithError_(self, device, error):
        if error:
            self.error = str(error)
        else:
            self.session_open = True
        self._open_event.set()

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
                try:
                    check_progress(self._progress, pct)
                except ScanAborted:
                    self._aborted = True
                    self._scan_done.set()
                    return
                self._last_pct = pct

    def scannerDevice_didCompleteScanWithError_(self, device, error):
        if error:
            self.error = str(error)
        self._scan_done.set()

    def device_didCloseSessionWithError_(self, device, error):
        self._closed_event.set()

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

    Returns (raw_pixels, width, height, color_mode) where
    *raw_pixels* contains no PNG filter-byte prefix.
    """
    from _scanlib_accel import strip_alpha, trim_rows

    if bpc == 1:
        pixel_row_bytes = (width + 7) // 8
    else:
        pixel_row_bytes = width * nc * (bpc // 8)

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
            full_buf[dst_off : dst_off + pixel_row_bytes] = raw[
                src_off : src_off + pixel_row_bytes
            ]

    # Map pixel data type to color type and extract raw pixels.
    # The scanner may return extra channels (e.g. 4-component RGBX for
    # RGB mode); strip them.
    if pdt == 0:  # BW — 1-bit grayscale
        mode = ColorMode.BW
        packed_row = (width + 7) // 8
        raw_pixels = trim_rows(bytes(full_buf), height, pixel_row_bytes, packed_row)
    elif pdt == 1:  # Gray — 8-bit grayscale
        mode = ColorMode.GRAY
        raw_pixels = trim_rows(bytes(full_buf), height, pixel_row_bytes, width)
    else:  # RGB (2) and others — 8-bit RGB
        mode = ColorMode.COLOR
        if nc > 3:
            # Strip extra channels (e.g. RGBX → RGB) via C extension
            raw_pixels = strip_alpha(bytes(full_buf), width, height, nc)
        else:
            rgb_row_bytes = width * 3
            raw_pixels = trim_rows(
                bytes(full_buf), height, pixel_row_bytes, rgb_row_bytes
            )

    return raw_pixels, width, height, mode


def _scan_area_from_fu(fu: object) -> ScanArea | None:
    """Read physical size from a functional unit, converting to 1/10 mm."""
    physical_size = fu.physicalSize()
    factor = _measurement_factor(fu.measurementUnit())
    if factor is None:
        return None
    return ScanArea(
        x=0,
        y=0,
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
        return normalize_resolutions(sorted(resolutions))
    except Exception:
        return []


def _read_color_modes_from_fu(fu: object) -> list[ColorMode]:
    """Infer supported color modes from a functional unit's bit depths."""
    try:
        supported = fu.supportedBitDepths()
        if supported is not None:
            modes: list[ColorMode] = []
            if supported.containsIndex_(8):
                modes.append(ColorMode.COLOR)
                modes.append(ColorMode.GRAY)
            if supported.containsIndex_(1):
                modes.append(ColorMode.BW)
            if modes:
                return modes
    except Exception:
        pass
    return [ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW]


def _read_defaults(
    device: object,
    sources: list[SourceInfo],
) -> ScannerDefaults | None:
    """Read default settings from the selected functional unit."""
    try:
        fu = device.selectedFunctionalUnit()
        if fu is None:
            return None

        try:
            dpi = int(fu.resolution())
        except Exception:
            dpi = 300

        first = sources[0] if sources else None
        src_modes = first.color_modes if first else []
        color_mode = (
            ColorMode.COLOR
            if ColorMode.COLOR in src_modes
            else (src_modes[0] if src_modes else ColorMode.COLOR)
        )

        return ScannerDefaults(
            dpi=dpi,
            color_mode=color_mode,
            source=first.type if first else None,
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


def _safe_str(dev, attr: str) -> str | None:
    """Read an optional string attribute from an ObjC device object."""
    try:
        val = getattr(dev, attr)()
        return str(val) if val else None
    except Exception:
        return None


class MacOSBackend:
    """macOS scanning backend using ImageCaptureCore.

    Thread-safe: a lock serialises access.  All work runs on a background
    worker thread; individual ImageCaptureCore calls are dispatched to
    the main thread via ``performSelectorOnMainThread:`` and return
    quickly.  Delegate callbacks arrive on the main thread and signal
    ``threading.Event`` objects that the worker waits on, so the main
    thread is never blocked for more than a few milliseconds at a time.
    """

    def __init__(self) -> None:
        self._devices: dict[str, object] = {}
        self._open_sessions: set[str] = set()
        self._browser = None
        self._browser_delegate = None
        self._lock = threading.Lock()

    # --- Main-thread dispatch helpers ---

    def _on_main(self, func, *args):
        """Execute *func* on the main thread, blocking until done.

        Individual ICC API calls are short (they just enqueue work),
        so this returns quickly and does not block the main thread
        for an extended period.
        """
        if threading.current_thread() is threading.main_thread():
            return func(*args)

        global _InvokerCls
        if _InvokerCls is None:
            _InvokerCls = _make_invoker_cls()

        invoker = _InvokerCls.alloc().init()
        invoker.func = func
        invoker.args = args
        invoker.performSelectorOnMainThread_withObject_waitUntilDone_(
            "invoke:",
            None,
            True,
        )
        if invoker.error is not None:
            raise invoker.error
        return invoker.result

    def _call(self, func, *args):
        """Run *func* on a worker thread; block the caller until done.

        If called from the main thread, the NSRunLoop is pumped while
        waiting so that ICC delegate callbacks (and GUI events) continue
        to be processed.  If called from a background thread, the caller
        simply blocks on a ``threading.Event``.
        """
        box: dict = {}
        done = threading.Event()

        def _worker():
            try:
                box["value"] = func(*args)
            except BaseException as exc:
                box["error"] = exc
            done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        if threading.current_thread() is threading.main_thread():
            run_loop = NSRunLoop.currentRunLoop()
            while not done.is_set():
                run_loop.runMode_beforeDate_(
                    NSDefaultRunLoopMode,
                    NSDate.dateWithTimeIntervalSinceNow_(0.05),
                )
        else:
            done.wait()

        if "error" in box:
            raise box["error"]
        return box.get("value")

    # --- Polling helpers ---

    def _wait_for(self, predicate, timeout: float, interval: float = 0.1):
        """Poll *predicate* (via ``_on_main``) until truthy or *timeout*."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self._on_main(predicate)
            if result:
                return result
            time.sleep(interval)
        return None

    # --- Browser management ---

    def _ensure_browser(self) -> _BrowserDelegate:
        """Start the device browser if not already running.  Must run on main thread."""
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

    # --- Public ScanBackend protocol ---

    def list_scanners(self, timeout: float = DISCOVERY_TIMEOUT) -> list[Scanner]:
        with self._lock:
            scanners = self._call(self._list_scanners_impl, timeout)
        for s in scanners:
            s._backend_impl = self
        return scanners

    def open_scanner(self, scanner: Scanner) -> None:
        with self._lock:
            return self._call(self._open_scanner_impl, scanner)

    def close_scanner(self, scanner: Scanner) -> None:
        with self._lock:
            return self._call(self._close_scanner_impl, scanner)

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]:
        with self._lock:
            pages = self._call(self._scan_pages_impl, scanner, options)
        yield from pages

    # --- Implementation (runs on worker thread) ---

    def _list_scanners_impl(self, timeout: float) -> list[Scanner]:
        delegate = self._on_main(self._ensure_browser)

        if not delegate._done.is_set():
            delegate._done.wait(timeout=timeout)

        def _build_list():
            for removed_dev in delegate._removed:
                self._devices.pop(removed_dev.name(), None)
            delegate._removed.clear()

            for dev in delegate.scanners:
                if dev.name() not in self._devices:
                    self._devices[dev.name()] = dev

            return [
                Scanner(
                    name=dev.name(),
                    vendor=_safe_str(dev, "manufacturer"),
                    model=None,
                    backend="imagecapture",
                    location=_safe_str(dev, "locationDescription"),
                )
                for dev in delegate.scanners
            ]

        return self._on_main(_build_list)

    def _open_scanner_impl(self, scanner: Scanner) -> None:
        device = self._devices.get(scanner.name)
        if device is None:
            raise ScanError(f"Scanner {scanner.name!r} not found")

        # Retry opening the session.  Network scanners may refuse if the
        # previous session close hasn't fully propagated yet.
        last_error = None
        scan_delegate = None
        for _attempt in range(3):

            def _request_open():
                d = _ScanDelegate.alloc().init()
                device.setDelegate_(d)
                device.requestOpenSession()
                return d

            scan_delegate = self._on_main(_request_open)

            if scan_delegate._open_event.wait(timeout=10.0):
                if scan_delegate.session_open:
                    last_error = None
                    break

            last_error = scan_delegate.error
            time.sleep(2.0)

        if last_error:
            raise ScanError(f"Failed to open device session: {last_error}")

        if scan_delegate is None or not scan_delegate.session_open:
            raise ScanError("Timed out waiting for device session to open")

        # Wait for functional unit types to become available (up to 5s).
        self._wait_for(device.availableFunctionalUnitTypes, timeout=5.0)

        self._open_sessions.add(scanner.name)

        # Read per-source capabilities from each functional unit.
        def _read_capabilities():
            source_types = _read_sources_from_device(device)
            remaining_sources = set(source_types)
            source_infos: dict[ScanSource, SourceInfo] = {}

            try:
                fu = device.selectedFunctionalUnit()
                if fu is not None:
                    fu_source = _ICC_SOURCE_MAP.get(fu.type())
                    if fu_source is not None and fu_source in remaining_sources:
                        source_infos[fu_source] = SourceInfo(
                            type=fu_source,
                            resolutions=_read_resolutions(device),
                            color_modes=_read_color_modes_from_fu(fu),
                            max_scan_area=_scan_area_from_fu(fu),
                        )
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

            return source_types, remaining_sources, source_infos, original_fu_type

        source_types, remaining, source_infos, original_fu_type = self._on_main(
            _read_capabilities
        )

        # Switch to remaining functional units and read their capabilities.
        for source in remaining:
            icc_type = _SCAN_SOURCE_TO_ICC.get(source)
            if icc_type is None:
                continue
            self._on_main(device.requestSelectFunctionalUnit_, icc_type)

            def _check_fu_selected(expected=icc_type):
                fu = device.selectedFunctionalUnit()
                return fu is not None and fu.type() == expected

            if not self._wait_for(_check_fu_selected, timeout=1.0):
                continue

            def _read_fu_caps(src=source):
                fu = device.selectedFunctionalUnit()
                if fu is None:
                    return None
                return SourceInfo(
                    type=src,
                    resolutions=_read_resolutions(device),
                    color_modes=_read_color_modes_from_fu(fu),
                    max_scan_area=_scan_area_from_fu(fu),
                )

            si = self._on_main(_read_fu_caps)
            if si is not None:
                source_infos[source] = si

        # Restore the original functional unit.
        if original_fu_type is not None:

            def _needs_restore():
                fu = device.selectedFunctionalUnit()
                return fu is None or fu.type() != original_fu_type

            if self._on_main(_needs_restore):
                self._on_main(device.requestSelectFunctionalUnit_, original_fu_type)

                def _check_restored():
                    fu = device.selectedFunctionalUnit()
                    return fu is not None and fu.type() == original_fu_type

                self._wait_for(_check_restored, timeout=1.0)

        # Preserve source_types ordering.
        scanner._sources = [source_infos[s] for s in source_types if s in source_infos]
        scanner._defaults = self._on_main(_read_defaults, device, scanner._sources)

    def _close_scanner_impl(self, scanner: Scanner) -> None:
        self._open_sessions.discard(scanner.name)
        device = self._devices.get(scanner.name)
        if device is None:
            return

        def _request_close():
            d = _ScanDelegate.alloc().init()
            device.setDelegate_(d)
            device.requestCloseSession()
            return d

        close_delegate = self._on_main(_request_close)
        close_delegate._closed_event.wait(timeout=5.0)

    def _scan_pages_impl(
        self, scanner: Scanner, options: ScanOptions
    ) -> list[ScannedPage]:
        device = self._devices.get(scanner.name)
        if device is None:
            raise ScanError("Scanner is not open")

        if scanner.name not in self._open_sessions:
            raise ScanError("Scanner is not open")

        # Create a fresh delegate for each scan.
        scan_delegate = self._on_main(
            lambda: _ScanDelegate.alloc().init()
        )
        self._on_main(device.setDelegate_, scan_delegate)

        try:
            self._on_main(device.setTransferMode_, _TRANSFER_MODE_MEMORY_BASED)

            # Select scan source if specified, and wait for the switch.
            if options.source is not None:
                icc_type = _SCAN_SOURCE_TO_ICC.get(options.source)
                if icc_type is not None:
                    unit_types = self._on_main(device.availableFunctionalUnitTypes)
                    if unit_types and icc_type in unit_types:
                        self._on_main(
                            device.requestSelectFunctionalUnit_, icc_type
                        )

                        def _check_source(expected=icc_type):
                            fu = device.selectedFunctionalUnit()
                            return fu is not None and fu.type() == expected

                        self._wait_for(_check_source, timeout=2.0)

            # Configure functional unit settings.
            def _configure_fu():
                fu = device.selectedFunctionalUnit()
                if fu is None:
                    return 0
                fu.setResolution_(options.dpi)
                pixel_type = _COLOR_MODE_TO_PIXEL_DATA_TYPE.get(options.color_mode)
                if pixel_type is not None:
                    fu.setPixelDataType_(pixel_type)

                # Pick bit depth: BW wants 1-bit, everything else 8-bit.
                preferred_bpc = 1 if options.color_mode == ColorMode.BW else 8
                supported_depths = fu.supportedBitDepths()
                if supported_depths and supported_depths.containsIndex_(preferred_bpc):
                    fu.setBitDepth_(preferred_bpc)
                elif supported_depths and supported_depths.count() > 0:
                    idx = supported_depths.indexGreaterThanOrEqualToIndex_(
                        preferred_bpc
                    )
                    if idx == 2**63 - 1:  # NSNotFound
                        idx = supported_depths.firstIndex()
                    fu.setBitDepth_(idx)
                else:
                    fu.setBitDepth_(8)

                if options.scan_area is not None:
                    factor = _measurement_factor(fu.measurementUnit())
                    if factor is None:
                        factor = MM_PER_INCH * 10
                    x_val = options.scan_area.x / factor
                    y_val = options.scan_area.y / factor
                    w_val = options.scan_area.width / factor
                    h_val = options.scan_area.height / factor
                    fu.setScanArea_(((x_val, y_val), (w_val, h_val)))
                else:
                    phys = fu.physicalSize()
                    fu.setScanArea_(((0, 0), (phys.width, phys.height)))

                # Compute expected pixel height for progress reporting.
                scan_area = fu.scanArea()
                unit_factor = _measurement_factor(fu.measurementUnit())
                if unit_factor is None:
                    unit_factor = MM_PER_INCH * 10
                area_mm = scan_area.size.height * unit_factor
                return int(area_mm / (MM_PER_INCH * 10) * options.dpi)

            expected_height = self._on_main(_configure_fu)

            check_progress(options.progress, 0)

            # Brief pause so the device can process config changes.
            time.sleep(0.5)

            is_feeder = options.source == ScanSource.FEEDER
            all_pages: list[ScannedPage] = []

            while True:
                scan_delegate._scan_done.clear()
                scan_delegate.error = None
                scan_delegate.completed_pages = []
                scan_delegate._current_bands = []
                scan_delegate._rows_received = 0
                scan_delegate._last_pct = 0
                scan_delegate._aborted = False
                scan_delegate._expected_height = expected_height
                scan_delegate._progress = options.progress

                check_progress(options.progress, -1)

                self._on_main(device.requestScan)
                scan_delegate._scan_done.wait(timeout=120.0)

                if scan_delegate._aborted:
                    self._on_main(device.cancelScan)
                    raise ScanAborted("Scan aborted by user")

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
                    if is_feeder:
                        raise FeederEmptyError("No documents in feeder")
                    raise ScanError("Scan completed but no image data was received")

                for bands, w, h, bpc, nc, pdt in scan_delegate.completed_pages:
                    raw, width, height, mode = _assemble_image(
                        bands, w, h, bpc, nc, pdt
                    )
                    all_pages.append(
                        ScannedPage(
                            data=raw,
                            width=width,
                            height=height,
                            color_mode=mode,
                        )
                    )

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
