import sys
from unittest import mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

ImageCaptureCore = pytest.importorskip("ImageCaptureCore")
from scanlib._types import ScanSource


class TestReadSourcesFromDevice:
    def test_flatbed_and_feeder(self):
        from scanlib.backends._macos import _read_sources_from_device

        device = mock.MagicMock()
        device.availableFunctionalUnitTypes.return_value = [
            ImageCaptureCore.ICScannerFunctionalUnitTypeFlatbed,
            ImageCaptureCore.ICScannerFunctionalUnitTypeDocumentFeeder,
        ]
        sources = _read_sources_from_device(device)

        assert ScanSource.FLATBED in sources
        assert ScanSource.FEEDER in sources

    def test_flatbed_only(self):
        from scanlib.backends._macos import _read_sources_from_device

        device = mock.MagicMock()
        device.availableFunctionalUnitTypes.return_value = [
            ImageCaptureCore.ICScannerFunctionalUnitTypeFlatbed,
        ]
        sources = _read_sources_from_device(device)

        assert sources == [ScanSource.FLATBED]

    def test_no_units(self):
        from scanlib.backends._macos import _read_sources_from_device

        device = mock.MagicMock()
        device.availableFunctionalUnitTypes.return_value = None
        sources = _read_sources_from_device(device)

        assert sources == []
