from unittest import mock
import struct
import zlib

import pytest

import ImageCaptureCore
from scanlib._types import ScanError, ScanSource


class TestReadPngDimensions:
    def _make_png_header(self, width, height):
        """Create minimal PNG header bytes (signature + IHDR)."""
        sig = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
        ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + ihdr_crc
        return sig + ihdr

    def test_reads_dimensions(self):
        from scanlib.backends._macos import _read_png_dimensions

        data = self._make_png_header(640, 480)
        w, h = _read_png_dimensions(data)
        assert w == 640
        assert h == 480

    def test_invalid_png_raises(self):
        from scanlib.backends._macos import _read_png_dimensions

        with pytest.raises(ScanError, match="Invalid PNG"):
            _read_png_dimensions(b"not a png")


class TestReadSourcesFromDevice:
    def test_flatbed_and_feeder(self):
        from scanlib.backends._macos import _read_sources_from_device

        device = mock.MagicMock()
        device.availableFunctionalUnitTypes.return_value = [
            ImageCaptureCore.ICScannerFunctionalUnitTypeFlatbed,
            ImageCaptureCore.ICScannerFunctionalUnitTypeDocumentFeeder,
        ]
        sources = _read_sources_from_device(device)

        assert ScanSource.FLATBED in sources
        assert ScanSource.FEEDER in sources

    def test_flatbed_only(self):
        from scanlib.backends._macos import _read_sources_from_device

        device = mock.MagicMock()
        device.availableFunctionalUnitTypes.return_value = [
            ImageCaptureCore.ICScannerFunctionalUnitTypeFlatbed,
        ]
        sources = _read_sources_from_device(device)

        assert sources == [ScanSource.FLATBED]

    def test_no_units(self):
        from scanlib.backends._macos import _read_sources_from_device

        device = mock.MagicMock()
        device.availableFunctionalUnitTypes.return_value = None
        sources = _read_sources_from_device(device)

        assert sources == []
