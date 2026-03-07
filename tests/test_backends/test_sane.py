from types import SimpleNamespace
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
def mock_sane():
    """Patch SANE entry-point functions so tests work on any platform."""
    with mock.patch("scanlib.backends._sane._init"), \
         mock.patch("scanlib.backends._sane._get_devices") as m_get_devices, \
         mock.patch("scanlib.backends._sane._open_device") as m_open:
        yield SimpleNamespace(get_devices=m_get_devices, open=m_open)


def _make_backend():
    from scanlib.backends._sane import SaneBackend
    return SaneBackend()


def _make_gray_pixel_data(width, height, value=128):
    """Create raw grayscale pixel data (1 byte per pixel)."""
    return bytes([value] * (width * height))


def _make_rgb_pixel_data(width, height, r=128, g=128, b=128):
    """Create raw RGB pixel data (3 bytes per pixel)."""
    return bytes([r, g, b] * (width * height))


def _make_mock_dev(options=None):
    """Create a mock SaneDevice with standard defaults."""
    dev = mock.MagicMock()
    dev.get_options.return_value = options or []
    return dev


def _setup_scan(dev, width, height, pixel_data, frame=1, depth=8):
    """Configure a mock device to return scan data."""
    from scanlib.backends._sane import Parameters
    dev.start.return_value = 0  # GOOD
    dev.get_parameters.return_value = Parameters(
        format=frame,
        last_frame=True,
        bytes_per_line=width * (3 if frame == 1 else 1) if depth == 8 else (width + 7) // 8,
        pixels_per_line=width,
        lines=height,
        depth=depth,
    )
    # Return all data in one chunk, then EOF
    dev.read.side_effect = [
        (pixel_data, 0),   # GOOD
        (b"", 5),          # EOF
    ]


def _open_scanner(backend, mock_sane, mock_dev, name="s:1"):
    """Helper: list scanners, then open the first one."""
    mock_sane.get_devices.return_value = [
        (name, "V", "M", "t"),
    ]
    mock_sane.open.return_value = mock_dev
    scanners = backend.list_scanners()
    scanner = scanners[0]
    backend.open_scanner(scanner)
    return scanner


class TestSaneBackend:
    def test_list_scanners(self, mock_sane):
        mock_sane.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]

        backend = _make_backend()
        scanners = backend.list_scanners()

        assert len(scanners) == 1
        assert scanners[0].name == "epson:usb:001"
        assert scanners[0].vendor == "Epson"
        assert scanners[0].model == "GT-S50"
        assert scanners[0].backend == "sane"

    def test_list_scanners_empty(self, mock_sane):
        mock_sane.get_devices.return_value = []

        backend = _make_backend()
        scanners = backend.list_scanners()
        assert scanners == []

    def test_open_scanner_parses_sources(self, mock_sane):
        mock_dev = _make_mock_dev([
            ("source", "Source", "Scan source", 3, 0, 0, 0,
             ["Flatbed", "Automatic Document Feeder"]),
        ])
        mock_sane.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]
        mock_sane.open.return_value = mock_dev

        backend = _make_backend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert ScanSource.FLATBED in scanners[0]._sources
        assert ScanSource.FEEDER in scanners[0]._sources

    def test_open_scanner_parses_max_page_sizes(self, mock_sane):
        mock_dev = _make_mock_dev([
            ("source", "Source", "Scan source", 3, 0, 0, 0,
             ["Flatbed"]),
            ("br_x", "Bottom-right x", "", 1, 3, 4, 5, (0.0, 215.9, 0.1)),
            ("br_y", "Bottom-right y", "", 1, 3, 4, 5, (0.0, 297.0, 0.1)),
        ])
        mock_sane.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]
        mock_sane.open.return_value = mock_dev

        backend = _make_backend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        sizes = scanners[0]._max_page_sizes
        assert ScanSource.FLATBED in sizes
        assert sizes[ScanSource.FLATBED].width == 2159
        assert sizes[ScanSource.FLATBED].height == 2970

    def test_open_scanner_max_page_sizes_empty_without_options(self, mock_sane):
        mock_dev = _make_mock_dev()
        mock_sane.get_devices.return_value = [("s:1", "V", "M", "t")]
        mock_sane.open.return_value = mock_dev

        backend = _make_backend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert scanners[0]._max_page_sizes == {}

    def test_close_scanner(self, mock_sane):
        mock_dev = _make_mock_dev()
        mock_sane.get_devices.return_value = [("s:1", "V", "M", "t")]
        mock_sane.open.return_value = mock_dev

        backend = _make_backend()
        scanners = backend.list_scanners()
        scanner = scanners[0]
        backend.open_scanner(scanner)
        backend.close_scanner(scanner)

        mock_dev.close.assert_called_once()

    def test_scan_pages_returns_single_page(self, mock_sane):
        mock_dev = _make_mock_dev()
        pixel_data = _make_rgb_pixel_data(100, 200)
        _setup_scan(mock_dev, 100, 200, pixel_data, frame=1)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(dpi=300, color_mode=ColorMode.COLOR))

        assert len(pages) == 1
        assert pages[0].png_data.startswith(b"\x89PNG")
        assert pages[0].width == 100
        assert pages[0].height == 200

    def test_scan_pages_not_open_raises(self, mock_sane):
        mock_sane.get_devices.return_value = [("s:1", "V", "M", "t")]

        backend = _make_backend()
        scanners = backend.list_scanners()

        with pytest.raises(ScanError, match="not open"):
            backend.scan_pages(scanners[0], ScanOptions())

    def test_scan_pages_sets_options(self, mock_sane):
        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(50, 50)
        _setup_scan(mock_dev, 50, 50, pixel_data, frame=0)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)
        backend.scan_pages(scanner, ScanOptions(dpi=600, color_mode=ColorMode.GRAY))

        calls = {c.args[0]: c.args[1] for c in mock_dev.set_option.call_args_list}
        assert calls["mode"] == "gray"
        assert calls["resolution"] == 600

    def test_scan_pages_sets_page_size(self, mock_sane):
        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(50, 50)
        _setup_scan(mock_dev, 50, 50, pixel_data, frame=0)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)
        backend.scan_pages(scanner, ScanOptions(page_size=PageSize(2100, 2970)))

        calls = {c.args[0]: c.args[1] for c in mock_dev.set_option.call_args_list}
        assert calls["br-x"] == 210.0
        assert calls["br-y"] == 297.0

    def test_scan_pages_sets_source(self, mock_sane):
        from scanlib.backends._sane import Parameters

        mock_dev = _make_mock_dev()
        mock_dev.start.side_effect = [0, 7]  # GOOD, NO_DOCS
        mock_dev.get_parameters.return_value = Parameters(
            format=0, last_frame=True, bytes_per_line=50,
            pixels_per_line=50, lines=50, depth=8,
        )
        pixel_data = _make_gray_pixel_data(50, 50)
        mock_dev.read.side_effect = [
            (pixel_data, 0), (b"", 5),  # first page
        ]

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)
        backend.scan_pages(scanner, ScanOptions(source=ScanSource.FEEDER))

        calls = {c.args[0]: c.args[1] for c in mock_dev.set_option.call_args_list}
        assert calls["source"] == "Automatic Document Feeder"

    def test_scan_pages_no_page_size_skips_setting(self, mock_sane):
        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(50, 50)
        _setup_scan(mock_dev, 50, 50, pixel_data, frame=0)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)
        backend.scan_pages(scanner, ScanOptions())

        option_names = [c.args[0] for c in mock_dev.set_option.call_args_list]
        assert "br-x" not in option_names
        assert "br-y" not in option_names

    def test_scan_pages_abort_via_progress(self, mock_sane):
        mock_dev = _make_mock_dev()

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        with pytest.raises(ScanAborted):
            backend.scan_pages(scanner, ScanOptions(progress=lambda pct: False))

        mock_dev.start.assert_not_called()
        mock_dev.cancel.assert_called()

    def test_scan_pages_progress_called(self, mock_sane):
        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(50, 50)
        _setup_scan(mock_dev, 50, 50, pixel_data, frame=0)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        calls = []
        backend.scan_pages(scanner, ScanOptions(progress=lambda pct: (calls.append(pct) or True)))

        assert 0 in calls
        assert 100 in calls

    def test_scan_pages_progress_none_return_continues(self, mock_sane):
        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(50, 50)
        _setup_scan(mock_dev, 50, 50, pixel_data, frame=0)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(progress=lambda pct: None))
        assert pages[0].png_data.startswith(b"\x89PNG")

    def test_scan_pages_hardware_cancel_raises_scan_aborted(self, mock_sane):
        mock_dev = _make_mock_dev()
        mock_dev.start.return_value = 2  # CANCELLED

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        with pytest.raises(ScanAborted, match="cancelled"):
            backend.scan_pages(scanner, ScanOptions())

    def test_scan_pages_feeder_multi_page(self, mock_sane):
        """Feeder scanning: returns multiple pages, stops on no_docs."""
        from scanlib.backends._sane import Parameters

        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(50, 50)
        params = Parameters(
            format=0, last_frame=True, bytes_per_line=50,
            pixels_per_line=50, lines=50, depth=8,
        )

        mock_dev.start.side_effect = [0, 0, 0, 7]
        mock_dev.get_parameters.return_value = params
        mock_dev.read.side_effect = [
            (pixel_data, 0), (b"", 5),  # page 1
            (pixel_data, 0), (b"", 5),  # page 2
            (pixel_data, 0), (b"", 5),  # page 3
        ]

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(source=ScanSource.FEEDER))

        assert len(pages) == 3
        for p in pages:
            assert p.png_data.startswith(b"\x89PNG")

    def test_scan_pages_feeder_single_page(self, mock_sane):
        """Feeder with only one page: returns it, stops on no_docs."""
        from scanlib.backends._sane import Parameters

        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(50, 50)
        params = Parameters(
            format=0, last_frame=True, bytes_per_line=50,
            pixels_per_line=50, lines=50, depth=8,
        )

        mock_dev.start.side_effect = [0, 7]  # GOOD, NO_DOCS
        mock_dev.get_parameters.return_value = params
        mock_dev.read.side_effect = [
            (pixel_data, 0), (b"", 5),  # page 1
        ]

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(source=ScanSource.FEEDER))
        assert len(pages) == 1

    def test_scan_pages_gray_mode_converts_rgb(self, mock_sane):
        """Scanning in GRAY mode with RGB input converts to grayscale."""
        mock_dev = _make_mock_dev()
        pixel_data = _make_rgb_pixel_data(4, 2, r=255, g=255, b=255)
        _setup_scan(mock_dev, 4, 2, pixel_data, frame=1)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(color_mode=ColorMode.GRAY))
        assert len(pages) == 1
        assert pages[0].png_data.startswith(b"\x89PNG")
        assert pages[0].width == 4
        assert pages[0].height == 2

    def test_scan_pages_bw_mode_converts(self, mock_sane):
        """Scanning in BW mode produces 1-bit output."""
        mock_dev = _make_mock_dev()
        pixel_data = _make_gray_pixel_data(8, 2, value=200)
        _setup_scan(mock_dev, 8, 2, pixel_data, frame=0)

        backend = _make_backend()
        scanner = _open_scanner(backend, mock_sane, mock_dev)

        pages = backend.scan_pages(scanner, ScanOptions(color_mode=ColorMode.BW))
        assert len(pages) == 1
        assert pages[0].png_data.startswith(b"\x89PNG")

    def test_open_scanner_populates_defaults(self, mock_sane):
        """Defaults are read from device options after open."""
        mock_dev = _make_mock_dev([
            ("source", "Source", "", 3, 0, 0, 0,
             ["Flatbed", "Automatic Document Feeder"]),
            ("mode", "Mode", "", 3, 0, 0, 0,
             ["color", "gray", "lineart"]),
            ("resolution", "Resolution", "", 1, 4, 0, 0,
             [75, 150, 300, 600, 1200]),
        ])
        mock_dev.get_option.side_effect = lambda name: {
            "resolution": 300,
            "mode": "color",
            "source": "Flatbed",
        }[name]

        mock_sane.get_devices.return_value = [
            ("epson:usb:001", "Epson", "GT-S50", "flatbed scanner"),
        ]
        mock_sane.open.return_value = mock_dev

        backend = _make_backend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        defaults = scanners[0]._defaults
        assert defaults is not None
        assert defaults.dpi == 300
        assert defaults.color_mode == ColorMode.COLOR
        assert defaults.source == ScanSource.FLATBED

        assert 300 in scanners[0]._resolutions
        assert 600 in scanners[0]._resolutions
        assert ColorMode.COLOR in scanners[0]._color_modes
        assert ColorMode.GRAY in scanners[0]._color_modes
        assert ColorMode.BW in scanners[0]._color_modes

    def test_open_scanner_resolutions_from_range(self, mock_sane):
        """Resolution constraint as range tuple produces a list."""
        mock_dev = _make_mock_dev([
            ("resolution", "Resolution", "", 1, 4, 0, 0, (75, 1200, 75)),
        ])
        mock_dev.get_option.side_effect = lambda name: {
            "resolution": 300,
            "mode": "color",
        }.get(name)

        mock_sane.get_devices.return_value = [("s:1", "V", "M", "t")]
        mock_sane.open.return_value = mock_dev

        backend = _make_backend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert 75 in scanners[0]._resolutions
        assert 300 in scanners[0]._resolutions
        assert 1200 in scanners[0]._resolutions

    def test_open_scanner_defaults_none_on_failure(self, mock_sane):
        """Defaults gracefully return None if get_option fails entirely."""
        mock_dev = _make_mock_dev()
        mock_dev.get_option.side_effect = Exception("not supported")

        mock_sane.get_devices.return_value = [("s:1", "V", "M", "t")]
        mock_sane.open.return_value = mock_dev

        backend = _make_backend()
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        # Should still produce defaults (with fallback values), not crash
        defaults = scanners[0]._defaults
        assert defaults is not None
        assert defaults.dpi == 300
