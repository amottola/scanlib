import pytest

from scanlib._types import (
    ColorMode,
    ImageFormat,
    PageSize,
    ScanAborted,
    ScanLibError,
    Scanner,
    ScannerDefaults,
    ScannerNotOpenError,
    ScanOptions,
    ScanSource,
    ScannedDocument,
    ScannedPage,
    build_pdf,
)


class TestColorMode:
    def test_values(self):
        assert ColorMode.COLOR.value == "color"
        assert ColorMode.GRAY.value == "gray"
        assert ColorMode.BW.value == "bw"


class TestScanSource:
    def test_values(self):
        assert ScanSource.FLATBED.value == "flatbed"
        assert ScanSource.FEEDER.value == "feeder"


class TestPageSize:
    def test_creation(self):
        ps = PageSize(width=2100, height=2970)
        assert ps.width == 2100
        assert ps.height == 2970


class TestScanner:
    def test_creation(self):
        s = Scanner(name="test", vendor="Acme", model="X100", backend="sane")
        assert s.name == "test"
        assert s.vendor == "Acme"
        assert s.model == "X100"
        assert s.backend == "sane"
        assert s.is_open is False

    def test_optional_fields(self):
        s = Scanner(name="test", vendor=None, model=None, backend="twain")
        assert s.vendor is None
        assert s.model is None

    def test_sources_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            _ = s.sources

    def test_scan_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            s.scan()

    def test_defaults_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            _ = s.defaults

    def test_defaults_none_by_default(self):
        mock_backend = type("B", (), {
            "open_scanner": lambda self, s: None,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            assert s.defaults is None

    def test_defaults_populated(self):
        defaults = ScannerDefaults(
            dpi=300, color_mode=ColorMode.COLOR, source=ScanSource.FLATBED,
        )
        def open_scanner(self, s):
            s._defaults = defaults
        mock_backend = type("B", (), {
            "open_scanner": open_scanner,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            assert s.defaults is defaults
            assert s.defaults.dpi == 300

    def test_resolutions_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            _ = s.resolutions

    def test_resolutions_default_empty(self):
        mock_backend = type("B", (), {
            "open_scanner": lambda self, s: None,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            assert s.resolutions == []

    def test_resolutions_populated(self):
        def open_scanner(self, s):
            s._resolutions = [150, 300, 600]
        mock_backend = type("B", (), {
            "open_scanner": open_scanner,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            assert s.resolutions == [150, 300, 600]

    def test_color_modes_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            _ = s.color_modes

    def test_color_modes_default_empty(self):
        mock_backend = type("B", (), {
            "open_scanner": lambda self, s: None,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            assert s.color_modes == []

    def test_color_modes_populated(self):
        def open_scanner(self, s):
            s._color_modes = [ColorMode.COLOR, ColorMode.GRAY]
        mock_backend = type("B", (), {
            "open_scanner": open_scanner,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            assert s.color_modes == [ColorMode.COLOR, ColorMode.GRAY]

    def test_max_page_sizes_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            _ = s.max_page_sizes

    def test_max_page_sizes_default_empty(self):
        mock_backend = type("B", (), {
            "open_scanner": lambda self, s: None,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            assert s.max_page_sizes == {}

    def test_context_manager(self):
        mock_backend = type("B", (), {
            "open_scanner": lambda self, s: None,
            "close_scanner": lambda self, s: None,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s as opened:
            assert opened is s
            assert s.is_open is True
        assert s.is_open is False

    def test_repr(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        assert "closed" in repr(s)


class TestScanOptions:
    def test_defaults(self):
        opts = ScanOptions()
        assert opts.dpi == 300
        assert opts.color_mode == ColorMode.COLOR
        assert opts.page_size is None
        assert opts.source is None
        assert opts.progress is None
        assert opts.next_page is None

    def test_custom(self):
        opts = ScanOptions(
            dpi=600,
            color_mode=ColorMode.GRAY,
            page_size=PageSize(2100, 2970),
            source=ScanSource.FEEDER,
        )
        assert opts.dpi == 600
        assert opts.color_mode == ColorMode.GRAY
        assert opts.page_size == PageSize(2100, 2970)
        assert opts.source == ScanSource.FEEDER


class TestScannedDocument:
    def test_creation(self):
        doc = ScannedDocument(
            data=b"%PDF-1.4",
            page_count=1,
            width=100,
            height=200,
            dpi=300,
            color_mode=ColorMode.COLOR,
        )
        assert doc.data == b"%PDF-1.4"
        assert doc.page_count == 1
        assert doc.width == 100
        assert doc.height == 200
        assert doc.dpi == 300
        assert doc.color_mode == ColorMode.COLOR


class TestScanAborted:
    def test_is_scanlib_error(self):
        assert issubclass(ScanAborted, ScanLibError)


class TestScannerNotOpenError:
    def test_is_scanlib_error(self):
        assert issubclass(ScannerNotOpenError, ScanLibError)


def _make_page(width=16, height=16, color_type=2, bit_depth=8):
    """Create a ScannedPage with deterministic pixel data."""
    if color_type == 2:
        channels = 3
    else:
        channels = 1
    data = bytes(
        [(y * 37 + x * 13) & 0xFF
         for y in range(height)
         for x in range(width * channels)]
    )
    return ScannedPage(
        data=data, width=width, height=height,
        color_type=color_type, bit_depth=bit_depth,
    )


class TestScannedPage:
    def test_color_mode_rgb(self):
        page = _make_page(color_type=2)
        assert page.color_mode == ColorMode.COLOR

    def test_color_mode_gray(self):
        page = _make_page(color_type=0)
        assert page.color_mode == ColorMode.GRAY

    def test_to_jpeg_rgb(self):
        page = _make_page(color_type=2)
        jpg = page.to_jpeg()
        assert jpg[:2] == b"\xff\xd8"  # SOI marker
        assert jpg[-2:] == b"\xff\xd9"  # EOI marker

    def test_to_jpeg_gray(self):
        page = _make_page(color_type=0)
        jpg = page.to_jpeg(quality=50)
        assert jpg[:2] == b"\xff\xd8"

    def test_to_png_rgb(self):
        page = _make_page(color_type=2)
        png = page.to_png()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_to_png_gray(self):
        page = _make_page(color_type=0)
        png = page.to_png()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_to_png_roundtrip(self):
        """PNG output can be decoded (validate structure)."""
        import struct, zlib
        page = _make_page(width=4, height=4, color_type=2)
        png = page.to_png()
        # Parse IHDR
        ihdr_len = struct.unpack(">I", png[8:12])[0]
        assert png[12:16] == b"IHDR"
        w, h, bd, ct = struct.unpack(">IIBBBBB", png[16:16 + ihdr_len])[:4]
        assert w == 4
        assert h == 4
        assert bd == 8
        assert ct == 2


class TestScanPages:
    def test_scan_pages_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            s.scan_pages()

    def test_scan_pages_yields_pages(self):
        pages = [_make_page(), _make_page()]

        def scan_pages(self, scanner, options):
            return iter(pages)

        mock_backend = type("B", (), {
            "open_scanner": lambda self, s: None,
            "close_scanner": lambda self, s: None,
            "scan_pages": scan_pages,
        })()
        s = Scanner(name="test", vendor=None, model=None, backend="sane",
                    _backend_impl=mock_backend)
        with s:
            result = list(s.scan_pages())
        assert len(result) == 2
        assert all(isinstance(p, ScannedPage) for p in result)


class TestBuildPdf:
    def test_basic(self):
        pages = [_make_page(), _make_page()]
        doc = build_pdf(pages, dpi=300)
        assert doc.data[:8] == b"%PDF-1.4"
        assert doc.page_count == 2

    def test_reordered_pages(self):
        page_small = _make_page(width=8, height=8)
        page_large = _make_page(width=32, height=32)
        doc = build_pdf([page_large, page_small], dpi=300)
        assert doc.page_count == 2
        assert doc.width == 32  # first page dimensions
        assert doc.height == 32

    def test_png_format(self):
        pages = [_make_page()]
        doc = build_pdf(pages, image_format=ImageFormat.PNG)
        assert b"/FlateDecode" in doc.data

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No pages"):
            build_pdf([])
