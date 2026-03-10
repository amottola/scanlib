import sys
from unittest import mock

import pytest

from scanlib._types import (
    ColorMode,
    ScanArea,
    ScanError,
    ScanOptions,
    ScanSource,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

# WIA property IDs used in tests
_WIA_DIP_DEV_ID = 2
_WIA_DIP_DEV_NAME = 7
_WIA_DIP_DEV_TYPE = 5
_WIA_DIP_VEND_DESC = 3


def _make_mock_enum(storages):
    """Create a mock IEnumWIA_DEV_INFO with GetCount/Next."""
    enum = mock.MagicMock()
    enum.GetCount.return_value = len(storages)
    returns = [(s, 1) for s in storages] + [(None, 0)]
    enum.Next.side_effect = returns
    return enum


def _make_mock_storage(props):
    """Create a mock IWiaPropertyStorage with a _test_props dict."""
    storage = mock.MagicMock()
    storage._test_props = props
    return storage


def _make_device_storages(devices):
    """Create mock storages for a list of (name, vendor, dev_type) tuples."""
    storages = []
    for i, (name, vendor, dev_type) in enumerate(devices):
        props = {
            _WIA_DIP_DEV_TYPE: dev_type,
            _WIA_DIP_DEV_ID: f"device_{i}",
            _WIA_DIP_DEV_NAME: name,
            _WIA_DIP_VEND_DESC: vendor,
        }
        storages.append(_make_mock_storage(props))
    return storages


def _read_prop_from_mock(storage, prop_id, default=None):
    """Side effect for _read_prop that reads from mock _test_props."""
    props = getattr(storage, "_test_props", {})
    return props.get(prop_id, default)


def _make_open_scanner_dm():
    """Create a mock device manager with root item and child item for open_scanner tests."""
    storages = _make_device_storages([("Scanner A", None, 1)])
    dm = mock.MagicMock()
    dm.EnumDeviceInfo.return_value = _make_mock_enum(storages)
    root_item = mock.MagicMock()
    dm.CreateDevice.return_value = root_item
    child_item = mock.MagicMock()
    enum_items = mock.MagicMock()
    enum_items.Next.return_value = (child_item, 1)
    root_item.EnumChildItems.return_value = enum_items
    return dm


_WIA_MODULE = "scanlib.backends._wia"


class TestWiaBackend:
    def _make_backend(self, dm):
        from scanlib.backends._wia import WiaBackend

        backend = WiaBackend()
        backend._create_device_manager = mock.MagicMock(return_value=dm)
        return backend

    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_list_scanners(self, _mock_rp):
        storages = _make_device_storages([
            ("Scanner A", "Vendor A", 1),
            ("Scanner B", None, 1),
        ])
        dm = mock.MagicMock()
        dm.EnumDeviceInfo.return_value = _make_mock_enum(storages)

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()

        assert len(scanners) == 2
        assert scanners[0].name == "Scanner A"
        assert scanners[0].vendor is None
        assert scanners[0].backend == "wia"
        assert scanners[1].name == "Scanner B"

    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_list_scanners_empty(self, _mock_rp):
        dm = mock.MagicMock()
        dm.EnumDeviceInfo.return_value = _make_mock_enum([])

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        assert scanners == []

    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_list_scanners_filters_non_scanners(self, _mock_rp):
        storages = _make_device_storages([
            ("Scanner A", None, 1),    # scanner (STI type 1)
            ("Camera X", None, 2),     # not a scanner
        ])
        dm = mock.MagicMock()
        dm.EnumDeviceInfo.return_value = _make_mock_enum(storages)

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()

        assert len(scanners) == 1
        assert scanners[0].name == "Scanner A"

    @mock.patch(f"{_WIA_MODULE}._read_wia_defaults", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_color_modes", return_value=[ColorMode.COLOR])
    @mock.patch(f"{_WIA_MODULE}._read_wia_resolutions", return_value=[300])
    @mock.patch(f"{_WIA_MODULE}._read_wia_max_scan_area", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_sources",
                return_value=[ScanSource.FLATBED, ScanSource.FEEDER])
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_sources(
        self, _rp, _src, _ps, _res, _cm, _def
    ):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert ScanSource.FLATBED in scanners[0]._sources
        assert ScanSource.FEEDER in scanners[0]._sources

    @mock.patch(f"{_WIA_MODULE}._read_wia_defaults", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_color_modes", return_value=[ColorMode.COLOR])
    @mock.patch(f"{_WIA_MODULE}._read_wia_resolutions", return_value=[300])
    @mock.patch(f"{_WIA_MODULE}._read_wia_max_scan_area",
                return_value=ScanArea(x=0, y=0, width=2159, height=2970))
    @mock.patch(f"{_WIA_MODULE}._read_wia_sources",
                return_value=[ScanSource.FLATBED])
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_max_scan_area(
        self, _rp, _src, _ps, _res, _cm, _def
    ):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        areas = scanners[0]._max_scan_areas
        assert ScanSource.FLATBED in areas
        assert areas[ScanSource.FLATBED].x == 0
        assert areas[ScanSource.FLATBED].y == 0
        assert areas[ScanSource.FLATBED].width == 2159
        assert areas[ScanSource.FLATBED].height == 2970

    @mock.patch(f"{_WIA_MODULE}._read_wia_defaults", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_color_modes", return_value=[ColorMode.COLOR])
    @mock.patch(f"{_WIA_MODULE}._read_wia_resolutions",
                return_value=[150, 300, 600])
    @mock.patch(f"{_WIA_MODULE}._read_wia_max_scan_area", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_sources",
                return_value=[ScanSource.FLATBED])
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_resolutions(
        self, _rp, _src, _ps, _res, _cm, _def
    ):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert scanners[0]._resolutions == [150, 300, 600]

    @mock.patch(f"{_WIA_MODULE}._read_wia_defaults", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_color_modes",
                return_value=[ColorMode.BW, ColorMode.GRAY, ColorMode.COLOR])
    @mock.patch(f"{_WIA_MODULE}._read_wia_resolutions", return_value=[300])
    @mock.patch(f"{_WIA_MODULE}._read_wia_max_scan_area", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_sources",
                return_value=[ScanSource.FLATBED])
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_color_modes(
        self, _rp, _src, _ps, _res, _cm, _def
    ):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        assert ColorMode.BW in scanners[0]._color_modes
        assert ColorMode.GRAY in scanners[0]._color_modes
        assert ColorMode.COLOR in scanners[0]._color_modes

    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_scan_pages_not_open_raises(self, _mock_rp):
        storages = _make_device_storages([("Scanner A", None, 1)])
        dm = mock.MagicMock()
        dm.EnumDeviceInfo.return_value = _make_mock_enum(storages)

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()

        with pytest.raises(ScanError, match="not open"):
            list(backend.scan_pages(scanners[0], ScanOptions()))
