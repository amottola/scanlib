from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct
import zlib

import twain

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

_COLOR_MODE_MAP = {
    ColorMode.COLOR: "color",
    ColorMode.GRAY: "gray",
    ColorMode.BW: "bw",
}

_MM_PER_INCH = 25.4


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
    elif bits_per_pixel == 32:
        color_type = 6  # RGBA
        channels = 4
    elif bits_per_pixel == 8:
        color_type = 0  # Grayscale
        channels = 1
    else:
        raise ScanError(f"Unsupported BMP bit depth: {bits_per_pixel}")

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

    def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + chunk + crc

    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0),
    )
    png += _png_chunk(b"IDAT", zlib.compress(raw_data))
    png += _png_chunk(b"IEND", b"")

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


class TwainBackend:
    """Windows scanning backend using pytwain (TWAIN)."""

    def _get_source_manager(self) -> twain.SourceManager:
        hwnd = _create_hidden_window()
        return twain.SourceManager(hwnd)

    def _query_sources(self, source_name: str) -> list[ScanSource]:
        """Query available scan sources for a TWAIN source."""
        sources = [ScanSource.FLATBED]
        try:
            with self._get_source_manager() as sm:
                src = sm.open_source(source_name)
                try:
                    if src.get_capability(twain.CAP_FEEDERENABLED) is not None:
                        sources.append(ScanSource.FEEDER)
                except Exception:
                    pass
                finally:
                    src.close()
        except Exception:
            pass
        return sources

    def list_scanners(self) -> list[ScannerInfo]:
        with self._get_source_manager() as sm:
            scanners = []
            for name in sm.source_list:
                scanners.append(
                    ScannerInfo(
                        name=name,
                        vendor=None,
                        model=None,
                        backend="twain",
                        sources=self._query_sources(name),
                    )
                )
            return scanners

    def scan(
        self, scanner: ScannerInfo | None, options: ScanOptions
    ) -> ScannedDocument:
        if scanner is None:
            scanners = self.list_scanners()
            if not scanners:
                raise NoScannerFoundError("No scanners found")
            scanner = scanners[0]

        try:
            with self._get_source_manager() as sm:
                src = sm.open_source(scanner.name)
                try:
                    if options.source == ScanSource.FEEDER:
                        src.set_capability(twain.CAP_FEEDERENABLED, twain.TWTY_BOOL, True)
                    elif options.source == ScanSource.FLATBED:
                        src.set_capability(twain.CAP_FEEDERENABLED, twain.TWTY_BOOL, False)

                    if options.page_size is not None:
                        width_in = options.page_size.width / 10.0 / _MM_PER_INCH
                        height_in = options.page_size.height / 10.0 / _MM_PER_INCH
                        src.set_image_layout((0, 0, width_in, height_in))

                    if options.progress is not None and options.progress(0) is False:
                        raise ScanAborted("Scan aborted")

                    try:
                        src.request_acquire(show_ui=False, modal_ui=False)
                        handle, _ = src.xfer_image_natively()
                    except ScanAborted:
                        raise
                    except Exception as exc:
                        msg = str(exc).lower()
                        if "cancel" in msg or "abort" in msg:
                            raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                        raise

                    if options.progress is not None and options.progress(100) is False:
                        raise ScanAborted("Scan aborted")

                    bmp_data = twain.dib_to_bm_file(handle)
                finally:
                    src.close()

            png_data, width, height = _bmp_to_png(bmp_data)

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
