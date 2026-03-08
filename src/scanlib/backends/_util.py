"""Shared utilities for scanner backends."""

from __future__ import annotations

from collections.abc import Callable

from .._types import ScanAborted

MM_PER_INCH = 25.4


def check_progress(progress: Callable[[int], bool] | None, percent: int) -> None:
    """Call the progress callback; raise ScanAborted if it returns False."""
    if progress is not None and progress(percent) is False:
        raise ScanAborted("Scan aborted")


def rgb_to_gray(data: bytes, width: int, height: int) -> bytes:
    """Convert 8-bit interleaved RGB to 8-bit grayscale using luminance."""
    count = width * height
    out = bytearray(count)
    for i in range(count):
        off = i * 3
        out[i] = (76 * data[off] + 150 * data[off + 1] + 29 * data[off + 2]) >> 8
    return bytes(out)


def gray_to_bw(data: bytes, width: int, height: int) -> bytes:
    """Convert 8-bit grayscale to 1-bit packed (MSB first), threshold at 128.

    In PNG 1-bit grayscale: 0=black, 1=white.
    """
    row_bytes = (width + 7) // 8
    out = bytearray(row_bytes * height)
    for y in range(height):
        src_off = y * width
        dst_off = y * row_bytes
        for x in range(0, width, 8):
            byte_val = 0
            for bit in range(min(8, width - x)):
                if data[src_off + x + bit] >= 128:
                    byte_val |= 0x80 >> bit
            out[dst_off + x // 8] = byte_val
    return bytes(out)


def trim_rows(data: bytes, height: int, stride: int, row_width: int) -> bytes:
    """Remove row padding from raw scan data.

    *stride* is the actual bytes per row in *data* (may include padding).
    *row_width* is the desired bytes per row (pixel data only).
    """
    if stride <= row_width:
        return data
    trimmed = bytearray(height * row_width)
    for y in range(height):
        trimmed[y * row_width : (y + 1) * row_width] = (
            data[y * stride : y * stride + row_width]
        )
    return bytes(trimmed)


