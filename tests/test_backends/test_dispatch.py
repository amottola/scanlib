"""Tests for the thread dispatch mechanisms in macOS and WIA backends."""

from __future__ import annotations

import sys
import threading

import pytest


class TestWiaDispatch:
    """Test WiaBackend._dispatch runs functions on the worker thread."""

    @pytest.fixture(autouse=True)
    def mock_comtypes(self):
        from unittest import mock

        mock_ct = mock.MagicMock()
        mock_ct_client = mock.MagicMock()
        with mock.patch.dict("sys.modules", {
            "comtypes": mock_ct,
            "comtypes.client": mock_ct_client,
        }):
            yield

    def _make_backend(self):
        from scanlib.backends._wia import WiaBackend

        return WiaBackend()

    def test_dispatch_returns_value(self):
        backend = self._make_backend()
        result = backend._dispatch(lambda: 42)
        assert result == 42

    def test_dispatch_passes_arguments(self):
        backend = self._make_backend()
        result = backend._dispatch(lambda a, b: a + b, 3, 7)
        assert result == 10

    def test_dispatch_runs_on_worker_thread(self):
        backend = self._make_backend()
        caller_thread = threading.current_thread()
        exec_thread = backend._dispatch(lambda: threading.current_thread())
        assert exec_thread is not caller_thread
        assert exec_thread is backend._thread

    def test_dispatch_propagates_exception(self):
        backend = self._make_backend()
        with pytest.raises(ValueError, match="test error"):
            backend._dispatch(lambda: (_ for _ in ()).throw(ValueError("test error")))

    def test_dispatch_reusable_after_exception(self):
        backend = self._make_backend()

        def fail():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            backend._dispatch(fail)

        # Worker thread should still be alive and functional
        result = backend._dispatch(lambda: "ok")
        assert result == "ok"

    def test_dispatch_from_multiple_threads(self):
        backend = self._make_backend()
        results = [None] * 4
        errors = [None] * 4

        def call_from_thread(idx):
            try:
                results[idx] = backend._dispatch(lambda i=idx: i * 10)
            except Exception as exc:
                errors[idx] = exc

        threads = [threading.Thread(target=call_from_thread, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == [None] * 4
        assert sorted(results) == [0, 10, 20, 30]

    def test_all_dispatched_work_runs_on_same_worker(self):
        backend = self._make_backend()
        thread_ids = set()

        def record_thread():
            thread_ids.add(threading.current_thread().ident)

        for _ in range(5):
            backend._dispatch(record_thread)

        assert len(thread_ids) == 1
        assert thread_ids.pop() == backend._thread.ident


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
class TestMacOSDispatch:
    """Test MacOSBackend._call dispatches correctly by thread."""

    def _make_backend(self):
        from scanlib.backends._macos import MacOSBackend

        return MacOSBackend()

    def test_call_runs_directly_on_main_thread(self):
        """When called from the main thread, _call invokes directly."""
        assert threading.current_thread() is threading.main_thread()
        backend = self._make_backend()
        exec_thread = backend._call(lambda: threading.current_thread())
        assert exec_thread is threading.main_thread()

    def test_call_returns_value_on_main_thread(self):
        backend = self._make_backend()
        result = backend._call(lambda: 42)
        assert result == 42

    def test_call_passes_arguments_on_main_thread(self):
        backend = self._make_backend()
        result = backend._call(lambda a, b: a * b, 6, 7)
        assert result == 42

    def test_call_propagates_exception_on_main_thread(self):
        backend = self._make_backend()
        with pytest.raises(ValueError, match="test error"):
            backend._call(lambda: (_ for _ in ()).throw(ValueError("test error")))

    def test_lock_serialises_access(self):
        """Verify that the lock prevents concurrent access."""
        backend = self._make_backend()
        assert not backend._lock.locked()

        # Acquire the lock manually and verify _call blocks
        backend._lock.acquire()
        entered = threading.Event()
        result_box = {}

        def try_call():
            # This should block on the lock
            entered.set()
            result_box["value"] = backend._call(lambda: "done")

        t = threading.Thread(target=try_call)
        t.start()
        # Wait for the thread to start; it should be blocked on the lock
        entered.wait(timeout=2)
        # Give it a moment to actually hit the lock
        t.join(timeout=0.1)
        # Thread should still be alive (blocked)
        assert t.is_alive()

        # Release the lock — but the background thread will then try
        # performSelectorOnMainThread which needs a run loop. Since we're
        # testing the lock, just verify it was blocking and clean up.
        backend._lock.release()
        t.join(timeout=5)
