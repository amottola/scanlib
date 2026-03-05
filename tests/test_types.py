from scanlib._types import (
    ColorMode,
    PageSize,
    ScanAborted,
    ScanLibError,
    ScannerInfo,
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


class TestScannerInfo:
    def test_creation(self):
        info = ScannerInfo(name="test", vendor="Acme", model="X100", backend="sane")
        assert info.name == "test"
        assert info.vendor == "Acme"
        assert info.model == "X100"
        assert info.backend == "sane"
        assert info.sources == []

    def test_optional_fields(self):
        info = ScannerInfo(name="test", vendor=None, model=None, backend="twain")
        assert info.vendor is None
        assert info.model is None

    def test_sources(self):
        info = ScannerInfo(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            sources=[ScanSource.FLATBED, ScanSource.FEEDER],
        )
        assert info.sources == [ScanSource.FLATBED, ScanSource.FEEDER]


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
        scanner = ScannerInfo(name="s", vendor=None, model=None, backend="sane")
        doc = ScannedDocument(
            data=b"\x89PNG",
            width=100,
            height=200,
            dpi=300,
            color_mode=ColorMode.COLOR,
            scanner=scanner,
        )
        assert doc.data == b"\x89PNG"
        assert doc.width == 100
        assert doc.height == 200
        assert doc.dpi == 300
        assert doc.color_mode == ColorMode.COLOR
        assert doc.scanner is scanner


class TestScanAborted:
    def test_is_scanlib_error(self):
        assert issubclass(ScanAborted, ScanLibError)
