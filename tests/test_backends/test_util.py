import struct

import pytest

from scanlib.backends._util import raw_to_png


class TestRawToPng:
    def _parse_ihdr(self, png_data):
        """Extract width, height, bit_depth, color_type from PNG IHDR chunk."""
        assert png_data[:8] == b"\x89PNG\r\n\x1a\n"
        # IHDR chunk starts at byte 8: length(4) + "IHDR"(4) + data(13) + crc(4)
        ihdr_data = png_data[16:29]
        width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr_data[:10])
        return width, height, bit_depth, color_type

    def test_grayscale_8bit(self):
        width, height = 4, 2
        # Build raw data with filter byte per row
        raw = bytearray()
        for y in range(height):
            raw.append(0)  # filter: none
            raw.extend([128] * width)

        png = raw_to_png(bytes(raw), width, height, color_type=0, bit_depth=8)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        w, h, bd, ct = self._parse_ihdr(png)
        assert (w, h, bd, ct) == (4, 2, 8, 0)

    def test_rgb_8bit(self):
        width, height = 3, 2
        raw = bytearray()
        for y in range(height):
            raw.append(0)
            raw.extend([255, 0, 0] * width)  # red pixels

        png = raw_to_png(bytes(raw), width, height, color_type=2, bit_depth=8)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        w, h, bd, ct = self._parse_ihdr(png)
        assert (w, h, bd, ct) == (3, 2, 8, 2)

    def test_grayscale_1bit(self):
        width, height = 8, 2
        row_bytes = (width + 7) // 8  # 1 byte per row for 8 pixels
        raw = bytearray()
        for y in range(height):
            raw.append(0)
            raw.extend([0xFF] * row_bytes)  # all white

        png = raw_to_png(bytes(raw), width, height, color_type=0, bit_depth=1)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        w, h, bd, ct = self._parse_ihdr(png)
        assert (w, h, bd, ct) == (8, 2, 1, 0)

    def test_rgba_8bit(self):
        width, height = 2, 2
        raw = bytearray()
        for y in range(height):
            raw.append(0)
            raw.extend([255, 0, 0, 255] * width)

        png = raw_to_png(bytes(raw), width, height, color_type=6, bit_depth=8)

        w, h, bd, ct = self._parse_ihdr(png)
        assert (w, h, bd, ct) == (2, 2, 8, 6)
