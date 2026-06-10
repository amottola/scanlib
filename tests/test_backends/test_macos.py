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


class TestAwaitThread:
    def test_pumps_until_main_thread_worker_finishes(self):
        # On the main thread, await_thread pumps the run loop and returns
        # once the watched thread completes.  pytest runs on the main
        # thread, so this exercises the pumping branch.
        import threading

        from scanlib.backends._macos import await_thread

        done = threading.Event()
        t = threading.Thread(target=done.set)
        t.start()
        await_thread(t, timeout=2.0)
        assert done.is_set()
        assert not t.is_alive()

    def test_off_main_thread_joins(self):
        import threading

        from scanlib.backends._macos import await_thread

        ran = threading.Event()
        result = {}

        def caller():
            worker = threading.Thread(target=ran.set)
            worker.start()
            await_thread(worker, timeout=2.0)
            result["alive"] = worker.is_alive()

        c = threading.Thread(target=caller)
        c.start()
        c.join(5)
        assert ran.is_set()
        assert result.get("alive") is False
