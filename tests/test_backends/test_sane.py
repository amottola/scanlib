from unittest import mock

import pytest

from scanlib._types import ColorMode, PageSize, ScanAborted, ScanOptions, ScanSource


@pytest.fixture(autouse=True)
def mock_sane_module():
    """Provide a mock sane module so tests work on any platform."""
    mock_sane = mock.MagicMock()
    with mock.patch.dict("sys.modules", {"sane": mock_sane}):
        yield mock_sane


class TestSaneBackend:
    def test_list_scanners(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.get_options.return_value = [
            ("source", "Source", "Scan source", 3, 0, 0, 0, ["Flatbed", "Automatic Document Feeder"]),
        ]
        mock_sane_module.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]
        mock_sane_module.open.return_value = mock_dev

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        scanners = backend.list_scanners()

        assert len(scanners) == 1
        assert scanners[0].name == "epson:usb:001"
        assert scanners[0].vendor == "Epson"
        assert scanners[0].model == "GT-S50"
        assert scanners[0].backend == "sane"
        assert ScanSource.FLATBED in scanners[0].sources
        assert ScanSource.FEEDER in scanners[0].sources

    def test_list_scanners_empty(self, mock_sane_module):
        mock_sane_module.get_devices.return_value = []

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        scanners = backend.list_scanners()
        assert scanners == []

    def test_scan_returns_document(self, mock_sane_module):
        mock_img = mock.MagicMock()
        mock_img.width = 100
        mock_img.height = 200

        def fake_save(buf, format):
            buf.write(b"\x89PNG\r\n\x1a\nfakedata")

        mock_img.save = fake_save

        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = mock_img
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("scanner:1", "Vendor", "Model", "type"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        doc = backend.scan(None, ScanOptions(dpi=300, color_mode=ColorMode.COLOR))

        assert doc.data.startswith(b"\x89PNG")
        assert doc.width == 100
        assert doc.height == 200
        assert doc.dpi == 300
        assert doc.color_mode == ColorMode.COLOR
        assert doc.scanner.name == "scanner:1"

        mock_dev.close.assert_called()

    def test_scan_no_scanners(self, mock_sane_module):
        mock_sane_module.get_devices.return_value = []

        from scanlib.backends._sane import SaneBackend
        from scanlib._types import NoScannerFoundError

        backend = SaneBackend()
        with pytest.raises(NoScannerFoundError):
            backend.scan(None, ScanOptions())

    def test_scan_sets_color_mode(self, mock_sane_module):
        mock_img = mock.MagicMock()
        mock_img.width = 50
        mock_img.height = 50
        mock_img.save = lambda buf, format: buf.write(b"\x89PNG\r\n\x1a\ndata")

        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = mock_img
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        backend.scan(None, ScanOptions(dpi=600, color_mode=ColorMode.GRAY))

        assert mock_dev.mode == "gray"
        assert mock_dev.resolution == 600

    def test_scan_sets_page_size(self, mock_sane_module):
        mock_img = mock.MagicMock()
        mock_img.width = 50
        mock_img.height = 50
        mock_img.save = lambda buf, format: buf.write(b"\x89PNG\r\n\x1a\ndata")

        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = mock_img
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        backend.scan(None, ScanOptions(page_size=PageSize(2100, 2970)))

        assert mock_dev.br_x == 210.0
        assert mock_dev.br_y == 297.0

    def test_scan_sets_source(self, mock_sane_module):
        mock_img = mock.MagicMock()
        mock_img.width = 50
        mock_img.height = 50
        mock_img.save = lambda buf, format: buf.write(b"\x89PNG\r\n\x1a\ndata")

        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = mock_img
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        backend.scan(None, ScanOptions(source=ScanSource.FEEDER))

        assert mock_dev.source == "Automatic Document Feeder"

    def test_scan_no_page_size_skips_setting(self, mock_sane_module):
        mock_img = mock.MagicMock()
        mock_img.width = 50
        mock_img.height = 50
        mock_img.save = lambda buf, format: buf.write(b"\x89PNG\r\n\x1a\ndata")

        mock_dev = mock.MagicMock(spec=["mode", "resolution", "scan", "close", "get_options", "source"])
        mock_dev.scan.return_value = mock_img
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        backend.scan(None, ScanOptions())

        # br_x and br_y should not be set when page_size is None
        assert not hasattr(mock_dev, "br_x")
        assert not hasattr(mock_dev, "br_y")

    def test_scan_abort_via_progress(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        with pytest.raises(ScanAborted):
            backend.scan(None, ScanOptions(progress=lambda pct: False))

        mock_dev.scan.assert_not_called()
        mock_dev.cancel.assert_called()
        mock_dev.close.assert_called()

    def test_scan_progress_called(self, mock_sane_module):
        mock_img = mock.MagicMock()
        mock_img.width = 50
        mock_img.height = 50
        mock_img.save = lambda buf, format: buf.write(b"\x89PNG\r\n\x1a\ndata")

        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = mock_img
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        calls = []
        backend = SaneBackend()
        backend.scan(None, ScanOptions(progress=lambda pct: (calls.append(pct) or True)))

        assert 0 in calls
        assert 100 in calls

    def test_scan_progress_none_return_continues(self, mock_sane_module):
        mock_img = mock.MagicMock()
        mock_img.width = 50
        mock_img.height = 50
        mock_img.save = lambda buf, format: buf.write(b"\x89PNG\r\n\x1a\ndata")

        mock_dev = mock.MagicMock()
        mock_dev.scan.return_value = mock_img
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        # Returning None should not abort
        doc = backend.scan(None, ScanOptions(progress=lambda pct: None))
        assert doc.data.startswith(b"\x89PNG")

    def test_scan_hardware_cancel_raises_scan_aborted(self, mock_sane_module):
        mock_dev = mock.MagicMock()
        mock_dev.scan.side_effect = Exception("Operation was cancelled")
        mock_dev.get_options.return_value = []
        mock_sane_module.open.return_value = mock_dev
        mock_sane_module.get_devices.return_value = [
            ("s:1", "V", "M", "t"),
        ]

        from scanlib.backends._sane import SaneBackend

        backend = SaneBackend()
        with pytest.raises(ScanAborted, match="cancelled by device"):
            backend.scan(None, ScanOptions())
