from unittest import mock

import pytest

from scanlib._types import ColorMode, ScanError, Scanner, ScanOptions, ScanSource


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

    def test_converts_1bit_bmp(self):
        from scanlib.backends._twain import _bmp_to_png
        import struct as st

        width, height = 8, 2
        # 1-bit BMP: palette + bit-packed rows
        header_size = 14 + 40
        palette_size = 2 * 4  # 2 colors, 4 bytes each (BGRA)
        data_offset = header_size + palette_size
        bmp_row_size = 4  # 8 pixels = 1 byte, padded to 4
        pixel_data_size = bmp_row_size * height
        file_size = data_offset + pixel_data_size

        bmp = b"BM"
        bmp += st.pack("<I", file_size)
        bmp += b"\x00\x00\x00\x00"
        bmp += st.pack("<I", data_offset)
        # DIB header
        bmp += st.pack("<I", 40)
        bmp += st.pack("<i", width)
        bmp += st.pack("<i", height)  # bottom-up
        bmp += st.pack("<HH", 1, 1)  # planes, bpp
        bmp += st.pack("<I", 0)  # compression
        bmp += st.pack("<I", pixel_data_size)
        bmp += st.pack("<ii", 2835, 2835)
        bmp += st.pack("<II", 2, 0)  # colors used, important
        # Palette: entry 0 = black (B,G,R,A), entry 1 = white
        bmp += bytes([0, 0, 0, 0])      # black
        bmp += bytes([255, 255, 255, 0]) # white
        # Row 0 (bottom): all zeros
        bmp += bytes([0x00]) + bytes(3)
        # Row 1 (top): alternating
        bmp += bytes([0xAA]) + bytes(3)

        png_data, w, h = _bmp_to_png(bmp)

        assert png_data[:8] == b"\x89PNG\r\n\x1a\n"
        assert w == 8
        assert h == 2

    def test_invalid_bmp_raises(self):
        from scanlib.backends._twain import _bmp_to_png

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

    def test_list_scanners_empty(self, mock_twain_module):
        self._make_source_manager(mock_twain_module, [])

        from scanlib.backends._twain import TwainBackend

        backend = TwainBackend()
        scanners = backend.list_scanners()
        assert scanners == []

    def test_open_scanner_queries_sources(self, mock_twain_module):
        mock_sm = self._make_source_manager(mock_twain_module, ["Scanner A"])
        mock_src = mock.MagicMock()
        mock_src.get_capability.return_value = True
        mock_sm.open_source.return_value = mock_src

        from scanlib.backends._twain import TwainBackend

        backend = TwainBackend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert ScanSource.FLATBED in scanners[0]._sources
        assert ScanSource.FEEDER in scanners[0]._sources

    def test_open_scanner_queries_max_page_sizes(self, mock_twain_module):
        mock_sm = self._make_source_manager(mock_twain_module, ["Scanner A"])
        mock_src = mock.MagicMock()
        mock_src.get_capability.side_effect = lambda cap: {
            mock_twain_module.CAP_FEEDERENABLED: True,
            mock_twain_module.ICAP_PHYSICALWIDTH: 8.5,   # inches
            mock_twain_module.ICAP_PHYSICALHEIGHT: 11.69, # inches
        }.get(cap)
        mock_sm.open_source.return_value = mock_src

        from scanlib.backends._twain import TwainBackend

        backend = TwainBackend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        sizes = scanners[0]._max_page_sizes
        assert ScanSource.FLATBED in sizes
        assert ScanSource.FEEDER in sizes
        assert sizes[ScanSource.FLATBED].width == 2159   # ceil(8.5 * 25.4 * 10)
        assert sizes[ScanSource.FLATBED].height == 2970  # ceil(11.69 * 25.4 * 10)

    def test_scan_pages_not_open_raises(self, mock_twain_module):
        self._make_source_manager(mock_twain_module, ["Scanner A"])

        from scanlib.backends._twain import TwainBackend

        backend = TwainBackend()
        scanners = backend.list_scanners()

        with pytest.raises(ScanError, match="not open"):
            backend.scan_pages(scanners[0], ScanOptions())
