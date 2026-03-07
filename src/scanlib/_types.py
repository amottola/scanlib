from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


# --- Exceptions ---


class ScanLibError(Exception):
    """Base exception for scanlib."""


class NoScannerFoundError(ScanLibError):
    """No scanner device was found."""


class ScanError(ScanLibError):
    """An error occurred during scanning."""


class ScanAborted(ScanLibError):
    """The scan was aborted before completion."""


class BackendNotAvailableError(ScanLibError):
    """The scanning backend for this platform is not installed."""


class ScannerNotOpenError(ScanLibError):
    """Operation requires an open scanner session."""


# --- Enums ---


class ColorMode(enum.Enum):
    """Color mode for scanning."""

    COLOR = "color"
    GRAY = "gray"
    BW = "bw"


class ScanSource(enum.Enum):
    """Scan source type."""

    FLATBED = "flatbed"
    FEEDER = "feeder"


# --- Data classes ---


@dataclass(frozen=True)
class PageSize:
    """Page size in 1/10 millimeters."""

    width: int
    height: int


@dataclass(frozen=True)
class ScannerDefaults:
    """Default settings detected from the device after opening."""

    dpi: int
    color_mode: ColorMode
    source: ScanSource | None


@dataclass(frozen=True)
class ScanOptions:
    """Options for a scan operation."""

    dpi: int = 300
    color_mode: ColorMode = ColorMode.COLOR
    page_size: PageSize | None = None
    source: ScanSource | None = None
    progress: Callable[[int], bool] | None = None
    next_page: Callable[[int], bool] | None = None


@dataclass(frozen=True)
class ScannedPage:
    """A single scanned page as PNG data (internal type)."""

    png_data: bytes
    width: int
    height: int


@dataclass(frozen=True)
class ScannedDocument:
    """Result of a scan operation.

    ``data`` contains PDF file bytes (one or more pages).
    """

    data: bytes
    page_count: int
    width: int
    height: int
    dpi: int
    color_mode: ColorMode
    scanner: Scanner


# --- Scanner ---


class Scanner:
    """Represents a discovered scanner device.

    Use :meth:`open` / :meth:`close` (or the context-manager protocol) to
    start a session before calling :meth:`scan`.
    """

    def __init__(
        self,
        name: str,
        vendor: str | None,
        model: str | None,
        backend: str,
        *,
        _backend_impl: ScanBackend | None = None,
    ) -> None:
        self._name = name
        self._vendor = vendor
        self._model = model
        self._backend = backend
        self._backend_impl = _backend_impl
        self._sources: list[ScanSource] = []
        self._max_page_sizes: dict[ScanSource, PageSize] = {}
        self._resolutions: list[int] = []
        self._color_modes: list[ColorMode] = []
        self._defaults: ScannerDefaults | None = None
        self._is_open = False

    # --- Read-only properties (always available) ---

    @property
    def name(self) -> str:
        return self._name

    @property
    def vendor(self) -> str | None:
        return self._vendor

    @property
    def model(self) -> str | None:
        return self._model

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def sources(self) -> list[ScanSource]:
        """Available scan sources. Only populated after :meth:`open`."""
        if not self._is_open:
            raise ScannerNotOpenError(
                "Scanner must be opened before querying sources"
            )
        return self._sources

    @property
    def defaults(self) -> ScannerDefaults | None:
        """Default settings and supported values detected from the device.

        Returns ``None`` if the backend could not determine defaults.
        Only available after :meth:`open`.
        """
        if not self._is_open:
            raise ScannerNotOpenError(
                "Scanner must be opened before querying defaults"
            )
        return self._defaults

    @property
    def max_page_sizes(self) -> dict[ScanSource, PageSize]:
        """Maximum scan area per source as a :class:`PageSize` (1/10 mm).

        Returns a dict mapping each :class:`ScanSource` to its maximum
        scan area.  The dict may be empty if the backend could not
        determine sizes.  Only available after :meth:`open`.
        """
        if not self._is_open:
            raise ScannerNotOpenError(
                "Scanner must be opened before querying max page sizes"
            )
        return self._max_page_sizes

    @property
    def resolutions(self) -> list[int]:
        """Supported DPI values. Only populated after :meth:`open`."""
        if not self._is_open:
            raise ScannerNotOpenError(
                "Scanner must be opened before querying resolutions"
            )
        return self._resolutions

    @property
    def color_modes(self) -> list[ColorMode]:
        """Supported color modes. Only populated after :meth:`open`."""
        if not self._is_open:
            raise ScannerNotOpenError(
                "Scanner must be opened before querying color modes"
            )
        return self._color_modes

    # --- Session management ---

    def open(self) -> Scanner:
        """Open a session on the scanner device. Returns *self*."""
        if self._is_open:
            return self
        if self._backend_impl is None:
            raise ScanLibError("Scanner has no backend")
        self._backend_impl.open_scanner(self)
        self._is_open = True
        return self

    def close(self) -> None:
        """Close the scanner session."""
        if self._is_open and self._backend_impl is not None:
            self._backend_impl.close_scanner(self)
        self._is_open = False

    def __enter__(self) -> Scanner:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- Scanning ---

    def scan(
        self,
        *,
        dpi: int = 300,
        color_mode: ColorMode = ColorMode.COLOR,
        page_size: PageSize | None = None,
        source: ScanSource | None = None,
        progress: Callable[[int], bool] | None = None,
        next_page: Callable[[int], bool] | None = None,
    ) -> ScannedDocument:
        """Scan a document and return PDF bytes.

        When *source* is :attr:`ScanSource.FEEDER`, all pages in the
        document feeder are scanned.  Otherwise a single page is scanned.

        When *next_page* is provided and the source is not a feeder,
        the callback is called after each page with the number of pages
        scanned so far.  Return ``True`` to scan another page or ``False``
        to stop.
        """
        if not self._is_open:
            raise ScannerNotOpenError("Scanner must be opened before scanning")
        options = ScanOptions(
            dpi=dpi,
            color_mode=color_mode,
            page_size=page_size,
            source=source,
            progress=progress,
            next_page=next_page,
        )
        pages = self._backend_impl.scan_pages(self, options)
        from ._pdf import png_pages_to_pdf

        pdf_data = png_pages_to_pdf(
            [(p.png_data, p.width, p.height, dpi) for p in pages],
            color_mode=color_mode,
        )
        return ScannedDocument(
            data=pdf_data,
            page_count=len(pages),
            width=pages[0].width,
            height=pages[0].height,
            dpi=dpi,
            color_mode=color_mode,
            scanner=self,
        )

    def __repr__(self) -> str:
        state = "open" if self._is_open else "closed"
        return f"Scanner(name={self._name!r}, backend={self._backend!r}, {state})"


# --- Backend protocol ---


class ScanBackend(Protocol):
    """Interface that all platform backends must implement."""

    def list_scanners(self) -> list[Scanner]: ...

    def open_scanner(self, scanner: Scanner) -> None: ...

    def close_scanner(self, scanner: Scanner) -> None: ...

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> list[ScannedPage]: ...
