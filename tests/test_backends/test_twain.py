from unittest import mock

import pytest

from scanlib._types import ColorMode, ScanOptions, ScanSource


@pytest.fixture(autouse=True)
def mock_twain_module():
    """Provide mock modules so tests work on any platform."""
    mock_twain = mock.MagicMock()
    mock_ctypes = mock.MagicMock()
    mock_wintypes = mock.MagicMock()
    with mock.patch.dict("sys.modules", {
        "twain": mock_twain,
        "ctypes": mock_ctypes,
        "ctypes.wintypes": mock_wintypes,
    }):
        yield mock_twain


class TestBmpToPng:
    def _make_bmp(self, width, height, bpp=24):
        """Create a minimal valid BMP file."""
        import struct

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
        from scanlib.backends._twain import _bmp_to_png

        bmp = self._make_bmp(4, 3, 24)
        png_data, w, h = _bmp_to_png(bmp)

        assert png_data[:8] == b"\x89PNG\r\n\x1a\n"
        assert w == 4
        assert h == 3

    def test_converts_32bit_bmp(self):
        from scanlib.backends._twain import _bmp_to_png

        bmp = self._make_bmp(2, 2, 32)
        png_data, w, h = _bmp_to_png(bmp)

        assert png_data[:8] == b"\x89PNG\r\n\x1a\n"
        assert w == 2
        assert h == 2

    def test_converts_8bit_bmp(self):
        from scanlib.backends._twain import _bmp_to_png

        bmp = self._make_bmp(4, 2, 8)
        png_data, w, h = _bmp_to_png(bmp)

        assert png_data[:8] == b"\x89PNG\r\n\x1a\n"
        assert w == 4
        assert h == 2

    def test_invalid_bmp_raises(self):
        from scanlib.backends._twain import _bmp_to_png
        from scanlib._types import ScanError

        with pytest.raises(ScanError, match="Invalid BMP"):
            _bmp_to_png(b"not a bmp")


class TestTwainBackend:
    def _make_source_manager(self, mock_twain_module, source_list):
        mock_sm = mock.MagicMock()
        mock_sm.__enter__ = mock.MagicMock(return_value=mock_sm)
        mock_sm.__exit__ = mock.MagicMock(return_value=False)
        mock_sm.source_list = source_list
        mock_twain_module.SourceManager.return_value = mock_sm
        return mock_sm

    def test_list_scanners(self, mock_twain_module):
        self._make_source_manager(mock_twain_module, ["Scanner A", "Scanner B"])

        from scanlib.backends._twain import TwainBackend

        backend = TwainBackend()
        scanners = backend.list_scanners()

        assert len(scanners) == 2
        assert scanners[0].name == "Scanner A"
        assert scanners[0].backend == "twain"
        assert scanners[1].name == "Scanner B"

    def test_scan_no_scanners(self, mock_twain_module):
        self._make_source_manager(mock_twain_module, [])

        from scanlib.backends._twain import TwainBackend
        from scanlib._types import NoScannerFoundError

        backend = TwainBackend()
        with pytest.raises(NoScannerFoundError):
            backend.scan(None, ScanOptions())

