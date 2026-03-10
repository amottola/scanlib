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
    """A single scanned page with raw pixel data.

    *data* contains raw pixel bytes with no header or wrapper —
    1 byte per pixel for grayscale (*color_type* 0, *bit_depth* 8),
    3 bytes per pixel (R, G, B) for color (*color_type* 2, *bit_depth* 8),
    or 1-bit packed (MSB first) for black & white (*color_type* 0,
    *bit_depth* 1).  *color_type* follows PNG conventions.
    """

    data: bytes
    width: int
    height: int
    color_type: int
    bit_depth: int

    @property
    def color_mode(self) -> ColorMode:
        """The color mode of this page."""
        return ColorMode.GRAY if self.color_type == 0 else ColorMode.COLOR

    def rotate(self, degrees: int) -> ScannedPage:
        """Rotate the page clockwise by 90, 180, or 270 degrees.

        Returns a new :class:`ScannedPage` with the rotated pixel data.
        For 90° and 270° rotations, width and height are swapped.
        """
        if degrees not in (90, 180, 270):
            raise ValueError(f"degrees must be 90, 180, or 270, got {degrees}")
        from _scanlib_accel import rotate_pixels

        rotated = rotate_pixels(
            self.data, self.width, self.height,
            self.color_type, self.bit_depth, degrees,
        )
        if degrees == 180:
            new_w, new_h = self.width, self.height
        else:
            new_w, new_h = self.height, self.width
        return ScannedPage(
            data=rotated, width=new_w, height=new_h,
            color_type=self.color_type, bit_depth=self.bit_depth,
        )

    def to_jpeg(self, quality: int = 85) -> bytes:
        """Encode the page as JPEG and return the bytes.

        Uses a platform-native encoder (ImageIO on macOS, WIC on
        Windows, libjpeg-turbo on Linux).  *quality* ranges from
        1 (smallest) to 100 (best).  1-bit BW pages are unpacked to
        8-bit grayscale before encoding.
        """
        from ._jpeg import encode_jpeg

        data = self.data
        ct = self.color_type
        if self.bit_depth == 1:
            from _scanlib_accel import bw_to_gray

            data = bw_to_gray(self.data, self.width, self.height)
        return encode_jpeg(data, self.width, self.height, ct, quality)

    def to_png(self) -> bytes:
        """Encode the page as lossless PNG and return the bytes.

        Uses stdlib ``zlib`` for compression — no external dependency.
        """
        w, h = self.width, self.height
        ct, bd = self.color_type, self.bit_depth

        if bd == 1:
            row_bytes = (w + 7) // 8
        elif ct == 2:
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

        ihdr_data = struct.pack(">IIBBBBB", w, h, bd, ct, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr_data)
            + _chunk(b"IDAT", compressed)
            + _chunk(b"IEND", b"")
        )


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

    def scan_pages(
        self,
        *,
        dpi: int = 300,
        color_mode: ColorMode = ColorMode.COLOR,
        page_size: PageSize | None = None,
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
        options = ScanOptions(
            dpi=dpi,
            color_mode=color_mode,
            page_size=page_size,
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
        page_size: PageSize | None = None,
        source: ScanSource | None = None,
        progress: Callable[[int], bool] | None = None,
        next_page: Callable[[int], bool] | None = None,
        image_format: ImageFormat = ImageFormat.JPEG,
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
        PDF: :attr:`ImageFormat.JPEG` (default, smaller files) or
        :attr:`ImageFormat.PNG` (lossless).

        *jpeg_quality* (1–100) controls JPEG compression quality; ignored
        when *image_format* is PNG.
        """
        pages = self.scan_pages(
            dpi=dpi,
            color_mode=color_mode,
            page_size=page_size,
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

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> Iterator[ScannedPage]: ...


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
    image_format: ImageFormat = ImageFormat.JPEG,
    jpeg_quality: int = 85,
) -> ScannedDocument:
    """Build a PDF from scanned pages.

    *pages* is any iterable of :class:`ScannedPage` objects — they may
    come directly from :meth:`Scanner.scan_pages` or from a list that
    has been reordered, filtered, etc.

    *image_format* selects the encoding for page images inside the PDF:
    :attr:`ImageFormat.JPEG` (default, smaller files) or
    :attr:`ImageFormat.PNG` (lossless).  *jpeg_quality* (1–100) controls
    JPEG compression; it is ignored when *image_format* is PNG.

    Returns a :class:`ScannedDocument` containing the PDF bytes.
    """
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
        ct = page.color_type
        bd = page.bit_depth

        if first_w == 0:
            first_w, first_h = w, h

        # Apply color mode conversion
        if color_mode == ColorMode.GRAY:
            if ct == 2:
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
            elif bd == 1:
                raw_pixels = bw_to_gray(raw_pixels, w, h)
            ct = 0
            bd = 8
        elif color_mode == ColorMode.BW:
            if ct == 2:
                raw_pixels = rgb_to_gray(raw_pixels, w, h)
                bd = 8
            if image_format == ImageFormat.JPEG:
                # JPEG can't encode 1-bit; unpack to 8-bit grayscale
                if bd == 1:
                    raw_pixels = bw_to_gray(raw_pixels, w, h)
                ct = 0
                bd = 8
            else:
                if bd == 8:
                    raw_pixels = gray_to_bw(raw_pixels, w, h)
                ct = 0
                bd = 1

        # Encode image data
        if image_format == ImageFormat.JPEG:
            img_stream = encode_jpeg(raw_pixels, w, h, ct, jpeg_quality)
            filter_name = "/DCTDecode"
            pdf_bpc = 8
        else:
            if bd == 1:
                row_bytes = (w + 7) // 8
            elif ct == 2:
                row_bytes = w * 3
            else:
                row_bytes = w

            filtered = bytearray()
            for y in range(h):
                filtered.append(0)
                src = y * row_bytes
                filtered.extend(raw_pixels[src : src + row_bytes])

            img_stream = zlib.compress(bytes(filtered))
            filter_name = "/FlateDecode"
            pdf_bpc = bd

        color_space = "/DeviceGray" if ct == 0 else "/DeviceRGB"

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
        content_bytes = (
            f"q {media_w:.4f} 0 0 {media_h:.4f} 0 0 cm /Im0 Do Q"
        ).encode()
        content_obj_id = len(objects)
        content_dict = f"<< /Length {len(content_bytes)} >>".encode()
        objects.append(
            content_dict + b"\nstream\n" + content_bytes + b"\nendstream"
        )

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
