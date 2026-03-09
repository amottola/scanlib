"""scanlib — A multiplatform document scanning library for Python."""

from __future__ import annotations

import sys

from ._types import (
    DISCOVERY_TIMEOUT,
    BackendNotAvailableError,
    ColorMode,
    ImageFormat,
    NoScannerFoundError,
    PageSize,
    ScanAborted,
    ScanBackend,
    ScanError,
    ScanLibError,
    Scanner,
    ScannerDefaults,
    ScannerNotOpenError,
    ScanOptions,
    ScanSource,
    ScannedDocument,
)

__all__ = [
    "list_scanners",
    "DISCOVERY_TIMEOUT",
    "ColorMode",
    "ImageFormat",
    "PageSize",
    "Scanner",
    "ScannerDefaults",
    "ScannerNotOpenError",
    "ScanOptions",
    "ScanSource",
    "ScannedDocument",
    "ScanLibError",
    "ScanError",
    "ScanAborted",
    "NoScannerFoundError",
    "BackendNotAvailableError",
]

_backend: ScanBackend | None = None


def _get_backend() -> ScanBackend:
    """Return the appropriate scanning backend for the current platform."""
    global _backend
    if _backend is not None:
        return _backend

    if sys.platform == "linux":
        from .backends._sane import SaneBackend

        _backend = SaneBackend()

    elif sys.platform == "darwin":
        from .backends._macos import MacOSBackend

        _backend = MacOSBackend()

    elif sys.platform == "win32":
        from .backends._twain import TwainBackend

        _backend = TwainBackend()

    else:
        raise BackendNotAvailableError(f"Unsupported platform: {sys.platform}")

    return _backend


def list_scanners(*, timeout: float = DISCOVERY_TIMEOUT) -> list[Scanner]:
    """Return all available scanners on the current platform.

    *timeout* controls how long (in seconds) to wait for scanner
    discovery.  The default is :data:`DISCOVERY_TIMEOUT` (15 s).

    The returned :class:`Scanner` objects are lightweight — no device sessions
    are opened.  Use :meth:`Scanner.open` (or the context-manager protocol)
    to start a session before scanning.
    """
    return _get_backend().list_scanners(timeout=timeout)
