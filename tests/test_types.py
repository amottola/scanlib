import pytest

from scanlib._types import (
    ColorMode,
    PageSize,
    ScanAborted,
    ScanLibError,
    Scanner,
    ScannerDefaults,
    ScannerNotOpenError,
    ScanOptions,
    ScanSource,
    ScannedDocument,
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
        scanner = Scanner(name="s", vendor=None, model=None, backend="sane")
        doc = ScannedDocument(
            data=b"%PDF-1.4",
            page_count=1,
            width=100,
            height=200,
            dpi=300,
            color_mode=ColorMode.COLOR,
            scanner=scanner,
        )
        assert doc.data == b"%PDF-1.4"
        assert doc.page_count == 1
        assert doc.width == 100
        assert doc.height == 200
        assert doc.dpi == 300
        assert doc.color_mode == ColorMode.COLOR
        assert doc.scanner is scanner


class TestScanAborted:
    def test_is_scanlib_error(self):
        assert issubclass(ScanAborted, ScanLibError)


class TestScannerNotOpenError:
    def test_is_scanlib_error(self):
        assert issubclass(ScannerNotOpenError, ScanLibError)
