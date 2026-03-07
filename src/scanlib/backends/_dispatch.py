"""Thread-safe dispatchers for backends with thread-affine event loops."""

from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .._types import Scanner, ScanOptions, ScannedPage


class ThreadDispatcher:
    """Wraps a backend so all operations execute on a dedicated worker thread.

    Used for TWAIN, whose hidden window handle is bound to the thread
    that created it.
    """

    def __init__(self, backend_cls: type) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(backend_cls,), daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def _run(self, backend_cls: type) -> None:
        self._backend = backend_cls()
        self._ready.set()
        while True:
            func, args, event, box = self._queue.get()
            try:
                box["value"] = func(*args)
            except BaseException as exc:
                box["error"] = exc
            event.set()

    def _dispatch(self, func, *args):
        event = threading.Event()
        box: dict = {}
        self._queue.put((func, args, event, box))
        event.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def list_scanners(self) -> list[Scanner]:
        scanners = self._dispatch(self._backend.list_scanners)
        for s in scanners:
            s._backend_impl = self
        return scanners

    def open_scanner(self, scanner: Scanner) -> None:
        return self._dispatch(self._backend.open_scanner, scanner)

    def close_scanner(self, scanner: Scanner) -> None:
        return self._dispatch(self._backend.close_scanner, scanner)

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> list[ScannedPage]:
        return self._dispatch(self._backend.scan_pages, scanner, options)


class RunLoopDispatcher(ThreadDispatcher):
    """Wraps a backend so all operations execute on a thread with an NSRunLoop.

    Used for macOS ImageCaptureCore, whose delegate callbacks are delivered
    to the run loop of the thread that created the device objects.
    """

    def _run(self, backend_cls: type) -> None:
        from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop

        self._backend = backend_cls()
        self._ready.set()

        run_loop = NSRunLoop.currentRunLoop()
        while True:
            try:
                func, args, event, box = self._queue.get(block=False)
                try:
                    box["value"] = func(*args)
                except BaseException as exc:
                    box["error"] = exc
                event.set()
            except queue.Empty:
                pass
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.05),
            )
