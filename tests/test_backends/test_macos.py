import sys
import threading
from unittest import mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

ImageCaptureCore = pytest.importorskip("ImageCaptureCore")
from scanlib._types import (
    ColorMode,
    FeederEmptyError,
    ScanError,
    ScanOptions,
    Scanner,
    ScanSource,
)


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


class _FakeDelegate:
    """Scripts a sequence of requestScan rounds for _collect_scan_rounds."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self._scan_done = threading.Event()
        self.error = None
        self.error_code = None
        self.completed_pages = []
        self._current_bands = []
        self._rows_received = 0
        self._last_pct = 0
        self._aborted = False
        self._expected_height = 0
        self._progress = None

    def _finish_page(self):
        self.completed_pages.append(self._current_bands)
        self._current_bands = []

    def run_round(self):
        # Invoked via device.requestScan; applies the next scripted round.
        rnd = self._rounds.pop(0)
        self.completed_pages = list(rnd.get("pages", []))
        self.error = rnd.get("error")
        self.error_code = rnd.get("error_code")
        self._scan_done.set()


# A scan page as _collect_scan_rounds expects it: (bands, w, h, bpc, nc, pdt).
_PAGE = ([], 8, 8, 8, 1, 0)


class TestCollectScanRounds:
    def _run(self, monkeypatch, rounds, source):
        from scanlib.backends import _macos
        from scanlib.backends._macos import MacOSBackend

        monkeypatch.setattr(
            _macos,
            "_assemble_image",
            lambda bands, w, h, bpc, nc, pdt: (b"", w, h, ColorMode.GRAY),
        )
        backend = MacOSBackend()
        backend._on_main = lambda func, *a: func(*a)

        delegate = _FakeDelegate(rounds)
        device = mock.MagicMock()
        device.requestScan = delegate.run_round

        scanner = Scanner("S", None, None, "imagecapture")
        options = ScanOptions(
            dpi=300, color_mode=ColorMode.GRAY, scan_area=None, source=source
        )
        return backend._collect_scan_rounds(
            scanner, device, delegate, options, source == ScanSource.FEEDER, 0
        )

    def test_feeder_all_pages_one_round_then_empty(self, monkeypatch):
        # macOS commonly delivers every sheet in one requestScan; the trailing
        # empty pass must end the run and return the pages, not raise.
        pages = self._run(
            monkeypatch,
            [{"pages": [_PAGE, _PAGE]}, {"pages": []}],
            ScanSource.FEEDER,
        )
        assert len(pages) == 2

    def test_feeder_one_page_per_round_then_empty(self, monkeypatch):
        pages = self._run(
            monkeypatch,
            [{"pages": [_PAGE]}, {"pages": [_PAGE]}, {"pages": []}],
            ScanSource.FEEDER,
        )
        assert len(pages) == 2

    def test_feeder_trailing_no_documents_error_is_ignored(self, monkeypatch):
        # The final pass reports "no documents in feeder" *after* a page —
        # that is end-of-feeder, not a failure.
        pages = self._run(
            monkeypatch,
            [{"pages": [_PAGE]}, {"pages": [], "error": "No documents in feeder"}],
            ScanSource.FEEDER,
        )
        assert len(pages) == 1

    def test_feeder_empty_from_start_raises(self, monkeypatch):
        with pytest.raises(FeederEmptyError):
            self._run(monkeypatch, [{"pages": []}], ScanSource.FEEDER)

    def test_feeder_cancel_code_raises_aborted(self, monkeypatch):
        # A cancel is detected by ICReturnScanOperationCanceled (-9924),
        # not by parsing the message text.
        from scanlib._types import ScanAborted

        with pytest.raises(ScanAborted):
            self._run(
                monkeypatch,
                [{"pages": [_PAGE], "error": "stopped", "error_code": -9924}],
                ScanSource.FEEDER,
            )

    def test_feeder_real_error_first_round_raises(self, monkeypatch):
        with pytest.raises(ScanError, match="Communication failure"):
            self._run(
                monkeypatch,
                [{"pages": [], "error": "Communication failure"}],
                ScanSource.FEEDER,
            )

    def test_flatbed_scans_single_round(self, monkeypatch):
        # Even with more rounds scripted, flatbed stops after one page.
        pages = self._run(
            monkeypatch,
            [{"pages": [_PAGE]}, {"pages": [_PAGE]}],
            ScanSource.FLATBED,
        )
        assert len(pages) == 1

    def test_flatbed_no_data_raises_scan_error(self, monkeypatch):
        # A flatbed round that yields no image data is a failure, not an
        # empty feeder.
        with pytest.raises(ScanError, match="no image data"):
            self._run(monkeypatch, [{"pages": []}], ScanSource.FLATBED)


class TestNameAndLocation:
    def _dev(self, name, location, transport):
        device = mock.MagicMock()
        device.name.return_value = name
        device.locationDescription.return_value = location
        device.transportType.return_value = transport
        return device

    def test_network_uses_bonjour_name_no_location(self):
        # Network scanner: locationDescription is the Bonjour name macOS
        # shows, not a physical location.
        from scanlib.backends._macos import _name_and_location

        dev = self._dev(
            "EPSON WF-C8690/C8610 Series",
            "PLC",
            ImageCaptureCore.ICTransportTypeTCPIP,
        )
        name, location = _name_and_location(dev)
        assert name == "PLC"
        assert location is None

    def test_network_falls_back_to_name_when_no_location(self):
        from scanlib.backends._macos import _name_and_location

        dev = self._dev(
            "EPSON AM-C6000 Series", None, ImageCaptureCore.ICTransportTypeTCPIP
        )
        name, location = _name_and_location(dev)
        assert name == "EPSON AM-C6000 Series"
        assert location is None

    def test_usb_keeps_bus_descriptor_as_location(self):
        # USB scanner: locationDescription is the bus descriptor ("USB"),
        # a genuine location distinct from the device name.
        from scanlib.backends._macos import _name_and_location

        dev = self._dev(
            "CanoScan LiDE 300", "USB", ImageCaptureCore.ICTransportTypeUSB
        )
        name, location = _name_and_location(dev)
        assert name == "CanoScan LiDE 300"
        assert location == "USB"

    def test_usb_drops_location_echoing_name(self):
        from scanlib.backends._macos import _name_and_location

        dev = self._dev("My Scanner", "My Scanner", ImageCaptureCore.ICTransportTypeUSB)
        name, location = _name_and_location(dev)
        assert name == "My Scanner"
        assert location is None


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
