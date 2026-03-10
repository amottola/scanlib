"""Tests for the JPEG encoder (platform-native: ImageIO / WIC / libjpeg-turbo)."""

from __future__ import annotations

import sys

import pytest

from scanlib._jpeg import encode_jpeg
from scanlib._types import ColorMode


def _solid_gray(width: int, height: int, value: int = 128) -> bytes:
    """Create a solid grayscale image."""
    return bytes([value]) * (width * height)


def _gradient_gray(width: int, height: int) -> bytes:
    """Create a grayscale gradient image."""
    return bytes(
        ((y * width + x) * 255 // max(width * height - 1, 1)) & 0xFF
        for y in range(height) for x in range(width)
    )


def _solid_rgb(width: int, height: int, r: int = 128, g: int = 128, b: int = 128) -> bytes:
    """Create a solid RGB image."""
    pixel = bytes([r, g, b])
    return pixel * (width * height)


def _gradient_rgb(width: int, height: int) -> bytes:
    """Create an RGB gradient image."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            r = (x * 255 // max(width - 1, 1)) & 0xFF
            g = (y * 255 // max(height - 1, 1)) & 0xFF
            b = ((x + y) * 128 // max(width + height - 2, 1)) & 0xFF
            out.extend([r, g, b])
    return bytes(out)


class TestJpegBasic:
    def test_grayscale_jfif_markers(self):
        data = encode_jpeg(_solid_gray(8, 8), 8, 8, ColorMode.GRAY, 85)
        assert data[:2] == b"\xff\xd8"  # SOI
        assert data[-2:] == b"\xff\xd9"  # EOI

    def test_rgb_jfif_markers(self):
        data = encode_jpeg(_solid_rgb(8, 8), 8, 8, ColorMode.COLOR, 85)
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_grayscale_gradient(self):
        data = encode_jpeg(_gradient_gray(32, 32), 32, 32, ColorMode.GRAY, 85)
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"
        assert len(data) > 0

    def test_rgb_gradient(self):
        data = encode_jpeg(_gradient_rgb(32, 32), 32, 32, ColorMode.COLOR, 85)
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_1x1_grayscale(self):
        data = encode_jpeg(b"\x80", 1, 1, ColorMode.GRAY, 85)
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_1x1_rgb(self):
        data = encode_jpeg(b"\xff\x00\x80", 1, 1, ColorMode.COLOR, 85)
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_non_multiple_of_8(self):
        data = encode_jpeg(_gradient_gray(13, 7), 13, 7, ColorMode.GRAY, 85)
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_non_multiple_of_16_rgb(self):
        data = encode_jpeg(_gradient_rgb(13, 7), 13, 7, ColorMode.COLOR, 85)
        assert data[:2] == b"\xff\xd8"
        assert data[-2:] == b"\xff\xd9"

    def test_encode_jpeg_callable(self):
        """Verify encode_jpeg is available on the current platform."""
        data = encode_jpeg(_solid_gray(8, 8), 8, 8, ColorMode.GRAY, 85)
        assert data[:2] == b"\xff\xd8"


class TestJpegQuality:
    def test_quality_affects_size(self):
        pixels = _gradient_gray(64, 64)
        low = encode_jpeg(pixels, 64, 64, ColorMode.GRAY, 10)
        high = encode_jpeg(pixels, 64, 64, ColorMode.GRAY, 95)
        assert len(low) < len(high)

    def test_quality_bounds(self):
        pixels = _solid_gray(8, 8)
        q1 = encode_jpeg(pixels, 8, 8, ColorMode.GRAY, 1)
        q100 = encode_jpeg(pixels, 8, 8, ColorMode.GRAY, 100)
        assert q1[:2] == b"\xff\xd8"
        assert q100[:2] == b"\xff\xd8"


class TestJpegSof:
    """Verify SOF0 marker contains correct dimensions and components."""

    def _find_sof0(self, data: bytes) -> int:
        """Return offset of SOF0 marker payload (after length field)."""
        pos = 0
        while pos < len(data) - 1:
            if data[pos] == 0xFF and data[pos + 1] == 0xC0:
                return pos + 4  # skip marker + length
            pos += 1
        raise ValueError("SOF0 not found")

    def test_grayscale_sof0(self):
        data = encode_jpeg(_solid_gray(32, 24), 32, 24, ColorMode.GRAY, 85)
        off = self._find_sof0(data)
        precision = data[off]
        h = (data[off + 1] << 8) | data[off + 2]
        w = (data[off + 3] << 8) | data[off + 4]
        ncomp = data[off + 5]
        assert precision == 8
        assert w == 32
        assert h == 24
        assert ncomp == 1

    def test_rgb_sof0(self):
        data = encode_jpeg(_solid_rgb(48, 32), 48, 32, ColorMode.COLOR, 85)
        off = self._find_sof0(data)
        precision = data[off]
        h = (data[off + 1] << 8) | data[off + 2]
        w = (data[off + 3] << 8) | data[off + 4]
        ncomp = data[off + 5]
        assert precision == 8
        assert w == 48
        assert h == 32
        assert ncomp == 3
