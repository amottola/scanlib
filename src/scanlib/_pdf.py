"""Minimal stdlib-only PDF writer for embedding scanned pages."""

from __future__ import annotations

import zlib
from collections.abc import Iterator

from _scanlib_accel import gray_to_bw, rgb_to_gray

from ._jpeg import encode_jpeg

from ._types import ColorMode, ImageFormat, ScannedPage


def pages_to_pdf(
    pages: Iterator[ScannedPage],
    *,
    dpi: int = 300,
    color_mode: ColorMode = ColorMode.COLOR,
    image_format: ImageFormat = ImageFormat.JPEG,
    jpeg_quality: int = 85,
) -> tuple[bytes, int, int, int]:
    """Convert scanned pages to a single PDF file.

    *pages* is an iterator of :class:`ScannedPage` objects carrying raw
    pixel data.  Each page is encoded (JPEG or PNG) and added to the PDF
    incrementally so only one page's raw pixels live in memory at a time.

    Returns ``(pdf_bytes, page_count, first_width, first_height)``.
    """
    objects: list[bytes] = []  # 1-indexed (objects[0] unused)
    objects.append(b"")  # placeholder for index 0

    # Object 1: Catalog
    # Object 2: Pages (will be filled after pages are created)
    objects.append(b"")  # catalog placeholder
    objects.append(b"")  # pages placeholder

    page_obj_ids: list[int] = []
    first_w = first_h = 0

    for page in pages:
        w, h = page.width, page.height
        raw_pixels = page.data
        color_type = page.color_type
        bit_depth = page.bit_depth

        if first_w == 0:
            first_w, first_h = w, h

        # Apply color mode conversion
        if color_mode == ColorMode.GRAY:
            if color_type == 2:  # RGB → grayscale
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
            color_type = 0
            bit_depth = 8
        elif color_mode == ColorMode.BW:
            if color_type == 2:  # RGB → grayscale first
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
            if image_format == ImageFormat.JPEG:
                # JPEG doesn't support 1-bit; use 8-bit grayscale
                color_type = 0
                bit_depth = 8
            else:
                if bit_depth == 8:  # 8-bit grayscale → 1-bit
                    raw_pixels = gray_to_bw(raw_pixels, w, h)
                color_type = 0
                bit_depth = 1

        # Encode image data
        if image_format == ImageFormat.JPEG:
            img_stream = encode_jpeg(raw_pixels, w, h, color_type, jpeg_quality)
            filter_name = "/DCTDecode"
            # JPEG carries its own bit depth; PDF needs BitsPerComponent = 8
            pdf_bpc = 8
        else:
            # PNG path: prepend filter byte 0 to each row, then zlib compress
            if bit_depth == 1:
                row_bytes = (w + 7) // 8
            elif color_type == 2:
                row_bytes = w * 3
            else:
                row_bytes = w

            filtered = bytearray()
            for y in range(h):
                filtered.append(0)  # PNG filter type: None
                src = y * row_bytes
                filtered.extend(raw_pixels[src:src + row_bytes])

            img_stream = zlib.compress(bytes(filtered))
            filter_name = "/FlateDecode"
            pdf_bpc = bit_depth

        if color_type == 0:
            color_space = "/DeviceGray"
        else:
            color_space = "/DeviceRGB"

        # Image XObject
        img_obj_id = len(objects)
        img_dict = (
            f"<< /Type /XObject /Subtype /Image "
            f"/Width {w} /Height {h} "
            f"/BitsPerComponent {pdf_bpc} "
            f"/ColorSpace {color_space} "
            f"/Filter {filter_name} "
            f"/Length {len(img_stream)} >>"
        ).encode()
        objects.append(img_dict + b"\nstream\n" + img_stream + b"\nendstream")

        # Content stream: draw image full-page
        media_w = w * 72.0 / dpi
        media_h = h * 72.0 / dpi
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

    if not page_obj_ids:
        raise ValueError("No pages to convert")

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

    return bytes(buf), len(page_obj_ids), first_w, first_h
