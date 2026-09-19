"""Regression tests for macOS main-thread dispatch (never block forever).

ImageCaptureCore delivers all of its callbacks to the main thread, so the
macOS backend dispatches its calls there.  That dispatch only completes
while the main thread is servicing an ``NSRunLoop`` — which a headless
process never does.  Every wait is therefore bounded: calls that used to
hang forever now raise ``MainThreadUnavailableError``.

pytest's main thread does not run a run loop, so these tests exercise the
broken-at-the-time scenario directly.  No scanner hardware is needed.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

pytest.importorskip("ImageCaptureCore")

from scanlib._types import MainThreadUnavailableError, ScanAborted  # noqa: E402


def _backend():
    from scanlib.backends._macos import MacOSBackend

    return MacOSBackend()


def _run_off_main(fn, join_timeout):
    """Run *fn* on a background thread; return (outcome, elapsed).

    The main thread deliberately does **not** pump a run loop while
    waiting — that is the condition under test.
    """
    box = {}

    def _target():
        t0 = time.monotonic()
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - recording it is the point
            box["error"] = exc
        box["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(join_timeout)
    return box, t


class TestBoundedDispatch:
    def test_list_scanners_off_main_thread_does_not_hang(self):
        """The reported bug: this used to block forever, ignoring timeout."""
        import scanlib

        box, thread = _run_off_main(lambda: scanlib.list_scanners(timeout=2), 20)

        assert not thread.is_alive(), (
            "list_scanners() never returned from a background thread while the "
            "main thread was not pumping a run loop"
        )
        # Either outcome satisfies the contract; what matters is that the
        # call completed within its budget instead of hanging.
        assert "result" in box or isinstance(
            box.get("error"), (MainThreadUnavailableError, ScanAborted)
        ), f"unexpected outcome: {box}"
        assert box["elapsed"] < 15

    def test_on_main_raises_when_main_thread_is_not_pumping(self, monkeypatch):
        from scanlib.backends import _macos

        monkeypatch.setattr(_macos, "MAIN_DISPATCH_TIMEOUT", 0.5)
        backend = _backend()

        box, thread = _run_off_main(lambda: backend._on_main(lambda: 42), 10)

        assert not thread.is_alive()
        assert isinstance(box.get("error"), MainThreadUnavailableError)
        assert box["elapsed"] < 5

    def test_on_main_honours_cancel(self, monkeypatch):
        from scanlib.backends import _macos

        monkeypatch.setattr(_macos, "MAIN_DISPATCH_TIMEOUT", 30.0)
        backend = _backend()
        cancel = threading.Event()

        def _call_with_cancel():
            _macos._dispatch_ctx.cancel = cancel
            try:
                return backend._on_main(lambda: 42)
            finally:
                _macos._dispatch_ctx.cancel = None

        threading.Timer(0.3, cancel.set).start()
        box, thread = _run_off_main(_call_with_cancel, 10)

        assert not thread.is_alive()
        assert isinstance(box.get("error"), ScanAborted)
        # Returned on cancellation, nowhere near the 30s dispatch budget.
        assert box["elapsed"] < 5

    def test_call_off_main_thread_propagates_worker_result(self):
        """_call must still work off the main thread when no dispatch is needed."""
        backend = _backend()

        box, thread = _run_off_main(
            lambda: backend._call(lambda a, b: a + b, 3, 7, timeout=5), 10
        )

        assert not thread.is_alive()
        assert box.get("result") == 10


class TestDispatchStillWorksWhenPumped:
    """The fix must not break the supported path: a pumping main thread."""

    def test_on_main_returns_value_while_main_thread_pumps(self):
        from scanlib.backends._macos import pump_run_loop

        backend = _backend()
        box = {}
        done = threading.Event()

        def _target():
            try:
                box["result"] = backend._on_main(lambda a: a * 2, 21)
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc
            done.set()

        threading.Thread(target=_target, daemon=True).start()

        deadline = time.monotonic() + 5
        while not done.is_set() and time.monotonic() < deadline:
            pump_run_loop(0.05)

        assert done.is_set(), "dispatch did not complete while the main thread pumped"
        assert box.get("result") == 42, box

    def test_on_main_runs_func_on_the_main_thread(self):
        from scanlib.backends._macos import pump_run_loop

        backend = _backend()
        box = {}
        done = threading.Event()

        def _target():
            try:
                box["result"] = backend._on_main(
                    lambda: threading.current_thread() is threading.main_thread()
                )
            finally:
                done.set()

        threading.Thread(target=_target, daemon=True).start()

        deadline = time.monotonic() + 5
        while not done.is_set() and time.monotonic() < deadline:
            pump_run_loop(0.05)

        assert box.get("result") is True

    def test_exceptions_propagate_from_the_main_thread(self):
        from scanlib.backends._macos import pump_run_loop

        backend = _backend()
        box = {}
        done = threading.Event()

        def _boom():
            raise ValueError("boom")

        def _target():
            try:
                backend._on_main(_boom)
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc
            finally:
                done.set()

        threading.Thread(target=_target, daemon=True).start()

        deadline = time.monotonic() + 5
        while not done.is_set() and time.monotonic() < deadline:
            pump_run_loop(0.05)

        assert isinstance(box.get("error"), ValueError)


class TestAbortDoesNotVetoTeardown:
    """abort() leaves _abort_event set until the next scan clears it.

    Session open/close must not treat that as a cancellation — aborting a
    scan and then closing is the normal teardown path, and a cancelled
    close would leak the device session.
    """

    def _scanner_with_abort_set(self):
        from scanlib._types import Scanner

        scanner = Scanner(
            name="s",
            vendor=None,
            model=None,
            backend="imagecapture",
            scanner_id="s",
        )
        scanner._abort_event.set()
        return scanner

    def test_close_runs_with_abort_event_set(self):
        backend = _backend()
        scanner = self._scanner_with_abort_set()
        calls = []
        backend._close_scanner_impl = lambda s: calls.append(s)

        box, thread = _run_off_main(lambda: backend.close_scanner(scanner), 10)

        assert not thread.is_alive()
        assert "error" not in box, box
        assert calls == [scanner]

    def test_open_runs_with_abort_event_set(self):
        backend = _backend()
        scanner = self._scanner_with_abort_set()
        calls = []
        backend._open_scanner_impl = lambda s: calls.append(s)
        backend._devices[scanner.id] = object()  # skip targeted discovery

        box, thread = _run_off_main(lambda: backend.open_scanner(scanner), 10)

        assert not thread.is_alive()
        assert "error" not in box, box
        assert calls == [scanner]
