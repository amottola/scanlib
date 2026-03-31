"""eSCL (AirScan) backend — direct HTTP communication with network scanners.

Implements the eSCL protocol over HTTP/HTTPS using only stdlib modules.
Works on all platforms for scanners advertising ``_uscan._tcp`` or
``_uscans._tcp`` via mDNS.

eSCL protocol reference:
- Discovery: mDNS ``_uscan._tcp`` / ``_uscans._tcp``
- Capabilities: ``GET /<rs>/ScannerCapabilities``
- Scan job:    ``POST /<rs>/ScanJobs`` → 201 + ``Location`` header
- Retrieve:    ``GET <job_url>/NextDocument`` → image data
- Cancel:      ``DELETE <job_url>``
- Status:      ``GET /<rs>/ScannerStatus``

Units: eSCL uses 1/300 inch ("three-hundredths").  Scanlib uses 1/10 mm.
Conversion: ``escl = round(tenths_mm * 300 / 254)``,
            ``tenths_mm = round(escl * 254 / 300)``.
"""

from __future__ import annotations

import http.client
import ssl
import threading
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from urllib.parse import urlparse

from .._jpeg import decode_jpeg
from .._mdns import EsclServiceInfo, discover_escl_services, extract_ip_from_uri
from .._types import (
    DISCOVERY_TIMEOUT,
    ColorMode,
    ScanAborted,
    ScanArea,
    ScanError,
    ScannedPage,
    Scanner,
    ScannerDefaults,
    ScanOptions,
    ScanSource,
    SourceInfo,
    check_progress,
    normalize_resolutions,
)

# eSCL XML namespaces
_SCAN_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
_PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"

# Register namespace prefixes for ET output
ET.register_namespace("scan", _SCAN_NS)
ET.register_namespace("pwg", _PWG_NS)


def _ns(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------


def _tenths_mm_to_escl(value: int) -> int:
    """Convert 1/10 mm to eSCL units (1/300 inch)."""
    return round(value * 300 / 254)


def _escl_to_tenths_mm(value: int) -> int:
    """Convert eSCL units (1/300 inch) to 1/10 mm."""
    return round(value * 254 / 300)


# ---------------------------------------------------------------------------
# eSCL color mode mapping
# ---------------------------------------------------------------------------

_ESCL_TO_COLOR_MODE = {
    "BlackAndWhite1": ColorMode.BW,
    "Grayscale8": ColorMode.GRAY,
    "Grayscale16": ColorMode.GRAY,
    "RGB24": ColorMode.COLOR,
    "RGB48": ColorMode.COLOR,
}

_COLOR_MODE_TO_ESCL = {
    ColorMode.BW: "BlackAndWhite1",
    ColorMode.GRAY: "Grayscale8",
    ColorMode.COLOR: "RGB24",
}

_ESCL_SOURCE_MAP = {
    "Platen": ScanSource.FLATBED,
    "Adf": ScanSource.FEEDER,
    "ADFSimplex": ScanSource.FEEDER,
    "ADFDuplex": ScanSource.FEEDER,
}

_SOURCE_TO_ESCL = {
    ScanSource.FLATBED: "Platen",
    ScanSource.FEEDER: "Platen",  # overridden below if ADF found
}


# ---------------------------------------------------------------------------
# HTTP connection
# ---------------------------------------------------------------------------


class _EsclConnection:
    """HTTP(S) connection to an eSCL scanner."""

    def __init__(self, ip: str, port: int, tls: bool, resource_path: str) -> None:
        self.ip = ip
        self.port = port
        self.tls = tls
        self.base_path = f"/{resource_path.strip('/')}"
        self._conn: http.client.HTTPConnection | None = None
        self._current_job: str | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is not None:
            return self._conn
        if self.tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # scanners use self-signed certs
            self._conn = http.client.HTTPSConnection(
                self.ip, self.port, timeout=30, context=ctx
            )
        else:
            self._conn = http.client.HTTPConnection(
                self.ip, self.port, timeout=30
            )
        return self._conn

    def get_capabilities(self) -> ET.Element:
        conn = self._connect()
        conn.request("GET", f"{self.base_path}/ScannerCapabilities")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise ScanError(
                f"ScannerCapabilities returned {resp.status}: {body[:200]}"
            )
        return ET.fromstring(body)

    def get_status(self) -> str:
        conn = self._connect()
        conn.request("GET", f"{self.base_path}/ScannerStatus")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return "Unknown"
        root = ET.fromstring(body)
        state_el = root.find(_ns(_PWG_NS, "State"))
        if state_el is None:
            state_el = root.find(_ns(_SCAN_NS, "State"))
        return state_el.text if state_el is not None and state_el.text else "Unknown"

    def create_job(self, settings_xml: str) -> str:
        """POST scan settings, return the job URL path."""
        conn = self._connect()
        conn.request(
            "POST",
            f"{self.base_path}/ScanJobs",
            body=settings_xml.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
        )
        resp = conn.getresponse()
        resp.read()  # drain body
        if resp.status == 409:
            raise ScanError("Scanner is busy (HTTP 409 Conflict)")
        if resp.status not in (200, 201):
            raise ScanError(f"ScanJobs POST returned {resp.status}")
        location = resp.getheader("Location")
        if not location:
            raise ScanError("No Location header in ScanJobs response")
        # Location may be absolute URL or relative path
        parsed = urlparse(location)
        job_path = parsed.path
        self._current_job = job_path
        return job_path

    def get_next_document(self, job_path: str) -> bytes | None:
        """GET the next document page. Returns None when no more pages."""
        conn = self._connect()
        url = f"{job_path}/NextDocument"
        conn.request("GET", url)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status == 404:
            return None  # no more pages
        if resp.status != 200:
            raise ScanError(f"NextDocument returned {resp.status}")
        return body

    def delete_job(self, job_path: str) -> None:
        try:
            conn = self._connect()
            conn.request("DELETE", job_path)
            resp = conn.getresponse()
            resp.read()
        except Exception:
            pass
        self._current_job = None

    def close(self) -> None:
        if self._current_job:
            self.delete_job(self._current_job)
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# ---------------------------------------------------------------------------
# Capabilities parsing
# ---------------------------------------------------------------------------


def _parse_resolutions(source_el: ET.Element) -> list[int]:
    """Extract supported resolutions from a source element."""
    resolutions: list[int] = []

    # Try DiscreteResolutions first
    discrete = source_el.find(_ns(_SCAN_NS, "DiscreteResolutions"))
    if discrete is not None:
        for entry in discrete.findall(_ns(_SCAN_NS, "DiscreteResolution")):
            x_res = entry.find(_ns(_SCAN_NS, "XResolution"))
            if x_res is not None and x_res.text:
                resolutions.append(int(x_res.text))
        if resolutions:
            return sorted(set(resolutions))

    # Try ResolutionRange (min/max/step)
    for tag in ("XResolutionRange", "ResolutionRange"):
        rng = source_el.find(_ns(_SCAN_NS, tag))
        if rng is not None:
            min_el = rng.find(_ns(_SCAN_NS, "Min"))
            max_el = rng.find(_ns(_SCAN_NS, "Max"))
            step_el = rng.find(_ns(_SCAN_NS, "Step"))
            if min_el is not None and max_el is not None:
                lo = int(min_el.text or "75")
                hi = int(max_el.text or "600")
                step = int(step_el.text or "1") if step_el is not None else 1
                resolutions = list(range(lo, hi + 1, step))
                return normalize_resolutions(resolutions)

    return [300]  # safe default


def _parse_color_modes(source_el: ET.Element) -> list[ColorMode]:
    """Extract supported color modes from a source element."""
    modes: list[ColorMode] = []
    cm_el = source_el.find(_ns(_SCAN_NS, "ColorModes"))
    if cm_el is not None:
        for mode_el in cm_el.findall(_ns(_SCAN_NS, "ColorMode")):
            if mode_el.text and mode_el.text in _ESCL_TO_COLOR_MODE:
                cm = _ESCL_TO_COLOR_MODE[mode_el.text]
                if cm not in modes:
                    modes.append(cm)
    return modes or [ColorMode.COLOR]


def _parse_max_scan_area(source_el: ET.Element) -> ScanArea | None:
    """Extract max scan area from a source element (in 1/10 mm)."""
    w_el = source_el.find(_ns(_SCAN_NS, "MaxWidth"))
    h_el = source_el.find(_ns(_SCAN_NS, "MaxHeight"))
    if w_el is not None and h_el is not None and w_el.text and h_el.text:
        w_escl = int(w_el.text)
        h_escl = int(h_el.text)
        return ScanArea(0, 0, _escl_to_tenths_mm(w_escl), _escl_to_tenths_mm(h_escl))

    # Fallback: US Letter / A4 bounding box
    return ScanArea(0, 0, 2159, 2970)


def _parse_capabilities(
    root: ET.Element,
) -> tuple[list[SourceInfo], ScannerDefaults | None, dict[ScanSource, str]]:
    """Parse ScannerCapabilities XML.

    Returns ``(sources, defaults, source_escl_names)`` where
    *source_escl_names* maps :class:`ScanSource` to the eSCL
    ``InputSource`` string to use in scan settings.
    """
    sources: list[SourceInfo] = []
    source_escl_names: dict[ScanSource, str] = {}

    # Map of eSCL source element tags to scan source types
    source_tags = [
        ("Platen", ScanSource.FLATBED),
        ("Adf", ScanSource.FEEDER),
        ("AdfSimplex", ScanSource.FEEDER),
        ("AdfDuplex", ScanSource.FEEDER),
    ]

    seen_types: set[ScanSource] = set()

    for escl_tag, scan_source in source_tags:
        el = root.find(_ns(_SCAN_NS, escl_tag))
        if el is None:
            continue
        if scan_source in seen_types:
            continue
        seen_types.add(scan_source)

        resolutions = _parse_resolutions(el)
        color_modes = _parse_color_modes(el)
        max_area = _parse_max_scan_area(el)

        sources.append(
            SourceInfo(
                type=scan_source,
                resolutions=resolutions,
                color_modes=color_modes,
                max_scan_area=max_area,
            )
        )
        source_escl_names[scan_source] = escl_tag

    if not sources:
        # No recognized sources — create a default flatbed entry
        sources.append(
            SourceInfo(
                type=ScanSource.FLATBED,
                resolutions=[300],
                color_modes=[ColorMode.COLOR],
                max_scan_area=ScanArea(0, 0, 2159, 2970),
            )
        )
        source_escl_names[ScanSource.FLATBED] = "Platen"

    # Build defaults from the first source
    first = sources[0]
    default_dpi = 300 if 300 in first.resolutions else first.resolutions[0]
    default_cm = ColorMode.COLOR if ColorMode.COLOR in first.color_modes else first.color_modes[0]
    defaults = ScannerDefaults(
        dpi=default_dpi,
        color_mode=default_cm,
        source=first.type,
    )

    return sources, defaults, source_escl_names


# ---------------------------------------------------------------------------
# Scan settings XML
# ---------------------------------------------------------------------------


def _build_scan_settings(
    options: ScanOptions,
    source_name: str,
    max_area: ScanArea | None,
) -> str:
    """Build eSCL ScanSettings XML from scan options."""
    root = ET.Element(_ns(_SCAN_NS, "ScanSettings"))

    # Version
    ver = ET.SubElement(root, _ns(_PWG_NS, "Version"))
    ver.text = "2.0"

    # Input source
    src = ET.SubElement(root, _ns(_SCAN_NS, "InputSource"))
    src.text = source_name

    # Resolution
    res_el = ET.SubElement(root, _ns(_SCAN_NS, "XResolution"))
    res_el.text = str(options.dpi)
    res_el2 = ET.SubElement(root, _ns(_SCAN_NS, "YResolution"))
    res_el2.text = str(options.dpi)

    # Color mode
    cm = ET.SubElement(root, _ns(_SCAN_NS, "ColorMode"))
    cm.text = _COLOR_MODE_TO_ESCL.get(options.color_mode, "RGB24")

    # Scan area
    if options.scan_area is not None:
        area = options.scan_area
    elif max_area is not None:
        area = max_area
    else:
        area = None

    if area is not None:
        input_el = ET.SubElement(root, _ns(_SCAN_NS, "InputSize"))
        w_el = ET.SubElement(input_el, _ns(_SCAN_NS, "Width"))
        w_el.text = str(_tenths_mm_to_escl(area.width))
        h_el = ET.SubElement(input_el, _ns(_SCAN_NS, "Height"))
        h_el.text = str(_tenths_mm_to_escl(area.height))
        if area.x or area.y:
            xoff = ET.SubElement(root, _ns(_SCAN_NS, "XOffset"))
            xoff.text = str(_tenths_mm_to_escl(area.x))
            yoff = ET.SubElement(root, _ns(_SCAN_NS, "YOffset"))
            yoff.text = str(_tenths_mm_to_escl(area.y))

    # Request JPEG document format (widely supported)
    fmt = ET.SubElement(root, _ns(_SCAN_NS, "DocumentFormatExt"))
    fmt.text = "image/jpeg"

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


# ---------------------------------------------------------------------------
# Image decoding
# ---------------------------------------------------------------------------


def _decode_scan_response(
    data: bytes, color_mode: ColorMode
) -> ScannedPage:
    """Decode scanner response (JPEG) into a ScannedPage."""
    raw_pixels, width, height, components = decode_jpeg(data)

    if components == 1:
        actual_mode = ColorMode.GRAY
    else:
        actual_mode = ColorMode.COLOR

    # If BW was requested, convert grayscale to 1-bit
    if color_mode == ColorMode.BW and actual_mode != ColorMode.BW:
        from _scanlib_accel import gray_to_bw, rgb_to_gray

        if actual_mode == ColorMode.COLOR:
            raw_pixels = rgb_to_gray(raw_pixels, width, height)
        raw_pixels = gray_to_bw(raw_pixels, width, height)
        actual_mode = ColorMode.BW

    return ScannedPage(
        data=raw_pixels,
        width=width,
        height=height,
        color_mode=actual_mode,
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class EsclBackend:
    """eSCL (AirScan) scanning backend using direct HTTP."""

    def __init__(self) -> None:
        self._connections: dict[str, _EsclConnection] = {}
        self._source_names: dict[str, dict[ScanSource, str]] = {}

    def list_scanners(
        self,
        timeout: float = DISCOVERY_TIMEOUT,
        cancel: threading.Event | None = None,
    ) -> list[Scanner]:
        services = discover_escl_services(timeout=min(timeout, 4.0))
        scanners: list[Scanner] = []

        for svc in services:
            if cancel is not None and cancel.is_set():
                return []

            scanner_id = f"escl:{svc.ip}:{svc.port}"
            if svc.uuid:
                scanner_id = f"escl:{svc.uuid}"

            scanners.append(
                Scanner(
                    name=svc.name,
                    vendor=None,
                    model=None,
                    backend="escl",
                    scanner_id=scanner_id,
                    location=svc.note,
                    _backend_impl=self,
                )
            )
            # Stash service info for open_scanner
            self._connections[scanner_id] = _EsclConnection(
                ip=svc.ip,
                port=svc.port,
                tls=svc.tls,
                resource_path=svc.resource_path,
            )

        return scanners

    def open_scanner(self, scanner: Scanner) -> None:
        conn = self._connections.get(scanner.id)
        if conn is None:
            raise ScanError(f"Unknown eSCL scanner: {scanner.id}")

        try:
            caps_xml = conn.get_capabilities()
        except Exception as exc:
            raise ScanError(f"Failed to get scanner capabilities: {exc}") from exc

        sources, defaults, source_names = _parse_capabilities(caps_xml)
        scanner._sources = sources
        scanner._defaults = defaults
        self._source_names[scanner.id] = source_names

    def close_scanner(self, scanner: Scanner) -> None:
        conn = self._connections.get(scanner.id)
        if conn is not None:
            conn.close()

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]:
        conn = self._connections.get(scanner.id)
        if conn is None:
            raise ScanError(f"Unknown eSCL scanner: {scanner.id}")

        source_names = self._source_names.get(scanner.id, {})
        source = options.source or ScanSource.FLATBED
        escl_source = source_names.get(source, "Platen")
        is_feeder = source == ScanSource.FEEDER

        # Find max area for this source
        max_area: ScanArea | None = None
        for si in scanner._sources:
            if si.type == source:
                max_area = si.max_scan_area
                break

        settings_xml = _build_scan_settings(options, escl_source, max_area)

        check_progress(options.progress, 0)

        try:
            job_path = conn.create_job(settings_xml)
        except ScanError:
            raise
        except Exception as exc:
            raise ScanError(f"Failed to create scan job: {exc}") from exc

        try:
            page_num = 0
            while True:
                if scanner._abort_event.is_set():
                    conn.delete_job(job_path)
                    raise ScanAborted("Scan aborted")

                check_progress(options.progress, -1)

                try:
                    doc_data = conn.get_next_document(job_path)
                except ScanAborted:
                    conn.delete_job(job_path)
                    raise
                except Exception as exc:
                    if page_num > 0 and is_feeder:
                        break  # connection error after pages = feeder empty
                    raise ScanError(f"Failed to retrieve scan data: {exc}") from exc

                if doc_data is None:
                    break  # no more pages

                page_num += 1
                try:
                    page = _decode_scan_response(doc_data, options.color_mode)
                except Exception as exc:
                    raise ScanError(f"Failed to decode scan data: {exc}") from exc

                check_progress(options.progress, min(page_num * 99, 99))
                yield page

                if not is_feeder:
                    break  # flatbed: one page per job

        except (ScanAborted, ScanError):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
        finally:
            conn._current_job = None

        check_progress(options.progress, 100)

    def abort_scan(self, scanner: Scanner) -> None:
        conn = self._connections.get(scanner.id)
        if conn is not None and conn._current_job:
            conn.delete_job(conn._current_job)

    def get_scanner_ips(self) -> dict[str, str]:
        """Return scanner_id → IP mapping for deduplication."""
        return {
            sid: conn.ip for sid, conn in self._connections.items()
        }
