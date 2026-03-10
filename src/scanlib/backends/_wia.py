from __future__ import annotations

import math
import queue
import threading
from collections.abc import Iterator

import comtypes
import comtypes.client

from _scanlib_accel import bmp_to_raw as _bmp_to_raw

from .._types import (
    DISCOVERY_TIMEOUT,
    ColorMode,
    FeederEmptyError,
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

# --- WIA constants ---

# Device-level property IDs
_WIA_DIP_DEV_NAME = 7
_WIA_DIP_VEND_DESC = 3

# Document handling
_WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = 3086
_WIA_DPS_DOCUMENT_HANDLING_SELECT = 3088
_WIA_DPS_MAX_HORIZONTAL_SIZE = 3074  # thousandths of inch
_WIA_DPS_MAX_VERTICAL_SIZE = 3075    # thousandths of inch

# Item-level property IDs
_WIA_IPS_XRES = 6147
_WIA_IPS_YRES = 6148
_WIA_IPS_XPOS = 6149
_WIA_IPS_YPOS = 6150
_WIA_IPS_XEXTENT = 6151
_WIA_IPS_YEXTENT = 6152
_WIA_IPA_DATATYPE = 4103
_WIA_IPS_PAGES = 3096

# DataType values
_WIA_DATA_BW = 0
_WIA_DATA_GRAY = 2
_WIA_DATA_COLOR = 3

# DocHandlingCaps flags
_FLAT = 0x001
_FEED = 0x002

# DocHandlingSelect values
_FLATBED = 1
_FEEDER = 2

# Property SubType constants
_WIA_PROP_RANGE = 1
_WIA_PROP_LIST = 2

# BMP format GUID for Transfer()
_WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"

# HRESULT error codes (as signed int32)
_WIA_ERROR_PAPER_EMPTY = -2145320957  # 0x80210003

# Mappings
_WIA_DATATYPE_TO_COLOR = {
    _WIA_DATA_BW: ColorMode.BW,
    _WIA_DATA_GRAY: ColorMode.GRAY,
    _WIA_DATA_COLOR: ColorMode.COLOR,
}

_COLOR_TO_WIA_DATATYPE = {v: k for k, v in _WIA_DATATYPE_TO_COLOR.items()}

_MM10_PER_THOUSANDTH_INCH = 0.254  # 25.4 mm/inch / 100


# --- Helpers ---


def _get_property(properties: object, prop_id: int) -> object | None:
    """Find a WIA property by PropertyID (1-based iteration)."""
    for i in range(1, properties.Count + 1):
        prop = properties.Item(i)
        if prop.PropertyID == prop_id:
            return prop
    return None


def _get_property_value(properties: object, prop_id: int, default: object = None) -> object:
    """Get the value of a WIA property by ID."""
    prop = _get_property(properties, prop_id)
    if prop is not None:
        return prop.Value
    return default


def _read_wia_resolutions(item: object) -> list[int]:
    """Read supported DPI values from a WIA item."""
    prop = _get_property(item.Properties, _WIA_IPS_XRES)
    if prop is None:
        return []
    try:
        if prop.SubType == _WIA_PROP_RANGE:
            lo, hi, step = prop.SubTypeMin, prop.SubTypeMax, prop.SubTypeStep
            step = max(1, step)
            if (hi - lo) // step <= 1000:
                return list(range(lo, hi + 1, step))
            # Too many values; sample a reasonable subset
            return list(range(lo, hi + 1, (hi - lo) // 20))
        if prop.SubType == _WIA_PROP_LIST:
            return sorted(int(v) for v in prop.SubTypeValues)
    except Exception:
        pass
    try:
        return [int(prop.Value)]
    except Exception:
        return []


def _read_wia_color_modes(item: object) -> list[ColorMode]:
    """Read supported color modes from a WIA item."""
    prop = _get_property(item.Properties, _WIA_IPA_DATATYPE)
    if prop is None:
        return []
    try:
        if prop.SubType == _WIA_PROP_LIST:
            modes: list[ColorMode] = []
            for v in prop.SubTypeValues:
                mapped = _WIA_DATATYPE_TO_COLOR.get(int(v))
                if mapped is not None and mapped not in modes:
                    modes.append(mapped)
            return modes
    except Exception:
        pass
    try:
        mapped = _WIA_DATATYPE_TO_COLOR.get(int(prop.Value))
        return [mapped] if mapped else []
    except Exception:
        return []


def _read_wia_sources(device: object) -> list[ScanSource]:
    """Determine available scan sources from device capabilities."""
    caps = _get_property_value(
        device.Properties, _WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES, 0
    )
    sources: list[ScanSource] = []
    if caps & _FLAT:
        sources.append(ScanSource.FLATBED)
    if caps & _FEED:
        sources.append(ScanSource.FEEDER)
    if not sources:
        sources.append(ScanSource.FLATBED)
    return sources


def _read_wia_max_page_size(device: object) -> PageSize | None:
    """Read max page size (thousandths of inch → 1/10mm)."""
    max_h = _get_property_value(device.Properties, _WIA_DPS_MAX_HORIZONTAL_SIZE)
    max_v = _get_property_value(device.Properties, _WIA_DPS_MAX_VERTICAL_SIZE)
    if max_h is not None and max_v is not None:
        width = math.ceil(int(max_h) * _MM10_PER_THOUSANDTH_INCH)
        height = math.ceil(int(max_v) * _MM10_PER_THOUSANDTH_INCH)
        return PageSize(width=width, height=height)
    return None


def _read_wia_defaults(
    item: object, sources: list[ScanSource]
) -> ScannerDefaults | None:
    """Read default settings from WIA item properties."""
    try:
        dpi = int(_get_property_value(item.Properties, _WIA_IPS_XRES, 300))
        dt = _get_property_value(item.Properties, _WIA_IPA_DATATYPE, _WIA_DATA_COLOR)
        color_mode = _WIA_DATATYPE_TO_COLOR.get(int(dt), ColorMode.COLOR)
        source = sources[0] if sources else None
        return ScannerDefaults(dpi=dpi, color_mode=color_mode, source=source)
    except Exception:
        return None


# --- Backend ---


class WiaBackend:
    """Windows scanning backend using WIA (via comtypes).

    Thread-safe: all operations execute on a dedicated worker thread
    that owns the COM apartment.
    """

    def __init__(self) -> None:
        self._handles: dict[str, object] = {}
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._work_event = None  # Win32 event handle, created in _run
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        comtypes.CoInitialize()  # STA for apartment-threaded COM objects
        try:
            import ctypes
            import ctypes.wintypes as wt

            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
        except (ImportError, AttributeError):
            # Non-Windows (test environment with mocked comtypes)
            self._ready.set()
            self._run_simple()
            return

        # Auto-reset event signaled when work items are enqueued
        kernel32.CreateEventW.restype = ctypes.c_void_p
        work_event = kernel32.CreateEventW(None, False, False, None)
        self._work_event = work_event
        self._ready.set()

        handles = (wt.HANDLE * 1)(work_event)
        msg = wt.MSG()

        while True:
            # Wait for work signal or Win32 messages
            user32.MsgWaitForMultipleObjects(
                1, handles, False, 0xFFFFFFFF, 0x04FF  # INFINITE, QS_ALLINPUT
            )
            # Drain pending messages (required for COM STA marshaling)
            while user32.PeekMessageW(
                ctypes.byref(msg), None, 0, 0, 0x0001  # PM_REMOVE
            ):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            # Process all queued work items
            while True:
                try:
                    func, args, event, box = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    box["value"] = func(*args)
                except BaseException as exc:
                    box["error"] = exc
                event.set()

    def _run_simple(self) -> None:
        """Simple event loop without Win32 message pump (test fallback)."""
        while True:
            func, args, event, box = self._queue.get()
            try:
                box["value"] = func(*args)
            except BaseException as exc:
                box["error"] = exc
            event.set()

    def _signal_work(self) -> None:
        """Signal the worker thread that work is available."""
        evt = self._work_event
        if evt is not None:
            import ctypes

            ctypes.windll.kernel32.SetEvent(evt)

    def _dispatch(self, func, *args):
        event = threading.Event()
        box: dict = {}
        self._queue.put((func, args, event, box))
        self._signal_work()
        event.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # --- Public ScanBackend protocol ---

    def list_scanners(self, timeout: float = DISCOVERY_TIMEOUT) -> list[Scanner]:
        event = threading.Event()
        box: dict = {}
        self._queue.put((self._list_scanners_impl, (), event, box))
        self._signal_work()
        if not event.wait(timeout):
            return []
        if "error" in box:
            raise box["error"]
        scanners = box.get("value", [])
        for s in scanners:
            s._backend_impl = self
        return scanners

    def open_scanner(self, scanner: Scanner) -> None:
        return self._dispatch(self._open_scanner_impl, scanner)

    def close_scanner(self, scanner: Scanner) -> None:
        return self._dispatch(self._close_scanner_impl, scanner)

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]:
        pages = self._dispatch(self._scan_pages_impl, scanner, options)
        yield from pages

    # --- Implementation (runs on worker thread) ---

    def _create_device_manager(self) -> object:
        return comtypes.client.CreateObject("WIA.DeviceManager")

    def _list_scanners_impl(self) -> list[Scanner]:
        dm = self._create_device_manager()
        scanners: list[Scanner] = []
        for i in range(1, dm.DeviceInfos.Count + 1):
            di = dm.DeviceInfos.Item(i)
            if di.Type != 1:  # 1 = scanner
                continue
            name = _get_property_value(di.Properties, _WIA_DIP_DEV_NAME, f"Scanner {i}")
            vendor = _get_property_value(di.Properties, _WIA_DIP_VEND_DESC)
            scanners.append(
                Scanner(
                    name=str(name),
                    vendor=str(vendor) if vendor else None,
                    model=None,
                    backend="wia",
                )
            )
        return scanners

    def _open_scanner_impl(self, scanner: Scanner) -> None:
        try:
            dm = self._create_device_manager()
            device = None
            for i in range(1, dm.DeviceInfos.Count + 1):
                di = dm.DeviceInfos.Item(i)
                if di.Type != 1:
                    continue
                di_name = _get_property_value(di.Properties, _WIA_DIP_DEV_NAME, "")
                if str(di_name) == scanner.name:
                    device = di.Connect()
                    break
            if device is None:
                raise ScanError(f"Scanner {scanner.name!r} not found")
        except ScanError:
            raise
        except Exception as exc:
            raise ScanError(
                f"Failed to open scanner {scanner.name!r}: {exc}"
            ) from exc

        self._handles[scanner.name] = device

        # Query sources
        sources = _read_wia_sources(device)
        scanner._sources = sources

        # Query max page size
        ps = _read_wia_max_page_size(device)
        if ps is not None:
            for source in sources:
                scanner._max_page_sizes[source] = ps

        # Query item-level properties from first scan item
        try:
            item = device.Items(1)
        except Exception:
            item = None

        if item is not None:
            scanner._resolutions = _read_wia_resolutions(item)
            scanner._color_modes = _read_wia_color_modes(item)
            scanner._defaults = _read_wia_defaults(item, sources)
        else:
            scanner._resolutions = []
            scanner._color_modes = []
            scanner._defaults = None

    def _close_scanner_impl(self, scanner: Scanner) -> None:
        self._handles.pop(scanner.name, None)

    def _scan_pages_impl(
        self, scanner: Scanner, options: ScanOptions
    ) -> list[ScannedPage]:
        device = self._handles.get(scanner.name)
        if device is None:
            raise ScanError("Scanner is not open")

        try:
            # Select source
            if options.source == ScanSource.FEEDER:
                prop = _get_property(
                    device.Properties, _WIA_DPS_DOCUMENT_HANDLING_SELECT
                )
                if prop is not None:
                    prop.Value = _FEEDER
            elif options.source == ScanSource.FLATBED:
                prop = _get_property(
                    device.Properties, _WIA_DPS_DOCUMENT_HANDLING_SELECT
                )
                if prop is not None:
                    prop.Value = _FLATBED

            item = device.Items(1)

            # Set resolution
            xres_prop = _get_property(item.Properties, _WIA_IPS_XRES)
            yres_prop = _get_property(item.Properties, _WIA_IPS_YRES)
            if xres_prop is not None:
                xres_prop.Value = options.dpi
            if yres_prop is not None:
                yres_prop.Value = options.dpi

            # Set color mode
            dt_prop = _get_property(item.Properties, _WIA_IPA_DATATYPE)
            if dt_prop is not None:
                wia_dt = _COLOR_TO_WIA_DATATYPE.get(options.color_mode)
                if wia_dt is not None:
                    dt_prop.Value = wia_dt

            # Set scan area
            if options.page_size is not None:
                width_px = int(options.page_size.width / 10.0 / 25.4 * options.dpi)
                height_px = int(options.page_size.height / 10.0 / 25.4 * options.dpi)

                xpos = _get_property(item.Properties, _WIA_IPS_XPOS)
                ypos = _get_property(item.Properties, _WIA_IPS_YPOS)
                xext = _get_property(item.Properties, _WIA_IPS_XEXTENT)
                yext = _get_property(item.Properties, _WIA_IPS_YEXTENT)

                if xpos is not None:
                    xpos.Value = 0
                if ypos is not None:
                    ypos.Value = 0
                if xext is not None:
                    xext.Value = width_px
                if yext is not None:
                    yext.Value = height_px

            check_progress(options.progress, 0)

            is_feeder = options.source == ScanSource.FEEDER
            pages: list[ScannedPage] = []

            # For feeder: scan all available pages
            if is_feeder:
                pages_prop = _get_property(item.Properties, _WIA_IPS_PAGES)
                if pages_prop is not None:
                    pages_prop.Value = 0

            while True:
                try:
                    image_file = item.Transfer(_WIA_FORMAT_BMP)
                except Exception as exc:
                    hr = getattr(exc, "hresult", None)
                    msg = str(exc).lower()
                    if hr == _WIA_ERROR_PAPER_EMPTY or "paper" in msg or "empty" in msg:
                        if is_feeder and not pages:
                            raise FeederEmptyError(
                                "No documents in feeder"
                            ) from exc
                        break
                    if "cancel" in msg or "abort" in msg:
                        raise ScanAborted(
                            f"Scan cancelled by device: {exc}"
                        ) from exc
                    if is_feeder and pages:
                        break
                    raise ScanError(f"Scan failed: {exc}") from exc

                bmp_data = bytes(image_file.FileData.BinaryData)
                raw, w, h, ct, bd = _bmp_to_raw(bmp_data)
                pages.append(
                    ScannedPage(data=raw, width=w, height=h, color_type=ct, bit_depth=bd)
                )

                if is_feeder:
                    continue
                else:
                    if options.next_page is not None and options.next_page(len(pages)):
                        continue
                    break

            if not pages:
                raise ScanError("No pages were scanned")

            check_progress(options.progress, 100)
            return pages

        except (ScanAborted, ScanError, FeederEmptyError):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
