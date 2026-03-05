from __future__ import annotations

import io

import sane

from .._types import (
    ColorMode,
    NoScannerFoundError,
    ScanAborted,
    ScanError,
    ScannerInfo,
    ScanOptions,
    ScanSource,
    ScannedDocument,
)

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


def _parse_sources(dev: object) -> list[ScanSource]:
    """Read available scan sources from a SANE device."""
    try:
        opts = dev.get_options()
    except Exception:
        return []

    for opt in opts:
        if not isinstance(opt, tuple) or len(opt) < 8:
            continue
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


def _check_progress(options: ScanOptions, percent: int) -> None:
    """Call the progress callback; raise ScanAborted if it returns False."""
    if options.progress is not None and options.progress(percent) is False:
        raise ScanAborted("Scan aborted")


class SaneBackend:
    """Linux scanning backend using python-sane (SANE)."""

    def __init__(self) -> None:
        sane.init()

    def list_scanners(self) -> list[ScannerInfo]:
        devices = sane.get_devices()
        scanners = []
        for dev_info in devices:
            name = dev_info[0]
            try:
                dev = sane.open(name)
                sources = _parse_sources(dev)
                dev.close()
            except Exception:
                sources = []
            scanners.append(
                ScannerInfo(
                    name=name,
                    vendor=dev_info[1] or None,
                    model=dev_info[2] or None,
                    backend="sane",
                    sources=sources,
                )
            )
        return scanners

    def scan(
        self, scanner: ScannerInfo | None, options: ScanOptions
    ) -> ScannedDocument:
        if scanner is None:
            scanners = self.list_scanners()
            if not scanners:
                raise NoScannerFoundError("No scanners found")
            scanner = scanners[0]

        try:
            dev = sane.open(scanner.name)
        except Exception as exc:
            raise ScanError(f"Failed to open scanner {scanner.name!r}: {exc}") from exc

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

            _check_progress(options, 0)

            try:
                img = dev.scan()
            except Exception as exc:
                msg = str(exc).lower()
                if "cancel" in msg or "abort" in msg or "jammed" in msg:
                    raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                raise

            _check_progress(options, 100)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_data = buf.getvalue()

            return ScannedDocument(
                data=png_data,
                width=img.width,
                height=img.height,
                dpi=options.dpi,
                color_mode=options.color_mode,
                scanner=scanner,
            )
        except ScanAborted:
            dev.cancel()
            raise
        except (NoScannerFoundError, ScanError):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
        finally:
            dev.close()
