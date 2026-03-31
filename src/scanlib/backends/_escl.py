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
            self._conn = http.client.HTTPConnection(self.ip, self.port, timeout=30)
        return self._conn

    def get_capabilities(self) -> ET.Element:
        conn = self._connect()
        conn.request("GET", f"{self.base_path}/ScannerCapabilities")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise ScanError(f"ScannerCapabilities returned {resp.status}: {body[:200]}")
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

    def cancel_active_jobs(self) -> int:
        """Cancel any active jobs on the scanner. Returns count cancelled."""
        conn = self._connect()
        conn.request("GET", f"{self.base_path}/ScannerStatus")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return 0
        root = ET.fromstring(body)
        cancelled = 0
        for job_uri_el in root.iter(_ns(_PWG_NS, "JobUri")):
            if job_uri_el.text:
                self.delete_job(job_uri_el.text.strip())
                cancelled += 1
        for job_uri_el in root.iter(_ns(_SCAN_NS, "JobUri")):
            if job_uri_el.text:
                self.delete_job(job_uri_el.text.strip())
                cancelled += 1
        return cancelled

    def create_job(
        self, settings_xml: str, retries: int = 3, delay: float = 2.0
    ) -> str:
        """POST scan settings, return the job URL path.

        Retries on HTTP 503 (scanner busy/warming up) with a short
        backoff.
        """
        import time

        last_status = 0
        for attempt in range(retries):
            conn = self._connect()
            conn.request(
                "POST",
                f"{self.base_path}/ScanJobs",
                body=settings_xml.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
            )
            resp = conn.getresponse()
            resp.read()  # drain body
            last_status = resp.status
            if resp.status in (409, 503):
                if attempt < retries - 1:
                    # Try cancelling any stale jobs before retrying
                    if resp.status == 409:
                        self.cancel_active_jobs()
                    time.sleep(delay)
                    self._conn = None
                    continue
                reason = (
                    "busy (HTTP 409 Conflict)"
                    if resp.status == 409
                    else "unavailable (HTTP 503)"
                )
                raise ScanError(
                    f"Scanner {reason} — " f"still not ready after {retries} attempts"
                )
            if resp.status not in (200, 201):
                raise ScanError(f"ScanJobs POST returned {resp.status}")
            break

        location = resp.getheader("Location")
        if not location:
            raise ScanError("No Location header in ScanJobs response")
        # Location may be absolute URL or relative path
        parsed = urlparse(location)
        job_path = parsed.path
        self._current_job = job_path
        return job_path

    def get_next_document(self, job_path: str) -> tuple[bytes, str] | None:
        """GET the next document page.

        Returns ``(body, content_type)`` or ``None`` when no more pages.
        """
        conn = self._connect()
        url = f"{job_path}/NextDocument"
        conn.request("GET", url)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status == 404:
            return None  # no more pages
        if resp.status != 200:
            raise ScanError(f"NextDocument returned {resp.status}")
        content_type = resp.getheader("Content-Type", "image/jpeg")
        return body, content_type

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


_KNOWN_VENDORS = (
    "Brother",
    "Canon",
    "Dell",
    "Epson",
    "Fujitsu",
    "HP",
    "Hewlett-Packard",
    "Konica Minolta",
    "Kyocera",
    "Lexmark",
    "OKI",
    "Panasonic",
    "Ricoh",
    "Samsung",
    "Sharp",
    "Toshiba",
    "Xerox",
)


def _populate_make_model(root: ET.Element, scanner: Scanner) -> None:
    """Extract vendor and model from ``MakeAndModel`` in capabilities XML."""
    mam_el = next(root.iter(_ns(_PWG_NS, "MakeAndModel")), None)
    if mam_el is None or not mam_el.text:
        return
    make_model = mam_el.text.strip()
    if not make_model:
        return

    # Update name if it was just the scanner ID
    if scanner._name == scanner._id:
        scanner._name = make_model

    # Try to split into vendor + model
    for vendor in _KNOWN_VENDORS:
        if make_model.lower().startswith(vendor.lower()):
            if scanner._vendor is None:
                scanner._vendor = vendor
            rest = make_model[len(vendor) :].strip().lstrip("-_")
            if rest and scanner._model is None:
                scanner._model = rest
            return

    # No known vendor matched — use the whole string as model
    if scanner._model is None:
        scanner._model = make_model


def _parse_resolutions(source_el: ET.Element) -> list[int]:
    """Extract supported resolutions from a source element.

    Searches recursively — real scanners nest capabilities inside
    ``PlatenInputCaps/SettingProfiles/SettingProfile/...``.
    """
    resolutions: list[int] = []

    # Try DiscreteResolutions (recursive search)
    for discrete in source_el.iter(_ns(_SCAN_NS, "DiscreteResolutions")):
        for entry in discrete.findall(_ns(_SCAN_NS, "DiscreteResolution")):
            x_res = entry.find(_ns(_SCAN_NS, "XResolution"))
            if x_res is not None and x_res.text:
                resolutions.append(int(x_res.text))
    if resolutions:
        return sorted(set(resolutions))

    # Try ResolutionRange (min/max/step)
    for tag in ("XResolutionRange", "ResolutionRange"):
        for rng in source_el.iter(_ns(_SCAN_NS, tag)):
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
    """Extract supported color modes from a source element.

    Searches recursively through nested ``InputCaps``/``SettingProfile``
    elements and merges color modes from all profiles.
    """
    modes: list[ColorMode] = []
    for cm_el in source_el.iter(_ns(_SCAN_NS, "ColorModes")):
        for mode_el in cm_el.findall(_ns(_SCAN_NS, "ColorMode")):
            if mode_el.text and mode_el.text in _ESCL_TO_COLOR_MODE:
                cm = _ESCL_TO_COLOR_MODE[mode_el.text]
                if cm not in modes:
                    modes.append(cm)
    return modes or [ColorMode.COLOR]


def _parse_max_scan_area(source_el: ET.Element) -> ScanArea | None:
    """Extract max scan area from a source element (in 1/10 mm).

    Searches recursively for ``MaxWidth``/``MaxHeight``.
    """
    w_el = next(source_el.iter(_ns(_SCAN_NS, "MaxWidth")), None)
    h_el = next(source_el.iter(_ns(_SCAN_NS, "MaxHeight")), None)
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
    default_cm = (
        ColorMode.COLOR
        if ColorMode.COLOR in first.color_modes
        else first.color_modes[0]
    )
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

    # Color mode — request Grayscale8 for BW since many scanners don't
    # support BlackAndWhite1 via eSCL; client-side conversion to 1-bit
    # is handled by _decode_scan_response.
    escl_color = _COLOR_MODE_TO_ESCL.get(options.color_mode, "RGB24")
    if escl_color == "BlackAndWhite1":
        escl_color = "Grayscale8"
    cm = ET.SubElement(root, _ns(_SCAN_NS, "ColorMode"))
    cm.text = escl_color

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

    fmt = ET.SubElement(root, _ns(_SCAN_NS, "DocumentFormatExt"))
    fmt.text = "image/jpeg"

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


# ---------------------------------------------------------------------------
# Image decoding
# ---------------------------------------------------------------------------


def _decode_png(data: bytes) -> tuple[bytes, int, int, int]:
    """Decode a PNG image to raw pixels using stdlib zlib.

    Returns ``(raw_pixels, width, height, components)``.
    Only handles 8-bit RGB and 8-bit grayscale (no palette, no 16-bit).
    """
    import struct
    import zlib

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG file")

    width = height = 0
    color_type = bit_depth = 0
    idat_chunks: list[bytes] = []

    pos = 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # 4 length + 4 type + data + 4 crc

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(
                ">IIBB", chunk_data[:10]
            )
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if not idat_chunks or width == 0:
        raise ValueError("Invalid PNG: missing IHDR or IDAT")

    raw_filtered = zlib.decompress(b"".join(idat_chunks))

    if color_type == 2:  # RGB
        components = 3
    elif color_type == 0:  # grayscale
        components = 1
    else:
        raise ValueError(f"Unsupported PNG color type: {color_type}")

    row_bytes = width * components
    # Remove filter byte from each row
    pixels = bytearray()
    for y in range(height):
        offset = y * (row_bytes + 1)
        _filter_byte = raw_filtered[offset]
        pixels.extend(raw_filtered[offset + 1 : offset + 1 + row_bytes])

    return bytes(pixels), width, height, components


def _decode_pdf_jpeg(data: bytes) -> tuple[bytes, int, int, int]:
    """Extract and decode the first JPEG stream from a PDF.

    eSCL scanners that return PDF wrap a single JPEG image per page.
    """
    # Find JPEG start marker (FFD8) in the PDF stream
    idx = data.find(b"\xff\xd8")
    if idx < 0:
        raise ValueError("No JPEG found in PDF response")
    # Find JPEG end marker (FFD9)
    end = data.find(b"\xff\xd9", idx)
    if end < 0:
        raise ValueError("Incomplete JPEG in PDF response")
    jpeg_data = data[idx : end + 2]
    return decode_jpeg(jpeg_data)


def _decode_scan_response(
    data: bytes,
    color_mode: ColorMode,
    content_type: str = "image/jpeg",
    bw_threshold: int = 128,
) -> ScannedPage:
    """Decode scanner response into a ScannedPage.

    Handles JPEG, PNG, and PDF (extracts first image from PDF) responses.
    """
    ct = content_type.split(";")[0].strip().lower()

    if ct == "image/png":
        raw_pixels, width, height, components = _decode_png(data)
    elif ct == "application/pdf":
        raw_pixels, width, height, components = _decode_pdf_jpeg(data)
    else:
        # Default: treat as JPEG (covers image/jpeg and unknown types)
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
        raw_pixels = gray_to_bw(raw_pixels, width, height, bw_threshold)
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


def _parse_escl_id(scanner_id: str) -> _EsclConnection | None:
    """Parse an eSCL scanner ID into a connection.

    Handles ``escl:IP:PORT`` and ``escl:IP:PORT/path`` formats.
    Returns ``None`` if the ID is not a valid eSCL ID.
    """
    if not scanner_id.startswith("escl:"):
        return None
    rest = scanner_id[5:]  # strip "escl:"
    # rest is "IP:PORT" or "IP:PORT/path"
    parts = rest.split(":", 1)
    if len(parts) != 2:
        return None
    ip = parts[0]
    port_and_path = parts[1]
    # Split port from optional path
    if "/" in port_and_path:
        port_str, resource_path = port_and_path.split("/", 1)
    else:
        port_str = port_and_path
        resource_path = "eSCL"
    try:
        port = int(port_str)
    except ValueError:
        return None
    tls = port == 443
    return _EsclConnection(ip=ip, port=port, tls=tls, resource_path=resource_path)


class EsclBackend:
    backend_name = "escl"
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

            # Always include IP:port so the scanner can be reopened by ID
            # without rediscovery.
            scanner_id = f"escl:{svc.ip}:{svc.port}"

            scanners.append(
                Scanner(
                    name=svc.name,
                    vendor=None,
                    model=None,
                    backend=self.backend_name,
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

    def _ensure_connection(self, scanner: Scanner) -> _EsclConnection:
        """Get or create a connection for the scanner."""
        conn = self._connections.get(scanner.id)
        if conn is not None:
            return conn
        # Parse ID to create a new connection (for open_by_id)
        conn = _parse_escl_id(scanner.id)
        if conn is None:
            raise ScanError(f"Unknown eSCL scanner: {scanner.id}")
        self._connections[scanner.id] = conn
        return conn

    def open_scanner(self, scanner: Scanner) -> None:
        conn = self._ensure_connection(scanner)

        try:
            caps_xml = conn.get_capabilities()
        except Exception as exc:
            raise ScanError(f"Failed to get scanner capabilities: {exc}") from exc

        sources, defaults, source_names = _parse_capabilities(caps_xml)
        scanner._sources = sources
        scanner._defaults = defaults
        self._source_names[scanner.id] = source_names

        # Extract vendor/model from MakeAndModel if not already set
        _populate_make_model(caps_xml, scanner)

    def close_scanner(self, scanner: Scanner) -> None:
        conn = self._connections.get(scanner.id)
        if conn is not None:
            conn.close()

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]:
        conn = self._ensure_connection(scanner)

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
                    result = conn.get_next_document(job_path)
                except ScanAborted:
                    conn.delete_job(job_path)
                    raise
                except Exception as exc:
                    if page_num > 0 and is_feeder:
                        break  # connection error after pages = feeder empty
                    raise ScanError(f"Failed to retrieve scan data: {exc}") from exc

                if result is None:
                    break  # no more pages

                doc_data, content_type = result
                page_num += 1
                try:
                    page = _decode_scan_response(
                        doc_data,
                        options.color_mode,
                        content_type,
                        options.bw_threshold,
                    )
                except Exception as exc:
                    raise ScanError(f"Failed to decode scan data: {exc}") from exc

                check_progress(options.progress, min(page_num * 99, 99))
                yield page

                if not is_feeder:
                    break  # flatbed: one page per job

        except (ScanAborted, ScanError):
            conn.delete_job(job_path)
            raise
        except Exception as exc:
            conn.delete_job(job_path)
            raise ScanError(f"Scan failed: {exc}") from exc
        else:
            conn.delete_job(job_path)

        check_progress(options.progress, 100)

    def abort_scan(self, scanner: Scanner) -> None:
        conn = self._connections.get(scanner.id)
        if conn is not None and conn._current_job:
            conn.delete_job(conn._current_job)

    def get_scanner_ips(self) -> dict[str, str]:
        """Return scanner_id → IP mapping for deduplication."""
        return {sid: conn.ip for sid, conn in self._connections.items()}
