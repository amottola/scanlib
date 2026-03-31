"""scanlib — A multiplatform document scanning library for Python."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from importlib.metadata import version

__version__ = version("scanlib")

from ._types import (
    DISCOVERY_TIMEOUT,
    BackendNotAvailableError,
    ColorMode,
    FeederEmptyError,
    ImageFormat,
    NoScannerFoundError,
    ScanArea,
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
    ScannedPage,
    SourceInfo,
    build_pdf,
)

__all__ = [
    "list_scanners",
    "open_scanner",
    "build_pdf",
    "DISCOVERY_TIMEOUT",
    "ColorMode",
    "ImageFormat",
    "ScanArea",
    "Scanner",
    "ScannerDefaults",
    "ScannerNotOpenError",
    "ScanOptions",
    "ScanSource",
    "ScannedDocument",
    "ScannedPage",
    "SourceInfo",
    "ScanLibError",
    "ScanError",
    "ScanAborted",
    "FeederEmptyError",
    "NoScannerFoundError",
    "BackendNotAvailableError",
]


# ---------------------------------------------------------------------------
# Composite backend — merges a platform backend with eSCL
# ---------------------------------------------------------------------------


class _CompositeBackend:
    """Merges results from a platform backend and the eSCL backend.

    ``list_scanners`` runs both backends' discovery in parallel and
    deduplicates by IP address.  Each scanner's ``_backend_impl`` points
    to whichever backend discovered it.
    """

    def __init__(self, platform_backend: ScanBackend) -> None:
        from .backends._escl import EsclBackend

        self._platform = platform_backend
        self._escl = EsclBackend()

    def list_scanners(
        self,
        timeout: float = DISCOVERY_TIMEOUT,
        cancel: threading.Event | None = None,
    ) -> list[Scanner]:
        # Run both discoveries in parallel.
        platform_box: list[list[Scanner]] = [[]]
        escl_box: list[list[Scanner]] = [[]]

        def _run_platform() -> None:
            try:
                platform_box[0] = self._platform.list_scanners(
                    timeout=timeout, cancel=cancel
                )
            except Exception:
                platform_box[0] = []

        def _run_escl() -> None:
            try:
                escl_box[0] = self._escl.list_scanners(timeout=timeout, cancel=cancel)
            except Exception:
                escl_box[0] = []

        t_platform = threading.Thread(target=_run_platform, daemon=True)
        t_escl = threading.Thread(target=_run_escl, daemon=True)
        t_platform.start()
        t_escl.start()

        # Wait for eSCL first (fast, ~4s max), then give the platform
        # backend a short grace period to finish.  Don't block on a
        # slow platform backend when eSCL already has results.
        t_escl.join(timeout=timeout + 2)
        if t_platform.is_alive():
            t_platform.join(timeout=2.0)

        if cancel is not None and cancel.is_set():
            return []

        platform_scanners = platform_box[0]
        escl_scanners = escl_box[0]

        if not escl_scanners:
            return platform_scanners

        # Collect IPs from platform scanners for deduplication
        from ._mdns import extract_ip_from_uri

        platform_ips: set[str] = set()
        for s in platform_scanners:
            ip = extract_ip_from_uri(s.name)
            if ip:
                platform_ips.add(ip)

        # Add eSCL scanners not already found by the platform backend
        escl_ips = self._escl.get_scanner_ips()
        for s in escl_scanners:
            ip = escl_ips.get(s.id)
            if ip and ip in platform_ips:
                continue  # already discovered by platform backend
            platform_scanners.append(s)

        return platform_scanners

    # Delegate remaining methods to the scanner's own _backend_impl
    def open_scanner(self, scanner: Scanner) -> None:
        scanner._backend_impl.open_scanner(scanner)

    def close_scanner(self, scanner: Scanner) -> None:
        scanner._backend_impl.close_scanner(scanner)

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]:
        return scanner._backend_impl.scan_pages(scanner, options)

    def abort_scan(self, scanner: Scanner) -> None:
        scanner._backend_impl.abort_scan(scanner)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_backend: ScanBackend | None = None


def _get_backend() -> ScanBackend:
    """Return the appropriate scanning backend for the current platform."""
    global _backend
    if _backend is not None:
        return _backend

    if sys.platform == "linux":
        from .backends._sane import SaneBackend

        _backend = _CompositeBackend(SaneBackend())

    elif sys.platform == "darwin":
        from .backends._macos import MacOSBackend

        if os.environ.get("SCANLIB_ESCL", "").strip() == "1":
            _backend = _CompositeBackend(MacOSBackend())
        else:
            _backend = MacOSBackend()

    elif sys.platform == "win32":
        from .backends._wia import WiaBackend

        _backend = _CompositeBackend(WiaBackend())

    else:
        raise BackendNotAvailableError(f"Unsupported platform: {sys.platform}")

    return _backend


def list_scanners(
    *,
    timeout: float = DISCOVERY_TIMEOUT,
    cancel: threading.Event | None = None,
) -> list[Scanner]:
    """Return all available scanners on the current platform.

    *timeout* controls how long (in seconds) to wait for scanner
    discovery.  The default is :data:`DISCOVERY_TIMEOUT` (15 s).

    *cancel*, if given, is a :class:`threading.Event` that the caller can
    set from another thread to interrupt discovery early.  When the event
    is set the function returns immediately with an empty list.

    The returned :class:`Scanner` objects are lightweight — no device sessions
    are opened.  Use :meth:`Scanner.open` (or the context-manager protocol)
    to start a session before scanning.
    """
    return _get_backend().list_scanners(timeout=timeout, cancel=cancel)


def open_scanner(scanner_id: str) -> Scanner:
    """Open a scanner directly by its ID, without discovery.

    *scanner_id* is the :attr:`Scanner.id` string obtained from a
    previous :func:`list_scanners` call (e.g. ``"escl:192.168.1.5:443"``
    for eSCL, a SANE device URI, or a WIA device ID).

    Returns an **opened** :class:`Scanner` ready for scanning.  The
    caller must call :meth:`Scanner.close` (or use the context-manager
    protocol) when done.

    This avoids the latency of scanner discovery when the ID is already
    known.
    """
    if scanner_id.startswith("escl:"):
        from .backends._escl import EsclBackend

        impl = EsclBackend()
    else:
        # Use the platform backend
        top = _get_backend()
        impl = top._platform if isinstance(top, _CompositeBackend) else top

    scanner = Scanner(
        name=scanner_id,
        vendor=None,
        model=None,
        backend=impl.backend_name,
        scanner_id=scanner_id,
        _backend_impl=impl,
    )
    scanner.open()
    return scanner
