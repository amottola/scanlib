"""scanlib — A multiplatform document scanning library for Python."""

from __future__ import annotations

import sys
from collections.abc import Callable

from ._types import (
    BackendNotAvailableError,
    ColorMode,
    NoScannerFoundError,
    PageSize,
    ScanAborted,
    ScanBackend,
    ScanError,
    ScanLibError,
    ScannerInfo,
    ScanOptions,
    ScanSource,
    ScannedDocument,
)

__all__ = [
    "list_scanners",
    "scan",
    "ColorMode",
    "PageSize",
    "ScannerInfo",
    "ScanOptions",
    "ScanSource",
    "ScannedDocument",
    "ScanLibError",
    "ScanError",
    "ScanAborted",
    "NoScannerFoundError",
    "BackendNotAvailableError",
]


def _get_backend() -> ScanBackend:
    """Return the appropriate scanning backend for the current platform."""
    if sys.platform == "linux":
        from .backends._sane import SaneBackend

        return SaneBackend()

    if sys.platform == "darwin":
        from .backends._macos import MacOSBackend

        return MacOSBackend()

    if sys.platform == "win32":
        from .backends._twain import TwainBackend

        return TwainBackend()

    raise BackendNotAvailableError(f"Unsupported platform: {sys.platform}")


def list_scanners() -> list[ScannerInfo]:
    """Return all available scanners on the current platform."""
    return _get_backend().list_scanners()


def scan(
    scanner: ScannerInfo | None = None,
    *,
    dpi: int = 300,
    color_mode: ColorMode = ColorMode.COLOR,
    page_size: PageSize | None = None,
    source: ScanSource | None = None,
    progress: Callable[[int], bool] | None = None,
) -> ScannedDocument:
    """Scan a document and return raw PNG bytes.

    If *scanner* is ``None``, the first available scanner is used.

    Args:
        scanner: A :class:`ScannerInfo` to scan from, or ``None``.
        dpi: Resolution in dots per inch (default 300).
        color_mode: A :class:`ColorMode` value (default ``ColorMode.COLOR``).
        page_size: A :class:`PageSize` in 1/10 mm, or ``None`` to autodetect.
        source: A :class:`ScanSource` value, or ``None`` for device default.
        progress: A callback ``(percent: int) -> bool`` invoked during scanning.
            *percent* is 0–100 or ``-1`` if progress is unavailable.
            Return ``True`` to continue or ``False`` to abort (raises
            :class:`ScanAborted`).

    Returns:
        A :class:`ScannedDocument` containing the scanned image as PNG bytes.
    """
    options = ScanOptions(
        dpi=dpi, color_mode=color_mode, page_size=page_size, source=source,
        progress=progress,
    )
    return _get_backend().scan(scanner, options)
