from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import math
import struct

import twain

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
from ._util import MM_PER_INCH, check_progress, raw_to_png

_COLOR_MODE_MAP = {
    ColorMode.COLOR: "color",
    ColorMode.GRAY: "gray",
    ColorMode.BW: "bw",
}


def _bmp_to_png(bmp_data: bytes) -> tuple[bytes, int, int]:
    """Convert BMP file bytes to PNG file bytes using only stdlib.

    Returns ``(png_bytes, width, height)``.
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
        png_bit_depth = 8
    elif bits_per_pixel == 32:
        color_type = 6  # RGBA
        channels = 4
        png_bit_depth = 8
    elif bits_per_pixel == 8:
        color_type = 0  # Grayscale
        channels = 1
        png_bit_depth = 8
    elif bits_per_pixel == 1:
        color_type = 0  # Grayscale 1-bit
        channels = 0  # special handling below
        png_bit_depth = 1
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
            raw_rows.append(b"\x00" + bytes(row))

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

            raw_rows.append(b"\x00" + row)

        raw_data = b"".join(raw_rows)

    png = raw_to_png(raw_data, width, height, color_type, png_bit_depth)
    return png, width, height


_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
)


def _default_wnd_proc(hwnd, msg, wparam, lparam):
    return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# prevent garbage collection of the callback
_wnd_proc_cb = _WNDPROC(_default_wnd_proc)


def _create_hidden_window() -> int:
    """Create a minimal hidden Win32 window and return its HWND."""
    hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
    class_name = "ScanlibTwainWindow"

    wc = wintypes.WNDCLASS()
    wc.lpfnWndProc = _wnd_proc_cb
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
    """Windows scanning backend using pytwain (TWAIN)."""

    def __init__(self) -> None:
        self._handles: dict[str, object] = {}

    def _get_source_manager(self) -> twain.SourceManager:
        hwnd = _create_hidden_window()
        return twain.SourceManager(hwnd)

    def list_scanners(self) -> list[Scanner]:
        with self._get_source_manager() as sm:
            return [
                Scanner(
                    name=name,
                    vendor=None,
                    model=None,
                    backend="twain",
                    _backend_impl=self,
                )
                for name in sm.source_list
            ]

    def open_scanner(self, scanner: Scanner) -> None:
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

    def close_scanner(self, scanner: Scanner) -> None:
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

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> list[ScannedPage]:
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
                    raise

                bmp_data = twain.dib_to_bm_file(native_handle)
                png_data, width, height = _bmp_to_png(bmp_data)
                pages.append(ScannedPage(png_data=png_data, width=width, height=height))

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
