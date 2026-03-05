from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
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

    width: float
    height: float


@dataclass(frozen=True)
class ScannerInfo:
    """Information about an available scanner."""

    name: str
    vendor: str | None
    model: str | None
    backend: str
    sources: list[ScanSource] = field(default_factory=list)


@dataclass(frozen=True)
class ScanOptions:
    """Options for a scan operation."""

    dpi: int = 300
    color_mode: ColorMode = ColorMode.COLOR
    page_size: PageSize | None = None
    source: ScanSource | None = None
    progress: Callable[[int], bool] | None = None


@dataclass(frozen=True)
class ScannedDocument:
    """Result of a scan operation.

    ``data`` contains raw PNG image bytes.
    """

    data: bytes
    width: int
    height: int
    dpi: int
    color_mode: ColorMode
    scanner: ScannerInfo


# --- Backend protocol ---


class ScanBackend(Protocol):
    """Interface that all platform backends must implement."""

    def list_scanners(self) -> list[ScannerInfo]: ...

    def scan(
        self, scanner: ScannerInfo | None, options: ScanOptions
    ) -> ScannedDocument: ...
