from __future__ import annotations

import io
import math

import sane

from .._types import (
    ColorMode,
    PageSize,
    ScanAborted,
    ScanError,
    ScannedPage,
    Scanner,
    ScanOptions,
    ScanSource,
)
from ._util import MM_PER_INCH, check_progress

_COLOR_MODE_MAP = {
    ColorMode.COLOR: "color",
    ColorMode.GRAY: "gray",
    ColorMode.BW: "lineart",
}

_SANE_SOURCE_MAP = {
    "flatbed": ScanSource.FLATBED,
    "automatic document feeder": ScanSource.FEEDER,
    "adf": ScanSource.FEEDER,
}

_SCAN_SOURCE_TO_SANE = {
    ScanSource.FLATBED: "Flatbed",
    ScanSource.FEEDER: "Automatic Document Feeder",
}


def _get_options(dev: object) -> list[tuple]:
    """Get SANE device options, returning an empty list on failure."""
    try:
        opts = dev.get_options()
    except Exception:
        return []
    return [opt for opt in opts if isinstance(opt, tuple) and len(opt) >= 8]


def _parse_sources(opts: list[tuple]) -> list[ScanSource]:
    """Read available scan sources from SANE device options."""
    for opt in opts:
        if opt[0] == "source":
            constraint = opt[7]
            if isinstance(constraint, (list, tuple)):
                sources: list[ScanSource] = []
                for value in constraint:
                    key = str(value).lower()
                    for pattern, source in _SANE_SOURCE_MAP.items():
                        if pattern in key and source not in sources:
                            sources.append(source)
                return sources
    return []


def _parse_max_page_size(opts: list[tuple]) -> PageSize | None:
    """Read maximum scan area from SANE device options (br_x, br_y)."""
    max_x = max_y = None
    for opt in opts:
        name = opt[0]
        constraint = opt[7]
        if name in ("br_x", "br-x") and isinstance(constraint, (list, tuple)) and len(constraint) >= 2:
            max_x = float(constraint[1])  # (min, max, step)
        elif name in ("br_y", "br-y") and isinstance(constraint, (list, tuple)) and len(constraint) >= 2:
            max_y = float(constraint[1])

    if max_x is not None and max_y is not None:
        return PageSize(width=math.ceil(max_x * 10), height=math.ceil(max_y * 10))
    return None


def _is_feeder_empty(exc: Exception) -> bool:
    """Return True if the exception indicates the ADF is out of pages."""
    msg = str(exc).lower()
    return any(s in msg for s in ("no more", "out of documents", "empty", "no doc"))


class SaneBackend:
    """Linux scanning backend using python-sane (SANE)."""

    def __init__(self) -> None:
        sane.init()
        self._handles: dict[str, object] = {}

    def list_scanners(self) -> list[Scanner]:
        devices = sane.get_devices()
        return [
            Scanner(
                name=dev_info[0],
                vendor=dev_info[1] or None,
                model=dev_info[2] or None,
                backend="sane",
                _backend_impl=self,
            )
            for dev_info in devices
        ]

    def open_scanner(self, scanner: Scanner) -> None:
        try:
            dev = sane.open(scanner.name)
        except Exception as exc:
            raise ScanError(
                f"Failed to open scanner {scanner.name!r}: {exc}"
            ) from exc
        self._handles[scanner.name] = dev

        opts = _get_options(dev)
        scanner._sources = _parse_sources(opts)

        # Query max page size per source
        for source in scanner._sources:
            try:
                dev.source = _SCAN_SOURCE_TO_SANE.get(source, source.value)
            except Exception:
                pass
            # Re-read options after source change (constraints may differ)
            source_opts = _get_options(dev)
            ps = _parse_max_page_size(source_opts)
            if ps is not None:
                scanner._max_page_sizes[source] = ps

    def close_scanner(self, scanner: Scanner) -> None:
        dev = self._handles.pop(scanner.name, None)
        if dev is not None:
            dev.close()

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> list[ScannedPage]:
        dev = self._handles.get(scanner.name)
        if dev is None:
            raise ScanError("Scanner is not open")

        try:
            dev.mode = _COLOR_MODE_MAP.get(options.color_mode, options.color_mode.value)
            dev.resolution = options.dpi

            if options.source is not None:
                dev.source = _SCAN_SOURCE_TO_SANE.get(
                    options.source, options.source.value
                )

            if options.page_size is not None:
                dev.br_x = options.page_size.width / 10.0
                dev.br_y = options.page_size.height / 10.0

            check_progress(options.progress, 0)

            is_feeder = options.source == ScanSource.FEEDER
            pages: list[ScannedPage] = []

            while True:
                try:
                    img = dev.scan()
                except Exception as exc:
                    msg = str(exc).lower()
                    if is_feeder and pages and _is_feeder_empty(exc):
                        break
                    if "cancel" in msg or "abort" in msg or "jammed" in msg:
                        raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                    raise

                # Convert to optimal mode for the requested color mode
                if options.color_mode == ColorMode.BW:
                    img = img.convert("1")
                elif options.color_mode == ColorMode.GRAY:
                    img = img.convert("L")

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                pages.append(ScannedPage(
                    png_data=buf.getvalue(),
                    width=img.width,
                    height=img.height,
                ))

                if not is_feeder:
                    break

            if not pages:
                raise ScanError("No pages were scanned")

            check_progress(options.progress, 100)
            return pages
        except ScanAborted:
            dev.cancel()
            raise
        except ScanError:
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
