import struct

import pytest

from _scanlib_accel import bmp_to_raw


class TestBmpToRaw:
    def _make_bmp(self, width, height, bpp=24):
        """Create a minimal valid BMP file."""
        channels = bpp // 8
        row_size = (width * channels + 3) & ~3
        pixel_data_size = row_size * height
        header_size = 14 + 40
        file_size = header_size + pixel_data_size

        bmp = b"BM"
        bmp += struct.pack("<I", file_size)
        bmp += b"\x00\x00\x00\x00"
        bmp += struct.pack("<I", header_size)

        bmp += struct.pack("<I", 40)
        bmp += struct.pack("<i", width)
        bmp += struct.pack("<i", height)
        bmp += struct.pack("<HH", 1, bpp)
        bmp += struct.pack("<I", 0)
        bmp += struct.pack("<I", pixel_data_size)
        bmp += struct.pack("<ii", 2835, 2835)
        bmp += struct.pack("<II", 0, 0)

        for y in range(height):
            row = bytes([0] * (width * channels))
            padding = b"\x00" * (row_size - width * channels)
            bmp += row + padding

        return bmp

    def test_converts_24bit_bmp(self):
        bmp = self._make_bmp(4, 3, 24)
        raw_data, w, h, ct, bd = bmp_to_raw(bmp)

        assert w == 4
        assert h == 3
        assert ct == 2  # RGB
        assert bd == 8
        assert len(raw_data) == 4 * 3 * 3

    def test_converts_32bit_bmp(self):
        bmp = self._make_bmp(2, 2, 32)
        raw_data, w, h, ct, bd = bmp_to_raw(bmp)

        assert w == 2
        assert h == 2
        assert ct == 6  # RGBA
        assert bd == 8

    def test_converts_8bit_bmp(self):
        bmp = self._make_bmp(4, 2, 8)
        raw_data, w, h, ct, bd = bmp_to_raw(bmp)

        assert w == 4
        assert h == 2
        assert ct == 0  # grayscale
        assert bd == 8

    def test_converts_1bit_bmp(self):
        width, height = 8, 2
        header_size = 14 + 40
        palette_size = 2 * 4
        data_offset = header_size + palette_size
        bmp_row_size = 4
        pixel_data_size = bmp_row_size * height
        file_size = data_offset + pixel_data_size

        bmp = b"BM"
        bmp += struct.pack("<I", file_size)
        bmp += b"\x00\x00\x00\x00"
        bmp += struct.pack("<I", data_offset)
        bmp += struct.pack("<I", 40)
        bmp += struct.pack("<i", width)
        bmp += struct.pack("<i", height)
        bmp += struct.pack("<HH", 1, 1)
        bmp += struct.pack("<I", 0)
        bmp += struct.pack("<I", pixel_data_size)
        bmp += struct.pack("<ii", 2835, 2835)
        bmp += struct.pack("<II", 2, 0)
        bmp += bytes([0, 0, 0, 0])
        bmp += bytes([255, 255, 255, 0])
        bmp += bytes([0x00]) + bytes(3)
        bmp += bytes([0xAA]) + bytes(3)

        raw_data, w, h, ct, bd = bmp_to_raw(bmp)

        assert w == 8
        assert h == 2
        assert ct == 0  # grayscale
        assert bd == 1

    def test_invalid_bmp_raises(self):
        with pytest.raises(ValueError, match="Invalid BMP"):
            bmp_to_raw(b"not a bmp")
