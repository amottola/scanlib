"""Tests for the stdlib-only PDF writer."""

import pytest

from scanlib._pdf import pages_to_pdf
from scanlib._types import ColorMode, ImageFormat, ScannedPage
from _scanlib_accel import gray_to_bw, rgb_to_gray


def _make_raw(width, height, color_type=2, bit_depth=8):
    """Create raw pixel data for testing.

    Returns a ScannedPage with deterministic pixel values.
    """
    if color_type == 0 and bit_depth == 1:
        # 1-bit grayscale: pack bits
        row_bytes = (width + 7) // 8
        data = bytearray(row_bytes * height)
        for y in range(height):
            for x in range(width):
                if (y * 37 + x * 13) & 1:
                    data[y * row_bytes + x // 8] |= 1 << (7 - (x % 8))
        return ScannedPage(
            data=bytes(data), width=width, height=height,
            color_type=color_type, bit_depth=bit_depth,
        )

    if color_type == 0:
        channels = 1
    elif color_type == 2:
        channels = 3
    else:
        raise ValueError(f"Unsupported color type: {color_type}")

    bpp = channels * bit_depth // 8
    data = bytes(
        [(y * 37 + x * 13) & 0xFF for y in range(height) for x in range(width * bpp)]
    )
    return ScannedPage(
        data=data, width=width, height=height,
        color_type=color_type, bit_depth=bit_depth,
    )


class TestPagesToPdfPng:
    """Tests for pages_to_pdf with PNG (FlateDecode) encoding."""

    def test_single_page(self):
        page = _make_raw(100, 200, color_type=2)
        pdf, count, w, h = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.PNG,
        )
        assert pdf[:8] == b"%PDF-1.4"
        assert b"%%EOF" in pdf
        assert b"/Type /Catalog" in pdf
        assert b"/Type /Pages" in pdf
        assert b"/Type /Page" in pdf
        assert b"/Count 1" in pdf
        assert count == 1
        assert w == 100
        assert h == 200

    def test_multi_page(self):
        page1 = _make_raw(100, 200, color_type=2)
        page2 = _make_raw(50, 100, color_type=0)
        pdf, count, w, h = pages_to_pdf(
            iter([page1, page2]), dpi=300, image_format=ImageFormat.PNG,
        )
        assert pdf[:8] == b"%PDF-1.4"
        assert b"/Count 2" in pdf
        assert count == 2
        assert w == 100  # first page dimensions
        assert h == 200

    def test_grayscale_page(self):
        page = _make_raw(10, 10, color_type=0)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, image_format=ImageFormat.PNG,
        )
        assert b"/DeviceGray" in pdf
        assert b"/Type /Page" in pdf

    def test_rgb_page(self):
        page = _make_raw(10, 10, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, image_format=ImageFormat.PNG,
        )
        assert b"/DeviceRGB" in pdf

    def test_empty_pages_raises(self):
        with pytest.raises(ValueError, match="No pages"):
            pages_to_pdf(iter([]), dpi=300, image_format=ImageFormat.PNG)

    def test_page_dimensions(self):
        # 300 DPI, 300x600 px -> 72x144 pt
        page = _make_raw(300, 600, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.PNG,
        )
        assert b"/MediaBox [0 0 72.0000 144.0000]" in pdf

    def test_flatedecode_filter(self):
        page = _make_raw(10, 10, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, image_format=ImageFormat.PNG,
        )
        assert b"/FlateDecode" in pdf


class TestPagesToPdfJpeg:
    """Tests for pages_to_pdf with JPEG (DCTDecode) encoding."""

    def test_single_page_rgb(self):
        page = _make_raw(16, 16, color_type=2)
        pdf, count, w, h = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.JPEG,
        )
        assert pdf[:8] == b"%PDF-1.4"
        assert b"/Filter /DCTDecode" in pdf
        assert b"/DeviceRGB" in pdf
        assert b"/BitsPerComponent 8" in pdf
        assert count == 1

    def test_single_page_grayscale(self):
        page = _make_raw(16, 16, color_type=0)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.JPEG,
        )
        assert b"/Filter /DCTDecode" in pdf
        assert b"/DeviceGray" in pdf

    def test_jpeg_produces_valid_dctdecode(self):
        """JPEG encoding produces a valid PDF with DCTDecode filter."""
        page = _make_raw(100, 100, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.JPEG,
        )
        assert b"/Filter /DCTDecode" in pdf
        # JPEG stream should start with JFIF SOI marker
        soi_idx = pdf.find(b"\xff\xd8\xff\xe0")
        assert soi_idx > 0

    def test_quality_affects_size(self):
        page = _make_raw(100, 100, color_type=2)
        pdf_low, *_ = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.JPEG, jpeg_quality=10,
        )
        page = _make_raw(100, 100, color_type=2)
        pdf_high, *_ = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.JPEG, jpeg_quality=95,
        )
        assert len(pdf_low) < len(pdf_high)

    def test_bw_mode_uses_grayscale_jpeg(self):
        """BW mode with JPEG uses 8-bit grayscale (JPEG can't do 1-bit)."""
        page = _make_raw(16, 16, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=300, color_mode=ColorMode.BW,
            image_format=ImageFormat.JPEG,
        )
        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 8" in pdf
        assert b"/Filter /DCTDecode" in pdf


class TestRgbToGray:
    def test_pure_white(self):
        # White pixel (255, 255, 255) -> (76*255+150*255+29*255)>>8 = 254
        result = rgb_to_gray(bytes([255, 255, 255]), 1, 1)
        assert result == bytes([254])

    def test_pure_black(self):
        result = rgb_to_gray(bytes([0, 0, 0]), 1, 1)
        assert result == bytes([0])

    def test_red_channel(self):
        # Pure red (255, 0, 0) -> (76*255)>>8 = 75
        result = rgb_to_gray(bytes([255, 0, 0]), 1, 1)
        assert result[0] == 75


class TestGrayToBw:
    def test_threshold(self):
        # 8 pixels: values above and below 128
        gray = bytes([0, 64, 127, 128, 192, 255, 0, 255])
        result = gray_to_bw(gray, 8, 1)
        # 0=black, 1=white. Pixels >= 128 become 1.
        # Bits: 0,0,0,1,1,1,0,1 = 0b00011101 = 0x1D
        assert result == bytes([0x1D])

    def test_row_padding(self):
        # 3 pixels wide -> 1 byte per row, 5 trailing bits zeroed
        gray = bytes([255, 0, 255])
        result = gray_to_bw(gray, 3, 1)
        # Bits: 1,0,1,0,0,0,0,0 = 0b10100000 = 0xA0
        assert result == bytes([0xA0])


class TestColorModeConversion:
    def test_gray_mode_converts_rgb(self):
        page = _make_raw(4, 4, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, color_mode=ColorMode.GRAY,
            image_format=ImageFormat.PNG,
        )
        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 8" in pdf
        assert b"/DeviceRGB" not in pdf

    def test_bw_mode_converts_rgb(self):
        page = _make_raw(8, 4, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, color_mode=ColorMode.BW,
            image_format=ImageFormat.PNG,
        )
        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 1" in pdf

    def test_bw_mode_from_grayscale(self):
        page = _make_raw(8, 4, color_type=0)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, color_mode=ColorMode.BW,
            image_format=ImageFormat.PNG,
        )
        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 1" in pdf

    def test_bw_mode_from_1bit(self):
        """1-bit input with BW mode passes through without re-conversion."""
        page = _make_raw(8, 4, color_type=0, bit_depth=1)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, color_mode=ColorMode.BW,
            image_format=ImageFormat.PNG,
        )
        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 1" in pdf

    def test_color_mode_default_unchanged(self):
        page = _make_raw(4, 4, color_type=2)
        pdf, *_ = pages_to_pdf(
            iter([page]), dpi=72, image_format=ImageFormat.PNG,
        )
        assert b"/DeviceRGB" in pdf
        assert b"/BitsPerComponent 8" in pdf

    def test_bw_pdf_smaller_than_color(self):
        page = _make_raw(100, 100, color_type=2)
        pdf_color, *_ = pages_to_pdf(
            iter([page]), dpi=300, image_format=ImageFormat.PNG,
        )
        page = _make_raw(100, 100, color_type=2)
        pdf_bw, *_ = pages_to_pdf(
            iter([page]), dpi=300, color_mode=ColorMode.BW,
            image_format=ImageFormat.PNG,
        )
        assert len(pdf_bw) < len(pdf_color)
