from __future__ import annotations

import enum
import struct
import zlib
from collections.abc import Callable, Iterable, Iterator
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


class FeederEmptyError(ScanError):
    """The document feeder has no pages to scan."""


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


class ImageFormat(enum.Enum):
    """Image encoding format used inside the PDF."""

    PNG = "png"
    JPEG = "jpeg"


# --- Data classes ---


@dataclass(frozen=True)
class ScanArea:
    """Scan region in 1/10 millimeters.

    *x* and *y* are offsets from the top-left corner of the scanner bed.
    *width* and *height* are the dimensions of the region to scan.
    """

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ScannerDefaults:
    """Default settings detected from the device after opening."""

    dpi: int
    color_mode: ColorMode
    source: ScanSource | None


@dataclass(frozen=True)
class SourceInfo:
    """Capabilities of a single scan source.

    Each :class:`SourceInfo` bundles the supported resolutions, color
    modes, and maximum scan area for one scan source (flatbed or feeder).
    Access via :attr:`Scanner.sources` after opening the device.
    """

    type: ScanSource
    resolutions: list[int]
    color_modes: list[ColorMode]
    max_scan_area: ScanArea | None


@dataclass(frozen=True)
class ScanOptions:
    """Options for a scan operation."""

    dpi: int = 300
    color_mode: ColorMode = ColorMode.COLOR
    scan_area: ScanArea | None = None
    source: ScanSource | None = None
    progress: Callable[[int], bool] | None = None
    next_page: Callable[[int], bool] | None = None


@dataclass(frozen=True)
class ScannedPage:
    """A single scanned page with raw pixel data.

    *data* contains raw pixel bytes with no header or wrapper —
    1 byte per pixel for grayscale (:attr:`ColorMode.GRAY`),
    3 bytes per pixel (R, G, B) for color (:attr:`ColorMode.COLOR`),
    or 1-bit packed (MSB first) for black & white (:attr:`ColorMode.BW`).
    """

    data: bytes
    width: int
    height: int
    color_mode: ColorMode

    def rotate(self, degrees: int) -> ScannedPage:
        """Rotate the page clockwise by 90, 180, or 270 degrees.

        Returns a new :class:`ScannedPage` with the rotated pixel data.
        For 90° and 270° rotations, width and height are swapped.
        """
        if degrees not in (90, 180, 270):
            raise ValueError(f"degrees must be 90, 180, or 270, got {degrees}")
        from _scanlib_accel import rotate_pixels

        rotated = rotate_pixels(
            self.data,
            self.width,
            self.height,
            _BPP[self.color_mode],
            degrees,
        )
        if degrees == 180:
            new_w, new_h = self.width, self.height
        else:
            new_w, new_h = self.height, self.width
        return ScannedPage(
            data=rotated,
            width=new_w,
            height=new_h,
            color_mode=self.color_mode,
        )

    def to_jpeg(self, quality: int = 85) -> bytes:
        """Encode the page as JPEG and return the bytes.

        Uses a platform-native encoder (ImageIO on macOS, WIC on
        Windows, libjpeg on Linux).  *quality* ranges from
        1 (smallest) to 100 (best).  1-bit BW pages are unpacked to
        8-bit grayscale before encoding.
        """
        from ._jpeg import encode_jpeg

        data = self.data
        mode = self.color_mode
        if mode == ColorMode.BW:
            from _scanlib_accel import bw_to_gray

            data = bw_to_gray(self.data, self.width, self.height)
            mode = ColorMode.GRAY
        return encode_jpeg(data, self.width, self.height, mode, quality)

    def to_png(self) -> bytes:
        """Encode the page as lossless PNG and return the bytes.

        Uses stdlib ``zlib`` for compression — no external dependency.
        """
        w, h = self.width, self.height
        png_ct, png_bd = _PNG_MODE[self.color_mode]

        if self.color_mode == ColorMode.BW:
            row_bytes = (w + 7) // 8
        elif self.color_mode == ColorMode.COLOR:
            row_bytes = w * 3
        else:
            row_bytes = w

        # Prepend filter byte 0 (None) to each row, then zlib compress.
        filtered = bytearray()
        for y in range(h):
            filtered.append(0)
            src = y * row_bytes
            filtered.extend(self.data[src : src + row_bytes])
        compressed = zlib.compress(bytes(filtered))

        def _chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        ihdr_data = struct.pack(">IIBBBBB", w, h, png_bd, png_ct, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr_data)
            + _chunk(b"IDAT", compressed)
            + _chunk(b"IEND", b"")
        )


# Lookup tables for ColorMode → internal format details.
_BPP: dict[ColorMode, int] = {
    ColorMode.BW: 0,  # 1-bit packed
    ColorMode.GRAY: 1,
    ColorMode.COLOR: 3,
}
_PNG_MODE: dict[ColorMode, tuple[int, int]] = {
    ColorMode.BW: (0, 1),  # grayscale, 1-bit
    ColorMode.GRAY: (0, 8),  # grayscale, 8-bit
    ColorMode.COLOR: (2, 8),  # RGB, 8-bit
}


@dataclass(frozen=True)
class ScannedDocument:
    """Result of a scan operation.

    ``data`` contains PDF file bytes (one or more pages).
    ``width`` and ``height`` reflect the dimensions of the first page
    in pixels.  Individual pages in the PDF may differ (e.g. after
    rotation).
    """

    data: bytes
    page_count: int
    width: int
    """Width of the first page in pixels."""
    height: int
    """Height of the first page in pixels."""
    dpi: int
    color_mode: ColorMode


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
        self._sources: list[SourceInfo] = []
        self._defaults: ScannerDefaults | None = None
        self._is_open = False

    # --- Read-only properties (always available) ---

    @property
    def name(self) -> str:
        return self._name

    def __str__(self) -> str:
        """Human-readable scanner name suitable for UI display."""
        if self._vendor and self._model:
            return f"{self._vendor} {self._model}"
        return self._vendor or self._model or self._name

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
    def sources(self) -> list[SourceInfo]:
        """Available scan sources and their capabilities.

        Each entry is a :class:`SourceInfo` with ``type``,
        ``resolutions``, ``color_modes``, and ``max_scan_area``.
        Only populated after :meth:`open`.

        The first entry is the scanner's primary source (typically
        flatbed).  When :meth:`scan` or :meth:`scan_pages` is called
        without an explicit *source*, the first entry is used for
        parameter validation.
        """
        if not self._is_open:
            raise ScannerNotOpenError("Scanner must be opened before querying sources")
        return self._sources

    @property
    def defaults(self) -> ScannerDefaults | None:
        """Default settings and supported values detected from the device.

        Returns ``None`` if the backend could not determine defaults.
        Only available after :meth:`open`.
        """
        if not self._is_open:
            raise ScannerNotOpenError("Scanner must be opened before querying defaults")
        return self._defaults

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

    def scan_pages(
        self,
        *,
        dpi: int = 300,
        color_mode: ColorMode = ColorMode.COLOR,
        scan_area: ScanArea | None = None,
        source: ScanSource | None = None,
        progress: Callable[[int], bool] | None = None,
        next_page: Callable[[int], bool] | None = None,
    ) -> Iterator[ScannedPage]:
        """Scan and yield individual :class:`ScannedPage` objects.

        Each page carries raw pixel data that can be inspected, encoded
        (via :meth:`ScannedPage.to_jpeg` / :meth:`ScannedPage.to_png`),
        reordered, or later assembled into a PDF with :func:`build_pdf`.

        Parameters are the same as :meth:`scan` except that
        *image_format* and *jpeg_quality* are not applicable here.
        """
        if not self._is_open:
            raise ScannerNotOpenError("Scanner must be opened before scanning")
        # Find the matching SourceInfo for validation.
        source_types = [si.type for si in self._sources]
        if source is not None and self._sources and source not in source_types:
            raise ValueError(
                f"Unsupported source {source.value!r}; "
                f"scanner supports {[s.value for s in source_types]}"
            )
        resolved: SourceInfo | None = None
        if self._sources:
            if source is not None:
                resolved = next((si for si in self._sources if si.type == source), None)
            else:
                resolved = self._sources[0]
        if resolved is not None:
            if resolved.resolutions and dpi not in resolved.resolutions:
                raise ValueError(
                    f"Unsupported DPI {dpi}; source {resolved.type.value!r} "
                    f"supports {resolved.resolutions}"
                )
            if resolved.color_modes and color_mode not in resolved.color_modes:
                raise ValueError(
                    f"Unsupported color mode {color_mode.value!r}; "
                    f"source {resolved.type.value!r} supports "
                    f"{[m.value for m in resolved.color_modes]}"
                )
            if scan_area is not None and resolved.max_scan_area is not None:
                max_area = resolved.max_scan_area
                if scan_area.x + scan_area.width > max_area.width:
                    raise ValueError(
                        f"scan_area extends beyond scanner width: "
                        f"x={scan_area.x} + width={scan_area.width} > "
                        f"max={max_area.width}"
                    )
                if scan_area.y + scan_area.height > max_area.height:
                    raise ValueError(
                        f"scan_area extends beyond scanner height: "
                        f"y={scan_area.y} + height={scan_area.height} > "
                        f"max={max_area.height}"
                    )
        options = ScanOptions(
            dpi=dpi,
            color_mode=color_mode,
            scan_area=scan_area,
            source=source,
            progress=progress,
            next_page=next_page,
        )
        return self._backend_impl.scan_pages(self, options)

    def scan(
        self,
        *,
        dpi: int = 300,
        color_mode: ColorMode = ColorMode.COLOR,
        scan_area: ScanArea | None = None,
        source: ScanSource | None = None,
        progress: Callable[[int], bool] | None = None,
        next_page: Callable[[int], bool] | None = None,
        image_format: ImageFormat | None = None,
        jpeg_quality: int = 85,
    ) -> ScannedDocument:
        """Scan a document and return PDF bytes.

        When *source* is :attr:`ScanSource.FEEDER`, all pages in the
        document feeder are scanned.  Otherwise a single page is scanned.

        When *next_page* is provided and the source is not a feeder,
        the callback is called after each page with the number of pages
        scanned so far.  Return ``True`` to scan another page or ``False``
        to stop.

        *image_format* selects the encoding for page images inside the
        PDF: :attr:`ImageFormat.JPEG` (smaller files) or
        :attr:`ImageFormat.PNG` (lossless).  When not specified, PNG is
        used for BW mode (since 1-bit packs much smaller than JPEG) and
        JPEG for everything else.

        *jpeg_quality* (1–100) controls JPEG compression quality; ignored
        when *image_format* is PNG.
        """
        pages = self.scan_pages(
            dpi=dpi,
            color_mode=color_mode,
            scan_area=scan_area,
            source=source,
            progress=progress,
            next_page=next_page,
        )
        return build_pdf(
            pages,
            dpi=dpi,
            color_mode=color_mode,
            image_format=image_format,
            jpeg_quality=jpeg_quality,
        )

    def __repr__(self) -> str:
        state = "open" if self._is_open else "closed"
        return f"Scanner(name={self._name!r}, backend={self._backend!r}, {state})"


# --- Backend protocol ---

DISCOVERY_TIMEOUT = 15.0  # seconds for list_scanners()


class ScanBackend(Protocol):
    """Interface that all platform backends must implement."""

    def list_scanners(self, timeout: float = DISCOVERY_TIMEOUT) -> list[Scanner]: ...

    def open_scanner(self, scanner: Scanner) -> None: ...

    def close_scanner(self, scanner: Scanner) -> None: ...

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]: ...


# --- Utilities ---

MM_PER_INCH = 25.4


def check_progress(progress: Callable[[int], bool] | None, percent: int) -> None:
    """Call the progress callback; raise ScanAborted if it returns False."""
    if progress is not None and progress(percent) is False:
        raise ScanAborted("Scan aborted")


def build_pdf(
    pages: Iterable[ScannedPage],
    *,
    dpi: int = 300,
    color_mode: ColorMode = ColorMode.COLOR,
    image_format: ImageFormat | None = None,
    jpeg_quality: int = 85,
) -> ScannedDocument:
    """Build a PDF from scanned pages.

    *pages* is any iterable of :class:`ScannedPage` objects — they may
    come directly from :meth:`Scanner.scan_pages` or from a list that
    has been reordered, filtered, etc.

    *image_format* selects the encoding for page images inside the PDF:
    :attr:`ImageFormat.JPEG` (smaller files) or :attr:`ImageFormat.PNG`
    (lossless).  When not specified, PNG is used for BW mode (since
    1-bit packs much smaller than JPEG) and JPEG for everything else.
    *jpeg_quality* (1–100) controls JPEG compression; it is ignored
    when the format is PNG.

    Returns a :class:`ScannedDocument` containing the PDF bytes.
    """
    if image_format is None:
        image_format = (
            ImageFormat.PNG if color_mode == ColorMode.BW else ImageFormat.JPEG
        )
    from _scanlib_accel import bw_to_gray, gray_to_bw, rgb_to_gray

    from ._jpeg import encode_jpeg

    objects: list[bytes] = [b""]  # 1-indexed (objects[0] unused)
    objects.append(b"")  # catalog placeholder (object 1)
    objects.append(b"")  # pages placeholder (object 2)

    page_obj_ids: list[int] = []
    first_w = first_h = 0

    for page in pages:
        w, h = page.width, page.height
        raw_pixels = page.data
        mode = page.color_mode

        if first_w == 0:
            first_w, first_h = w, h

        # Apply color mode conversion
        if color_mode == ColorMode.GRAY:
            if mode == ColorMode.COLOR:
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
            elif mode == ColorMode.BW:
                raw_pixels = bw_to_gray(raw_pixels, w, h)
            mode = ColorMode.GRAY
        elif color_mode == ColorMode.BW:
            if mode == ColorMode.COLOR:
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
                mode = ColorMode.GRAY
            if image_format == ImageFormat.JPEG:
                # JPEG can't encode 1-bit; unpack to 8-bit grayscale
                if mode == ColorMode.BW:
                    raw_pixels = bw_to_gray(raw_pixels, w, h)
                mode = ColorMode.GRAY
            else:
                if mode == ColorMode.GRAY:
                    raw_pixels = gray_to_bw(raw_pixels, w, h)
                mode = ColorMode.BW

        # Encode image data
        if image_format == ImageFormat.JPEG:
            img_stream = encode_jpeg(raw_pixels, w, h, mode, jpeg_quality)
            filter_name = "/DCTDecode"
            pdf_bpc = 8
        else:
            if mode == ColorMode.BW:
                row_bytes = (w + 7) // 8
            elif mode == ColorMode.COLOR:
                row_bytes = w * 3
            else:
                row_bytes = w

            img_stream = zlib.compress(raw_pixels[: row_bytes * h])
            filter_name = "/FlateDecode"
            pdf_bpc = 1 if mode == ColorMode.BW else 8

        color_space = "/DeviceRGB" if mode == ColorMode.COLOR else "/DeviceGray"

        # Image XObject
        img_obj_id = len(objects)
        img_dict = (
            f"<< /Type /XObject /Subtype /Image "
            f"/Width {w} /Height {h} "
            f"/BitsPerComponent {pdf_bpc} "
            f"/ColorSpace {color_space} "
            f"/Filter {filter_name} "
            f"/Length {len(img_stream)} >>"
        ).encode()
        objects.append(img_dict + b"\nstream\n" + img_stream + b"\nendstream")

        # Content stream
        media_w = w * 72.0 / dpi
        media_h = h * 72.0 / dpi
        content_bytes = (f"q {media_w:.4f} 0 0 {media_h:.4f} 0 0 cm /Im0 Do Q").encode()
        content_obj_id = len(objects)
        content_dict = f"<< /Length {len(content_bytes)} >>".encode()
        objects.append(content_dict + b"\nstream\n" + content_bytes + b"\nendstream")

        # Page object
        page_obj_id = len(objects)
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {media_w:.4f} {media_h:.4f}] "
            f"/Contents {content_obj_id} 0 R "
            f"/Resources << /XObject << /Im0 {img_obj_id} 0 R >> >> >>"
        ).encode()
        objects.append(page_obj)
        page_obj_ids.append(page_obj_id)

    if not page_obj_ids:
        raise ValueError("No pages to convert")

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_ids)} >>"
    ).encode()

    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]

    for i in range(1, len(objects)):
        offsets.append(len(buf))
        buf.extend(f"{i} 0 obj\n".encode())
        buf.extend(objects[i])
        buf.extend(b"\nendobj\n")

    xref_offset = len(buf)
    buf.extend(b"xref\n")
    buf.extend(f"0 {len(objects)}\n".encode())
    buf.extend(b"0000000000 65535 f \n")
    for i in range(1, len(objects)):
        buf.extend(f"{offsets[i]:010d} 00000 n \n".encode())

    buf.extend(b"trailer\n")
    buf.extend(f"<< /Size {len(objects)} /Root 1 0 R >>\n".encode())
    buf.extend(b"startxref\n")
    buf.extend(f"{xref_offset}\n".encode())
    buf.extend(b"%%EOF\n")

    return ScannedDocument(
        data=bytes(buf),
        page_count=len(page_obj_ids),
        width=first_w,
        height=first_h,
        dpi=dpi,
        color_mode=color_mode,
    )
