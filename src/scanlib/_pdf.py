"""Minimal stdlib-only PDF writer for embedding PNG pages."""

from __future__ import annotations

import struct
import zlib

from ._types import ColorMode
from .backends._util import gray_to_bw, rgb_to_gray


def _parse_png(data: bytes) -> tuple[int, int, int, int, bytes]:
    """Parse a PNG file and return (width, height, bit_depth, color_type, raw_pixels).

    Decompresses the IDAT stream and reconstructs unfiltered pixel data.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG data")

    pos = 8
    width = height = bit_depth = color_type = 0
    idat_chunks: list[bytes] = []

    while pos < len(data):
        chunk_len = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + chunk_len]
        pos += 12 + chunk_len  # length + type + data + crc

        if chunk_type == b"IHDR":
            width, height = struct.unpack(">II", chunk_data[:8])
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if not idat_chunks:
        raise ValueError("No IDAT chunks found in PNG")

    compressed = b"".join(idat_chunks)
    filtered = zlib.decompress(compressed)

    # Determine bytes per pixel
    if color_type == 0:  # grayscale
        channels = 1
    elif color_type == 2:  # RGB
        channels = 3
    elif color_type == 6:  # RGBA
        channels = 4
    else:
        raise ValueError(f"Unsupported PNG color type: {color_type}")

    # For sub-byte bit depths (1, 2, 4), stride is ceil(width * channels * bit_depth / 8)
    # and bpp for filtering is max(1, channels * bit_depth // 8)
    stride = (width * channels * bit_depth + 7) // 8
    bpp = max(1, channels * bit_depth // 8)
    raw_pixels = _unfilter_png_rows_stride(filtered, height, stride, bpp)

    # Strip alpha channel for PDF (RGBA → RGB)
    if color_type == 6:
        stripped = bytearray()
        row_bytes = width * 4
        for y in range(height):
            row_start = y * row_bytes
            for x in range(width):
                px = row_start + x * 4
                stripped.extend(raw_pixels[px : px + 3])
        raw_pixels = bytes(stripped)
        color_type = 2  # treat as RGB for PDF

    return width, height, bit_depth, color_type, raw_pixels


def _unfilter_png_rows(
    filtered: bytes, width: int, height: int, bpp: int
) -> bytes:
    """Reconstruct raw pixel data from PNG-filtered scanlines (8-bit channels)."""
    stride = width * bpp
    return _unfilter_png_rows_stride(filtered, height, stride, bpp)


def _unfilter_png_rows_stride(
    filtered: bytes, height: int, stride: int, bpp: int
) -> bytes:
    """Reconstruct raw pixel data from PNG-filtered scanlines."""
    result = bytearray(height * stride)
    prev_row = bytes(stride)

    for y in range(height):
        src_offset = y * (stride + 1)
        filter_type = filtered[src_offset]
        row_data = filtered[src_offset + 1 : src_offset + 1 + stride]
        dst_offset = y * stride

        if filter_type == 0:  # None
            result[dst_offset : dst_offset + stride] = row_data
        elif filter_type == 1:  # Sub
            row = bytearray(row_data)
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
            result[dst_offset : dst_offset + stride] = row
        elif filter_type == 2:  # Up
            row = bytearray(stride)
            for i in range(stride):
                row[i] = (row_data[i] + prev_row[i]) & 0xFF
            result[dst_offset : dst_offset + stride] = row
        elif filter_type == 3:  # Average
            row = bytearray(stride)
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                row[i] = (row_data[i] + (left + up) // 2) & 0xFF
            result[dst_offset : dst_offset + stride] = row
        elif filter_type == 4:  # Paeth
            row = bytearray(stride)
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                up_left = prev_row[i - bpp] if i >= bpp else 0
                row[i] = (row_data[i] + _paeth_predictor(left, up, up_left)) & 0xFF
            result[dst_offset : dst_offset + stride] = row
        else:
            raise ValueError(f"Unknown PNG filter type: {filter_type}")

        prev_row = result[dst_offset : dst_offset + stride]

    return bytes(result)


def _paeth_predictor(a: int, b: int, c: int) -> int:
    """PNG Paeth predictor function."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    elif pb <= pc:
        return b
    return c


def png_pages_to_pdf(
    pages: list[tuple[bytes, int, int, int]],
    color_mode: ColorMode = ColorMode.COLOR,
) -> bytes:
    """Convert a list of PNG pages to a single PDF file.

    Each element is ``(png_bytes, width_px, height_px, dpi)``.
    Returns the complete PDF file as bytes.
    """
    if not pages:
        raise ValueError("No pages to convert")

    objects: list[bytes] = []  # 1-indexed (objects[0] unused)
    objects.append(b"")  # placeholder for index 0

    # Object 1: Catalog
    # Object 2: Pages (will be filled after pages are created)
    objects.append(b"")  # catalog placeholder
    objects.append(b"")  # pages placeholder

    page_obj_ids: list[int] = []

    for png_data, width_px, height_px, dpi in pages:
        w, h, bit_depth, color_type, raw_pixels = _parse_png(png_data)

        # Apply color mode conversion
        if color_mode == ColorMode.GRAY:
            if color_type == 2:  # RGB → grayscale
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
            color_type = 0
            bit_depth = 8
        elif color_mode == ColorMode.BW:
            if color_type == 2:  # RGB → grayscale first
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
            if bit_depth == 8:  # 8-bit grayscale → 1-bit
                raw_pixels = gray_to_bw(raw_pixels, w, h)
            color_type = 0
            bit_depth = 1

        if color_type == 0:
            color_space = b"/DeviceGray"
        elif color_type == 2:
            color_space = b"/DeviceRGB"
        else:
            raise ValueError(f"Unsupported color type for PDF: {color_type}")

        compressed_pixels = zlib.compress(raw_pixels)

        # Image XObject
        img_obj_id = len(objects)
        img_stream = compressed_pixels
        img_dict = (
            f"<< /Type /XObject /Subtype /Image "
            f"/Width {w} /Height {h} "
            f"/BitsPerComponent {bit_depth} "
            f"/ColorSpace {color_space.decode()} "
            f"/Filter /FlateDecode "
            f"/Length {len(img_stream)} >>"
        ).encode()
        objects.append(img_dict + b"\nstream\n" + img_stream + b"\nendstream")

        # Content stream: draw image full-page
        media_w = width_px * 72.0 / dpi
        media_h = height_px * 72.0 / dpi
        content_bytes = f"q {media_w:.4f} 0 0 {media_h:.4f} 0 0 cm /Im0 Do Q".encode()
        content_obj_id = len(objects)
        content_dict = f"<< /Length {len(content_bytes)} >>".encode()
        objects.append(content_dict + b"\nstream\n" + content_bytes + b"\nendstream")

        # Page object
        page_obj_id = len(objects)
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {media_w:.4f} {media_h:.4f}] "
            f"/Contents {content_obj_id} 0 R "
            f"/Resources << /XObject << /Im0 {img_obj_id} 0 R >> >> >>"
        ).encode()
        objects.append(page_obj)
        page_obj_ids.append(page_obj_id)

    # Fill catalog (object 1)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    # Fill pages (object 2)
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_ids)} >>".encode()

    # Build PDF file
    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]  # index 0 unused

    for i in range(1, len(objects)):
        offsets.append(len(buf))
        buf.extend(f"{i} 0 obj\n".encode())
        buf.extend(objects[i])
        buf.extend(b"\nendobj\n")

    xref_offset = len(buf)
    buf.extend(b"xref\n")
    buf.extend(f"0 {len(objects)}\n".encode())
    buf.extend(b"0000000000 65535 f \n")
    for i in range(1, len(objects)):
        buf.extend(f"{offsets[i]:010d} 00000 n \n".encode())

    buf.extend(b"trailer\n")
    buf.extend(f"<< /Size {len(objects)} /Root 1 0 R >>\n".encode())
    buf.extend(b"startxref\n")
    buf.extend(f"{xref_offset}\n".encode())
    buf.extend(b"%%EOF\n")

    return bytes(buf)
