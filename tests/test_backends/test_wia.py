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
        storages = _make_device_storages(
            [
                ("Scanner A", "Vendor A", 1),
                ("Scanner B", None, 1),
            ]
        )
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
        storages = _make_device_storages(
            [
                ("Scanner A", None, 1),  # scanner (STI type 1)
                ("Camera X", None, 2),  # not a scanner
            ]
        )
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
    @mock.patch(
        f"{_WIA_MODULE}._read_wia_sources",
        return_value=[ScanSource.FLATBED, ScanSource.FEEDER],
    )
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_sources(self, _rp, _src, _ps, _res, _cm, _def):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        source_types = [si.type for si in scanners[0]._sources]
        assert ScanSource.FLATBED in source_types
        assert ScanSource.FEEDER in source_types

    @mock.patch(f"{_WIA_MODULE}._read_wia_defaults", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_color_modes", return_value=[ColorMode.COLOR])
    @mock.patch(f"{_WIA_MODULE}._read_wia_resolutions", return_value=[300])
    @mock.patch(
        f"{_WIA_MODULE}._read_wia_max_scan_area",
        return_value=ScanArea(x=0, y=0, width=2159, height=2970),
    )
    @mock.patch(f"{_WIA_MODULE}._read_wia_sources", return_value=[ScanSource.FLATBED])
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_max_scan_area(self, _rp, _src, _ps, _res, _cm, _def):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        si = next(s for s in scanners[0]._sources if s.type == ScanSource.FLATBED)
        assert si.max_scan_area is not None
        assert si.max_scan_area.x == 0
        assert si.max_scan_area.y == 0
        assert si.max_scan_area.width == 2159
        assert si.max_scan_area.height == 2970

    @mock.patch(f"{_WIA_MODULE}._read_wia_defaults", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_color_modes", return_value=[ColorMode.COLOR])
    @mock.patch(f"{_WIA_MODULE}._read_wia_resolutions", return_value=[150, 300, 600])
    @mock.patch(f"{_WIA_MODULE}._read_wia_max_scan_area", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_sources", return_value=[ScanSource.FLATBED])
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_resolutions(self, _rp, _src, _ps, _res, _cm, _def):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        si = scanners[0]._sources[0]
        assert si.resolutions == [150, 300, 600]

    @mock.patch(f"{_WIA_MODULE}._read_wia_defaults", return_value=None)
    @mock.patch(
        f"{_WIA_MODULE}._read_wia_color_modes",
        return_value=[ColorMode.BW, ColorMode.GRAY, ColorMode.COLOR],
    )
    @mock.patch(f"{_WIA_MODULE}._read_wia_resolutions", return_value=[300])
    @mock.patch(f"{_WIA_MODULE}._read_wia_max_scan_area", return_value=None)
    @mock.patch(f"{_WIA_MODULE}._read_wia_sources", return_value=[ScanSource.FLATBED])
    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_open_scanner_queries_color_modes(self, _rp, _src, _ps, _res, _cm, _def):
        dm = _make_open_scanner_dm()
        backend = self._make_backend(dm)
        scanners = backend.list_scanners()
        backend.open_scanner(scanners[0])

        si = scanners[0]._sources[0]
        assert ColorMode.BW in si.color_modes
        assert ColorMode.GRAY in si.color_modes
        assert ColorMode.COLOR in si.color_modes

    @mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=_read_prop_from_mock)
    def test_scan_pages_not_open_raises(self, _mock_rp):
        storages = _make_device_storages([("Scanner A", None, 1)])
        dm = mock.MagicMock()
        dm.EnumDeviceInfo.return_value = _make_mock_enum(storages)

        backend = self._make_backend(dm)
        scanners = backend.list_scanners()

        with pytest.raises(ScanError, match="not open"):
            list(backend.scan_pages(scanners[0], ScanOptions()))


class TestReadWiaMaxScanArea:
    """Unit tests for _read_wia_max_scan_area fallback chain."""

    def _get_fn(self):
        from scanlib.backends._wia import _read_wia_max_scan_area

        return _read_wia_max_scan_area

    def test_item_level_properties_preferred(self):
        """WIA 2.0 item-level props (6165/6166) take priority."""
        fn = self._get_fn()
        root = mock.MagicMock()
        item = mock.MagicMock()

        def item_read(storage, prop_id, default=None):
            # WIA_IPS_MAX_HORIZONTAL_SIZE / VERTICAL_SIZE
            if prop_id == 6165:
                return 8500  # thousandths of inch
            if prop_id == 6166:
                return 11000
            return default

        with mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=item_read):
            area = fn(root, item)

        assert area is not None
        assert area.x == 0
        assert area.y == 0
        assert area.width > 0
        assert area.height > 0

    def test_device_level_fallback(self):
        """Falls back to WIA 1.0 device-level props (3074/3075)."""
        fn = self._get_fn()
        root = mock.MagicMock()
        item = mock.MagicMock()

        def read_prop(storage, prop_id, default=None):
            # Item-level returns None; device-level returns values
            if prop_id == 3074:
                return 8500
            if prop_id == 3075:
                return 11693
            return default

        with mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=read_prop):
            with mock.patch(
                f"{_WIA_MODULE}._read_prop_attributes", return_value=(0, [])
            ):
                area = fn(root, item)

        assert area is not None
        assert area.width > 0
        assert area.height > 0

    def test_extent_derived_fallback(self):
        """Derives area from XEXTENT/YEXTENT range + resolution."""
        fn = self._get_fn()
        root = mock.MagicMock()
        item = mock.MagicMock()

        _WIA_IPS_XRES = 6147
        _WIA_IPS_XEXTENT = 6151
        _WIA_IPS_YEXTENT = 6152
        _WIA_PROP_RANGE = 0x10

        def read_prop(storage, prop_id, default=None):
            if prop_id == _WIA_IPS_XRES:
                return 300
            if prop_id == 6148:  # YRES
                return 300
            return default

        def read_attrs(storage, prop_id):
            if prop_id == _WIA_IPS_XEXTENT:
                return (_WIA_PROP_RANGE, [1, 2550, 1])  # min, max, step
            if prop_id == _WIA_IPS_YEXTENT:
                return (_WIA_PROP_RANGE, [1, 3510, 1])
            return (0, [])

        with mock.patch(f"{_WIA_MODULE}._read_prop", side_effect=read_prop):
            with mock.patch(
                f"{_WIA_MODULE}._read_prop_attributes", side_effect=read_attrs
            ):
                area = fn(root, item)

        assert area is not None
        # 2550px / 300dpi * 254 = 2159
        assert area.width == 2159
        # 3510px / 300dpi * 254 = 2972 (ceil)
        assert area.height == 2972

    def test_fallback_to_letter_a4_bounding_box(self):
        """Returns Letter/A4 bounding box when no properties available."""
        fn = self._get_fn()

        with mock.patch(f"{_WIA_MODULE}._read_prop", return_value=None):
            with mock.patch(
                f"{_WIA_MODULE}._read_prop_attributes", return_value=(0, [])
            ):
                area = fn(None, None)

        assert area == ScanArea(x=0, y=0, width=2159, height=2970)
