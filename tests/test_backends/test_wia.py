from unittest import mock

import pytest

from scanlib._types import ColorMode, ScanError, Scanner, ScanOptions, ScanSource


def _make_mock_property(prop_id, value, sub_type=None, **kwargs):
    """Create a mock WIA property."""
    p = mock.MagicMock()
    p.PropertyID = prop_id
    p.Value = value
    p.SubType = sub_type
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


def _make_mock_properties(prop_list):
    """Create a mock WIA Properties collection (1-based indexing)."""
    props = mock.MagicMock()
    props.Count = len(prop_list)
    props.Item = mock.MagicMock(side_effect=lambda i: prop_list[i - 1])
    return props


@pytest.fixture(autouse=True)
def mock_comtypes():
    """Provide mock comtypes modules so tests work on any platform."""
    mock_ct = mock.MagicMock()
    mock_ct_client = mock.MagicMock()
    with mock.patch.dict("sys.modules", {
        "comtypes": mock_ct,
        "comtypes.client": mock_ct_client,
    }):
        yield mock_ct, mock_ct_client


def _make_device_info(name, vendor=None, dev_type=1):
    """Create a mock WIA DeviceInfo object."""
    name_prop = _make_mock_property(7, name)
    vendor_prop = _make_mock_property(3, vendor)
    props = _make_mock_properties([vendor_prop, name_prop])
    di = mock.MagicMock()
    di.Type = dev_type
    di.Properties = props
    return di


def _make_device_manager(device_infos):
    """Create a mock WIA DeviceManager with given DeviceInfo list."""
    dm = mock.MagicMock()
    dm.DeviceInfos.Count = len(device_infos)
    dm.DeviceInfos.Item = mock.MagicMock(
        side_effect=lambda i: device_infos[i - 1]
    )
    return dm


class TestWiaBackend:
    def _make_backend(self, dm):
        from scanlib.backends._wia import WiaBackend

        backend = WiaBackend()
        backend._create_device_manager = mock.MagicMock(return_value=dm)
        return backend

    def test_list_scanners(self):
        di_a = _make_device_info("Scanner A", "Vendor A")
        di_b = _make_device_info("Scanner B")
        dm = _make_device_manager([di_a, di_b])

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()

        assert len(scanners) == 2
        assert scanners[0].name == "Scanner A"
        assert scanners[0].backend == "wia"
        assert scanners[1].name == "Scanner B"

    def test_list_scanners_empty(self):
        dm = _make_device_manager([])

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        assert scanners == []

    def test_list_scanners_filters_non_scanners(self):
        di_scanner = _make_device_info("Scanner A", dev_type=1)
        di_camera = _make_device_info("Camera X", dev_type=2)
        dm = _make_device_manager([di_scanner, di_camera])

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()

        assert len(scanners) == 1
        assert scanners[0].name == "Scanner A"

    def test_open_scanner_queries_sources(self):
        di = _make_device_info("Scanner A")
        dm = _make_device_manager([di])

        # Device with flatbed + feeder capabilities
        mock_device = mock.MagicMock()
        caps_prop = _make_mock_property(3086, 0x003)  # FLAT | FEED
        max_h = _make_mock_property(3074, 8500)
        max_v = _make_mock_property(3075, 11690)
        mock_device.Properties = _make_mock_properties([caps_prop, max_h, max_v])
        mock_device.Items = mock.MagicMock(
            side_effect=lambda i: mock.MagicMock(
                Properties=_make_mock_properties([])
            )
        )
        di.Connect.return_value = mock_device

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert ScanSource.FLATBED in scanners[0]._sources
        assert ScanSource.FEEDER in scanners[0]._sources

    def test_open_scanner_queries_max_page_sizes(self):
        di = _make_device_info("Scanner A")
        dm = _make_device_manager([di])

        mock_device = mock.MagicMock()
        caps_prop = _make_mock_property(3086, 0x001)  # FLAT only
        max_h = _make_mock_property(3074, 8500)   # 8.5 inches
        max_v = _make_mock_property(3075, 11690)  # 11.69 inches
        mock_device.Properties = _make_mock_properties([caps_prop, max_h, max_v])
        mock_device.Items = mock.MagicMock(
            side_effect=lambda i: mock.MagicMock(
                Properties=_make_mock_properties([])
            )
        )
        di.Connect.return_value = mock_device

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        sizes = scanners[0]._max_page_sizes
        assert ScanSource.FLATBED in sizes
        assert sizes[ScanSource.FLATBED].width == 2159   # ceil(8500 * 0.254)
        assert sizes[ScanSource.FLATBED].height == 2970  # ceil(11690 * 0.254)

    def test_open_scanner_queries_resolutions(self):
        di = _make_device_info("Scanner A")
        dm = _make_device_manager([di])

        mock_device = mock.MagicMock()
        caps_prop = _make_mock_property(3086, 0x001)
        mock_device.Properties = _make_mock_properties([caps_prop])

        xres_prop = _make_mock_property(
            6147, 300, sub_type=2, SubTypeValues=[150, 300, 600]
        )
        mock_item = mock.MagicMock()
        mock_item.Properties = _make_mock_properties([xres_prop])
        mock_device.Items = mock.MagicMock(side_effect=lambda i: mock_item)
        di.Connect.return_value = mock_device

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert scanners[0]._resolutions == [150, 300, 600]

    def test_open_scanner_queries_color_modes(self):
        di = _make_device_info("Scanner A")
        dm = _make_device_manager([di])

        mock_device = mock.MagicMock()
        caps_prop = _make_mock_property(3086, 0x001)
        mock_device.Properties = _make_mock_properties([caps_prop])

        dt_prop = _make_mock_property(
            4103, 3, sub_type=2, SubTypeValues=[0, 2, 3]
        )
        mock_item = mock.MagicMock()
        mock_item.Properties = _make_mock_properties([dt_prop])
        mock_device.Items = mock.MagicMock(side_effect=lambda i: mock_item)
        di.Connect.return_value = mock_device

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert ColorMode.BW in scanners[0]._color_modes
        assert ColorMode.GRAY in scanners[0]._color_modes
        assert ColorMode.COLOR in scanners[0]._color_modes

    def test_scan_pages_not_open_raises(self):
        di = _make_device_info("Scanner A")
        dm = _make_device_manager([di])

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()

        with pytest.raises(ScanError, match="not open"):
            list(backend.scan_pages(scanners[0], ScanOptions()))
