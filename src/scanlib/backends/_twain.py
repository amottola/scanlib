from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import math
import queue
import struct
import threading
from collections.abc import Iterator

import twain

from .._types import (
    DISCOVERY_TIMEOUT,
    MM_PER_INCH,
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

_COLOR_MODE_MAP = {
    ColorMode.COLOR: "color",
    ColorMode.GRAY: "gray",
    ColorMode.BW: "bw",
}


def _bmp_to_raw(bmp_data: bytes) -> tuple[bytes, int, int, int, int]:
    """Convert BMP file bytes to raw pixel data.

    Returns ``(raw_data, width, height, color_type, bit_depth)``.
    """
    if bmp_data[:2] != b"BM":
        raise ScanError("Invalid BMP data")

    (data_offset,) = struct.unpack_from("<I", bmp_data, 10)
    (header_size,) = struct.unpack_from("<I", bmp_data, 14)

    if header_size >= 40:
        width, height = struct.unpack_from("<iI", bmp_data, 18)
        (bits_per_pixel,) = struct.unpack_from("<H", bmp_data, 28)
    else:
        raise ScanError(f"Unsupported BMP header size: {header_size}")

    bottom_up = height > 0
    height = abs(height)

    if bits_per_pixel == 24:
        color_type = 2  # RGB
        channels = 3
        bit_depth = 8
    elif bits_per_pixel == 32:
        color_type = 6  # RGBA
        channels = 4
        bit_depth = 8
    elif bits_per_pixel == 8:
        color_type = 0  # Grayscale
        channels = 1
        bit_depth = 8
    elif bits_per_pixel == 1:
        color_type = 0  # Grayscale 1-bit
        channels = 0  # special handling below
        bit_depth = 1
    else:
        raise ScanError(f"Unsupported BMP bit depth: {bits_per_pixel}")

    if bits_per_pixel == 1:
        # 1-bit BMP: rows are bit-packed, padded to 4 bytes
        # BMP palette entry 0 is usually white, entry 1 black (or vice versa)
        # Read palette to determine mapping
        palette_offset = 14 + header_size
        pal_0 = bmp_data[palette_offset]      # blue component of entry 0
        pal_1 = bmp_data[palette_offset + 4]  # blue component of entry 1
        # If palette entry 0 is brighter, BMP bit 0=white, 1=black → invert for PNG
        # In PNG grayscale 1-bit: 0=black, 1=white
        invert = pal_0 > pal_1

        bmp_row_size = ((width + 31) // 32) * 4  # BMP rows padded to 4 bytes
        png_row_bytes = (width + 7) // 8
        pixel_data = bmp_data[data_offset:]

        raw_rows = []
        for y in range(height):
            src_y = (height - 1 - y) if bottom_up else y
            row_start = src_y * bmp_row_size
            row = bytearray(pixel_data[row_start : row_start + png_row_bytes])
            if invert:
                for i in range(len(row)):
                    row[i] ^= 0xFF
            # Mask unused trailing bits in last byte
            remainder = width % 8
            if remainder:
                row[-1] &= (0xFF << (8 - remainder)) & 0xFF
            raw_rows.append(bytes(row))

        raw_data = b"".join(raw_rows)
    else:
        bmp_row_size = (width * channels + 3) & ~3
        pixel_data = bmp_data[data_offset:]

        raw_rows = []
        for y in range(height):
            if bottom_up:
                src_y = height - 1 - y
            else:
                src_y = y
            row_start = src_y * bmp_row_size
            row = pixel_data[row_start : row_start + width * channels]

            if channels >= 3:
                row_array = bytearray(row)
                for x in range(width):
                    i = x * channels
                    row_array[i], row_array[i + 2] = row_array[i + 2], row_array[i]
                row = bytes(row_array)

            raw_rows.append(row)

        raw_data = b"".join(raw_rows)

    return raw_data, width, height, color_type, bit_depth


def _create_hidden_window() -> int:
    """Create a minimal hidden Win32 window and return its HWND."""
    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long, wintypes.HWND, ctypes.c_uint,
        wintypes.WPARAM, wintypes.LPARAM,
    )

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", ctypes.c_uint),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    def default_wnd_proc(hwnd, msg, wparam, lparam):
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
    class_name = "ScanlibTwainWindow"

    # prevent garbage collection of the callback
    _create_hidden_window._wnd_proc_cb = WNDPROC(default_wnd_proc)

    wc = WNDCLASS()
    wc.lpfnWndProc = _create_hidden_window._wnd_proc_cb
    wc.hInstance = hinstance
    wc.lpszClassName = class_name

    ctypes.windll.user32.RegisterClassW(ctypes.byref(wc))
    hwnd = ctypes.windll.user32.CreateWindowExW(
        0, class_name, None, 0, 0, 0, 0, 0, None, None, hinstance, None
    )
    return hwnd


_TWAIN_PIXEL_TO_COLOR = {0: ColorMode.BW, 1: ColorMode.GRAY, 2: ColorMode.COLOR}


def _read_twain_resolutions(src: object) -> list[int]:
    """Read supported resolutions from TWAIN capabilities."""
    try:
        res_values = src.get_capability(twain.ICAP_XRESOLUTION)
        if isinstance(res_values, (list, tuple)):
            return sorted(int(v) for v in res_values)
        if res_values is not None:
            return [int(res_values)]
    except Exception:
        pass
    return []


def _read_twain_color_modes(src: object) -> list[ColorMode]:
    """Read supported color modes from TWAIN capabilities."""
    try:
        pt_values = src.get_capability(twain.ICAP_PIXELTYPE)
        if isinstance(pt_values, (list, tuple)):
            modes: list[ColorMode] = []
            for v in pt_values:
                mapped = _TWAIN_PIXEL_TO_COLOR.get(int(v))
                if mapped is not None and mapped not in modes:
                    modes.append(mapped)
            return modes
    except Exception:
        pass
    return []


def _read_twain_defaults(src: object, sources: list[ScanSource]) -> ScannerDefaults | None:
    """Read default settings from TWAIN source capabilities."""
    try:
        try:
            dpi = int(src.get_capability(twain.ICAP_XRESOLUTION))
        except Exception:
            dpi = 300

        try:
            pixel_type = int(src.get_capability(twain.ICAP_PIXELTYPE))
            color_mode = _TWAIN_PIXEL_TO_COLOR.get(pixel_type, ColorMode.COLOR)
        except Exception:
            color_mode = ColorMode.COLOR

        source = sources[0] if sources else None

        return ScannerDefaults(
            dpi=dpi,
            color_mode=color_mode,
            source=source,
        )
    except Exception:
        return None


class TwainBackend:
    """Windows scanning backend using pytwain (TWAIN).

    Thread-safe: all operations execute on a dedicated worker thread
    that owns the hidden window handle TWAIN requires.
    """

    def __init__(self) -> None:
        self._handles: dict[str, object] = {}
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        self._ready.set()
        while True:
            func, args, event, box = self._queue.get()
            try:
                box["value"] = func(*args)
            except BaseException as exc:
                box["error"] = exc
            event.set()

    def _dispatch(self, func, *args):
        event = threading.Event()
        box: dict = {}
        self._queue.put((func, args, event, box))
        event.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def list_scanners(self, timeout: float = DISCOVERY_TIMEOUT) -> list[Scanner]:
        event = threading.Event()
        box: dict = {}
        self._queue.put((self._list_scanners_impl, (), event, box))
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

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> Iterator[ScannedPage]:
        pages = self._dispatch(self._scan_pages_impl, scanner, options)
        yield from pages

    def _get_source_manager(self) -> twain.SourceManager:
        hwnd = _create_hidden_window()
        return twain.SourceManager(hwnd)

    def _list_scanners_impl(self) -> list[Scanner]:
        with self._get_source_manager() as sm:
            return [
                Scanner(
                    name=name,
                    vendor=None,
                    model=None,
                    backend="twain",
                )
                for name in sm.source_list
            ]

    def _open_scanner_impl(self, scanner: Scanner) -> None:
        try:
            sm = self._get_source_manager()
            sm.__enter__()
            src = sm.open_source(scanner.name)
        except Exception as exc:
            raise ScanError(
                f"Failed to open scanner {scanner.name!r}: {exc}"
            ) from exc

        self._handles[scanner.name] = (sm, src)

        # Query sources
        sources = [ScanSource.FLATBED]
        try:
            if src.get_capability(twain.CAP_FEEDERENABLED) is not None:
                sources.append(ScanSource.FEEDER)
        except Exception:
            pass
        scanner._sources = sources

        # Query maximum scan area per source
        try:
            phys_w = src.get_capability(twain.ICAP_PHYSICALWIDTH)
            phys_h = src.get_capability(twain.ICAP_PHYSICALHEIGHT)
            if phys_w is not None and phys_h is not None:
                ps = PageSize(
                    width=math.ceil(float(phys_w) * MM_PER_INCH * 10),
                    height=math.ceil(float(phys_h) * MM_PER_INCH * 10),
                )
                for source in sources:
                    scanner._max_page_sizes[source] = ps
        except Exception:
            pass

        scanner._resolutions = _read_twain_resolutions(src)
        scanner._color_modes = _read_twain_color_modes(src)
        scanner._defaults = _read_twain_defaults(src, scanner._sources)

    def _close_scanner_impl(self, scanner: Scanner) -> None:
        handle = self._handles.pop(scanner.name, None)
        if handle is not None:
            sm, src = handle
            try:
                src.close()
            except Exception:
                pass
            try:
                sm.__exit__(None, None, None)
            except Exception:
                pass

    def _scan_pages_impl(self, scanner: Scanner, options: ScanOptions) -> list[ScannedPage]:
        handle = self._handles.get(scanner.name)
        if handle is None:
            raise ScanError("Scanner is not open")

        _, src = handle

        try:
            if options.source == ScanSource.FEEDER:
                src.set_capability(twain.CAP_FEEDERENABLED, twain.TWTY_BOOL, True)
            elif options.source == ScanSource.FLATBED:
                src.set_capability(twain.CAP_FEEDERENABLED, twain.TWTY_BOOL, False)

            if options.page_size is not None:
                width_in = options.page_size.width / 10.0 / MM_PER_INCH
                height_in = options.page_size.height / 10.0 / MM_PER_INCH
                src.set_image_layout((0, 0, width_in, height_in))

            check_progress(options.progress, 0)

            try:
                src.request_acquire(show_ui=False, modal_ui=False)
            except Exception as exc:
                msg = str(exc).lower()
                if "cancel" in msg or "abort" in msg:
                    raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                raise

            is_feeder = options.source == ScanSource.FEEDER
            pages: list[ScannedPage] = []

            while True:
                try:
                    native_handle, more_pending = src.xfer_image_natively()
                except Exception as exc:
                    msg = str(exc).lower()
                    if "cancel" in msg or "abort" in msg:
                        raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                    if is_feeder and not pages:
                        raise FeederEmptyError("No documents in feeder") from exc
                    raise

                bmp_data = twain.dib_to_bm_file(native_handle)
                raw, w, h, ct, bd = _bmp_to_raw(bmp_data)
                pages.append(ScannedPage(data=raw, width=w, height=h, color_type=ct, bit_depth=bd))

                if is_feeder:
                    if not more_pending:
                        break
                else:
                    if options.next_page is not None and options.next_page(len(pages)):
                        continue
                    break

            check_progress(options.progress, 100)

            return pages
        except (ScanAborted, ScanError):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
