from unittest import mock

import pytest

from scanlib._types import (
    ColorMode,
    PageSize,
    ScanAborted,
    ScanError,
    Scanner,
    ScanOptions,
    ScanSource,
)


@pytest.fixture(autouse=True)
def mock_sane_module():
    """Provide a mock sane module so tests work on any platform."""
    mock_sane = mock.MagicMock()
    with mock.patch.dict("sys.modules", {"sane": mock_sane}):
        yield mock_sane


def _make_backend(mock_sane_module):
    from scanlib.backends._sane import SaneBackend
    return SaneBackend()


def _make_mock_img(width=50, height=50):
    img = mock.MagicMock()
    img.width = width
    img.height = height
    img.save = lambda buf, format: buf.write(b"\x89PNG\r\n\x1a\ndata")
    return img


def _open_scanner(backend, mock_sane_module, mock_dev, name="s:1"):
    """Helper: list scanners, then open the first one."""
    mock_sane_module.get_devices.return_value = [
        (name, "V", "M", "t"),
    ]
    mock_sane_module.open.return_value = mock_dev
    scanners = backend.list_scanners()
    scanner = scanners[0]
    backend.open_scanner(scanner)
    return scanner


class TestSaneBackend:
    def test_list_scanners(self, mock_sane_module):
        mock_sane_module.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]

        backend = _make_backend(mock_sane_module)
        scanners = backend.list_scanners()

        assert len(scanners) == 1
        assert scanners[0].name == "epson:usb:001"
        assert scanners[0].vendor == "Epson"
        assert scanners[0].model == "GT-S50"
        assert scanners[0].backend == "sane"
        mock_sane_module.open.assert_not_called()

    def test_list_scanners_empty(self, mock_sane_module):
        mock_sane_module.get_devices.return_value = []

        backend = _make_backend(mock_sane_module)
        scanners = backend.list_scanners()
        assert scanners == []

    def test_open_scanner_parses_sources(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.get_options.return_value = [
            ("source", "Source", "Scan source", 3, 0, 0, 0,
             ["Flatbed", "Automatic Document Feeder"]),
        ]
        mock_sane_module.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]
        mock_sane_module.open.return_value = mock_dev

        backend = _make_backend(mock_sane_module)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert ScanSource.FLATBED in scanners[0]._sources
        assert ScanSource.FEEDER in scanners[0]._sources

    def test_open_scanner_parses_max_page_sizes(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.get_options.return_value = [
            ("source", "Source", "Scan source", 3, 0, 0, 0,
             ["Flatbed"]),
            ("br_x", "Bottom-right x", "", 1, 3, 4, 5, (0.0, 215.9, 0.1)),
            ("br_y", "Bottom-right y", "", 1, 3, 4, 5, (0.0, 297.0, 0.1)),
        ]
        mock_sane_module.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]
        mock_sane_module.open.return_value = mock_dev

        backend = _make_backend(mock_sane_module)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        sizes = scanners[0]._max_page_sizes
        assert ScanSource.FLATBED in sizes
        assert sizes[ScanSource.FLATBED].width == 2159
        assert sizes[ScanSource.FLATBED].height == 2970

    def test_open_scanner_max_page_sizes_empty_without_options(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.get_options.return_value = []
        mock_sane_module.get_devices.return_value = [("s:1", "V", "M", "t")]
        mock_sane_module.open.return_value = mock_dev

        backend = _make_backend(mock_sane_module)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert scanners[0]._max_page_sizes == {}

    def test_close_scanner(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.get_options.return_value = []
        mock_sane_module.get_devices.return_value = [("s:1", "V", "M", "t")]
        mock_sane_module.open.return_value = mock_dev

        backend = _make_backend(mock_sane_module)
        scanners = backend.list_scanners()
        scanner = scanners[0]
        backend.open_scanner(scanner)
        backend.close_scanner(scanner)

        mock_dev.close.assert_called_once()

    def test_scan_pages_returns_single_page(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = _make_mock_img(100, 200)
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(dpi=300, color_mode=ColorMode.COLOR))

        assert len(pages) == 1
        assert pages[0].png_data.startswith(b"\x89PNG")
        assert pages[0].width == 100
        assert pages[0].height == 200

    def test_scan_pages_not_open_raises(self, mock_sane_module):
        mock_sane_module.get_devices.return_value = [("s:1", "V", "M", "t")]

        backend = _make_backend(mock_sane_module)
        scanners = backend.list_scanners()

        with pytest.raises(ScanError, match="not open"):
            backend.scan_pages(scanners[0], ScanOptions())

    def test_scan_pages_sets_color_mode(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = _make_mock_img()
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)
        backend.scan_pages(scanner, ScanOptions(dpi=600, color_mode=ColorMode.GRAY))

        assert mock_dev.mode == "gray"
        assert mock_dev.resolution == 600

    def test_scan_pages_sets_page_size(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = _make_mock_img()
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)
        backend.scan_pages(scanner, ScanOptions(page_size=PageSize(2100, 2970)))

        assert mock_dev.br_x == 210.0
        assert mock_dev.br_y == 297.0

    def test_scan_pages_sets_source(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.side_effect = [_make_mock_img(), Exception("out of documents")]
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)
        backend.scan_pages(scanner, ScanOptions(source=ScanSource.FEEDER))

        assert mock_dev.source == "Automatic Document Feeder"

    def test_scan_pages_no_page_size_skips_setting(self, mock_sane_module):
        mock_dev = mock.MagicMock(spec=["mode", "resolution", "scan", "close", "get_options", "source"])
        mock_dev.scan.return_value = _make_mock_img()
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)
        backend.scan_pages(scanner, ScanOptions())

        assert not hasattr(mock_dev, "br_x")
        assert not hasattr(mock_dev, "br_y")

    def test_scan_pages_abort_via_progress(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)

        with pytest.raises(ScanAborted):
            backend.scan_pages(scanner, ScanOptions(progress=lambda pct: False))

        mock_dev.scan.assert_not_called()
        mock_dev.cancel.assert_called()

    def test_scan_pages_progress_called(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = _make_mock_img()
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)

        calls = []
        backend.scan_pages(scanner, ScanOptions(progress=lambda pct: (calls.append(pct) or True)))

        assert 0 in calls
        assert 100 in calls

    def test_scan_pages_progress_none_return_continues(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = _make_mock_img()
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(progress=lambda pct: None))
        assert pages[0].png_data.startswith(b"\x89PNG")

    def test_scan_pages_hardware_cancel_raises_scan_aborted(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.side_effect = Exception("Operation was cancelled")
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)

        with pytest.raises(ScanAborted, match="cancelled by device"):
            backend.scan_pages(scanner, ScanOptions())

    def test_scan_pages_feeder_multi_page(self, mock_sane_module):
        """Feeder scanning: returns multiple pages, stops on 'no more' error."""
        img1 = _make_mock_img(100, 200)
        img2 = _make_mock_img(100, 200)
        img3 = _make_mock_img(100, 200)

        mock_dev = mock.MagicMock()
        mock_dev.scan.side_effect = [img1, img2, img3, Exception("out of documents")]
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(source=ScanSource.FEEDER))

        assert len(pages) == 3
        for p in pages:
            assert p.png_data.startswith(b"\x89PNG")

    def test_scan_pages_feeder_single_page(self, mock_sane_module):
        """Feeder with only one page: returns it, stops on empty error."""
        mock_dev = mock.MagicMock()
        mock_dev.scan.side_effect = [_make_mock_img(), Exception("no more documents")]
        mock_dev.get_options.return_value = []

        backend = _make_backend(mock_sane_module)
        scanner = _open_scanner(backend, mock_sane_module, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(source=ScanSource.FEEDER))
        assert len(pages) == 1
