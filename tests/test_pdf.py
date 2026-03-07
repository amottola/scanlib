"""Tests for the stdlib-only PDF writer."""

import struct
import zlib

import pytest

from scanlib._pdf import _parse_png, _unfilter_png_rows, png_pages_to_pdf, _rgb_to_gray, _gray_to_bw
from scanlib._types import ColorMode


def _make_png(width, height, color_type=2, bit_depth=8, filter_type=0):
    """Create a minimal valid PNG with the given parameters.

    Returns PNG file bytes.
    """
    if color_type == 0 and bit_depth == 1:
        # 1-bit grayscale: pack bits
        row_bytes = (width + 7) // 8
        raw_rows = []
        for y in range(height):
            row = bytearray(row_bytes)
            for x in range(width):
                if (y * 37 + x * 13) & 1:
                    row[x // 8] |= 1 << (7 - (x % 8))
            raw_rows.append(bytes([0]) + bytes(row))
    else:
        if color_type == 0:
            channels = 1
        elif color_type == 2:
            channels = 3
        elif color_type == 6:
            channels = 4
        else:
            raise ValueError(f"Unsupported color type: {color_type}")

        bpp = channels * bit_depth // 8

        # Build raw pixel data with filter bytes
        raw_rows = []
        for y in range(height):
            row = bytes([(y * 37 + x * 13) & 0xFF for x in range(width * bpp)])
            raw_rows.append(bytes([0]) + row)

    filtered_data = b"".join(raw_rows)
    compressed = zlib.compress(filtered_data)

    # Build PNG
    def chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", compressed)
    png += chunk(b"IEND", b"")
    return png


class TestParsePng:
    def test_rgb_image(self):
        png = _make_png(4, 3, color_type=2)
        w, h, bd, ct, pixels = _parse_png(png)
        assert w == 4
        assert h == 3
        assert bd == 8
        assert ct == 2
        assert len(pixels) == 4 * 3 * 3  # w * h * RGB

    def test_grayscale_image(self):
        png = _make_png(8, 2, color_type=0)
        w, h, bd, ct, pixels = _parse_png(png)
        assert w == 8
        assert h == 2
        assert ct == 0
        assert len(pixels) == 8 * 2

    def test_rgba_strips_alpha(self):
        png = _make_png(2, 2, color_type=6)
        w, h, bd, ct, pixels = _parse_png(png)
        assert ct == 2  # converted to RGB
        assert len(pixels) == 2 * 2 * 3  # w * h * RGB (alpha stripped)

    def test_invalid_data_raises(self):
        with pytest.raises(ValueError, match="Invalid PNG"):
            _parse_png(b"not a png")


class TestUnfilterPngRows:
    def test_filter_none(self):
        # 2x2 grayscale, filter type 0
        filtered = bytes([0, 10, 20, 0, 30, 40])
        result = _unfilter_png_rows(filtered, 2, 2, 1)
        assert result == bytes([10, 20, 30, 40])

    def test_filter_sub(self):
        # 4x1 grayscale, filter type 1 (Sub)
        # Raw: [1, 10, 5, 5, 5] -> result: [10, 15, 20, 25]
        filtered = bytes([1, 10, 5, 5, 5])
        result = _unfilter_png_rows(filtered, 4, 1, 1)
        assert result == bytes([10, 15, 20, 25])

    def test_filter_up(self):
        # 2x2 grayscale, filter type 2 (Up)
        # Row 0 (None): [0, 10, 20]
        # Row 1 (Up):   [2,  5,  5] -> [10+5, 20+5] = [15, 25]
        filtered = bytes([0, 10, 20, 2, 5, 5])
        result = _unfilter_png_rows(filtered, 2, 2, 1)
        assert result == bytes([10, 20, 15, 25])

    def test_filter_average(self):
        # 3x1 grayscale, filter type 3 (Average)
        # bpp=1, prev_row=all zeros
        # x[0] = raw[0] + floor((0 + 0) / 2) = 20
        # x[1] = raw[1] + floor((20 + 0) / 2) = 10 + 10 = 20
        # x[2] = raw[2] + floor((20 + 0) / 2) = 10 + 10 = 20
        filtered = bytes([3, 20, 10, 10])
        result = _unfilter_png_rows(filtered, 3, 1, 1)
        assert result == bytes([20, 20, 20])

    def test_filter_paeth(self):
        # 2x2 grayscale, filter type 4 (Paeth)
        # Row 0 (None): [0, 100, 100]
        # Row 1 (Paeth): [4, 10, 10]
        # For row 1:
        #   x[0]: a=0, b=100, c=0, paeth=100 -> 10+100=110
        #   x[1]: a=110, b=100, c=100, paeth -> p=110, pa=0, pb=10, pc=10 -> a=110 -> 10+110=120
        filtered = bytes([0, 100, 100, 4, 10, 10])
        result = _unfilter_png_rows(filtered, 2, 2, 1)
        assert result == bytes([100, 100, 110, 120])


class TestPngPagesToPdf:
    def test_single_page(self):
        png = _make_png(100, 200, color_type=2)
        pdf = png_pages_to_pdf([(png, 100, 200, 300)])

        assert pdf[:8] == b"%PDF-1.4"
        assert b"%%EOF" in pdf
        assert b"/Type /Catalog" in pdf
        assert b"/Type /Pages" in pdf
        assert b"/Type /Page" in pdf
        assert b"/Count 1" in pdf

    def test_multi_page(self):
        png1 = _make_png(100, 200, color_type=2)
        png2 = _make_png(50, 100, color_type=0)
        pdf = png_pages_to_pdf([
            (png1, 100, 200, 300),
            (png2, 50, 100, 150),
        ])

        assert pdf[:8] == b"%PDF-1.4"
        assert b"/Count 2" in pdf

    def test_grayscale_page(self):
        png = _make_png(10, 10, color_type=0)
        pdf = png_pages_to_pdf([(png, 10, 10, 72)])

        assert b"/DeviceGray" in pdf
        assert b"/Type /Page" in pdf

    def test_rgb_page(self):
        png = _make_png(10, 10, color_type=2)
        pdf = png_pages_to_pdf([(png, 10, 10, 72)])

        assert b"/DeviceRGB" in pdf

    def test_empty_pages_raises(self):
        with pytest.raises(ValueError, match="No pages"):
            png_pages_to_pdf([])

    def test_page_dimensions(self):
        # 300 DPI, 300x600 px -> 72x144 pt
        png = _make_png(300, 600, color_type=2)
        pdf = png_pages_to_pdf([(png, 300, 600, 300)])

        assert b"/MediaBox [0 0 72.0000 144.0000]" in pdf


class TestRgbToGray:
    def test_pure_white(self):
        # White pixel (255, 255, 255) -> 255
        result = _rgb_to_gray(bytes([255, 255, 255]), 1, 1)
        assert result == bytes([255])

    def test_pure_black(self):
        result = _rgb_to_gray(bytes([0, 0, 0]), 1, 1)
        assert result == bytes([0])

    def test_red_channel(self):
        # Pure red (255, 0, 0) -> 0.299 * 255 = 76.245 -> 76
        result = _rgb_to_gray(bytes([255, 0, 0]), 1, 1)
        assert result[0] == 76


class TestGrayToBw:
    def test_threshold(self):
        # 8 pixels: values above and below 128
        gray = bytes([0, 64, 127, 128, 192, 255, 0, 255])
        result = _gray_to_bw(gray, 8, 1)
        # 0=black, 1=white. Pixels >= 128 become 1.
        # Bits: 0,0,0,1,1,1,0,1 = 0b00011101 = 0x1D
        assert result == bytes([0x1D])

    def test_row_padding(self):
        # 3 pixels wide -> 1 byte per row, 5 trailing bits zeroed
        gray = bytes([255, 0, 255])
        result = _gray_to_bw(gray, 3, 1)
        # Bits: 1,0,1,0,0,0,0,0 = 0b10100000 = 0xA0
        assert result == bytes([0xA0])


class TestColorModeConversion:
    def test_gray_mode_converts_rgb(self):
        png = _make_png(4, 4, color_type=2)
        pdf = png_pages_to_pdf([(png, 4, 4, 72)], color_mode=ColorMode.GRAY)

        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 8" in pdf
        assert b"/DeviceRGB" not in pdf

    def test_bw_mode_converts_rgb(self):
        png = _make_png(8, 4, color_type=2)
        pdf = png_pages_to_pdf([(png, 8, 4, 72)], color_mode=ColorMode.BW)

        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 1" in pdf

    def test_bw_mode_from_grayscale(self):
        png = _make_png(8, 4, color_type=0)
        pdf = png_pages_to_pdf([(png, 8, 4, 72)], color_mode=ColorMode.BW)

        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 1" in pdf

    def test_bw_mode_from_1bit_png(self):
        """1-bit PNG input with BW mode passes through without re-conversion."""
        png = _make_png(8, 4, color_type=0, bit_depth=1)
        pdf = png_pages_to_pdf([(png, 8, 4, 72)], color_mode=ColorMode.BW)

        assert b"/DeviceGray" in pdf
        assert b"/BitsPerComponent 1" in pdf

    def test_color_mode_default_unchanged(self):
        png = _make_png(4, 4, color_type=2)
        pdf = png_pages_to_pdf([(png, 4, 4, 72)])

        assert b"/DeviceRGB" in pdf
        assert b"/BitsPerComponent 8" in pdf

    def test_bw_pdf_smaller_than_color(self):
        png = _make_png(100, 100, color_type=2)
        pdf_color = png_pages_to_pdf([(png, 100, 100, 300)])
        pdf_bw = png_pages_to_pdf([(png, 100, 100, 300)], color_mode=ColorMode.BW)

        assert len(pdf_bw) < len(pdf_color)
