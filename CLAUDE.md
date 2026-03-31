# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests (excluding hardware tests that need a physical scanner)
pytest tests/ --ignore=tests/test_hardware.py -v

# Run a single test file
pytest tests/test_types.py -v

# Run a single test
pytest tests/test_types.py::TestScanner::test_context_manager -v

# Run hardware integration tests (requires connected scanner)
pytest tests/test_hardware.py -v

# Build Sphinx docs
pip install -e ".[docs]"
cd docs && make html

# Install for development (builds C extension)
pip install -e ".[dev]"

# Run pre-commit hooks on all files
pre-commit run --all-files

# Code is formatted with Black (runs automatically via pre-commit)
python -m black .
```

## Architecture

Scanlib is a multiplatform document scanning library. It provides a unified Python API across four backends: SANE (Linux, ctypes to libsane), ImageCaptureCore (macOS, pyobjc), WIA 2.0 (Windows, comtypes + ctypes), and eSCL/AirScan (cross-platform, direct HTTP to network scanners).

### C accelerator extension (`_scanlib_accel`)

A required CPython C extension provides pixel conversion, BMP parsing, and JPEG encoding:

- **`rgb_to_gray`** — RGB to grayscale conversion using integer luminance formula
- **`rgb_to_bgr`** — RGB to BGR channel swap (used by WIC encoder on Windows)
- **`gray_to_bw`** — grayscale to 1-bit packed conversion (threshold at 128)
- **`bw_to_gray`** — 1-bit packed to 8-bit grayscale unpacking (0→0, 1→255)
- **`trim_rows`** — removes row padding from raw scan data
- **`rotate_pixels`** — clockwise pixel rotation (90°/180°/270°) for 8-bit grayscale, RGB, and 1-bit BW
- **`bmp_to_raw`** — BMP file to raw pixel conversion (handles 1/8/24/32-bit BMPs, BGR→RGB swap, bottom-up reordering)
- **`encode_jpeg`** — JPEG encoding via libjpeg (Linux only, compiled conditionally via `#ifdef HAVE_JPEGLIB`)
- **`decode_jpeg`** — JPEG decoding via libjpeg (Linux only, `#ifdef HAVE_JPEGLIB`). Returns `(raw_pixels, width, height, components)`. Used by the eSCL backend to decode scanner responses.

The extension is built from `src/accel/_scanlib_accel.c`. Build configuration is in `setup.py`. On Linux, `setup.py` links against libjpeg and defines `HAVE_JPEGLIB` to enable the JPEG encoder/decoder. The GIL is released during computation in all functions.

### Page encoding (`_jpeg.py` and `_types.py`)

`_jpeg.py` provides unified `encode_jpeg()` and `decode_jpeg()` using platform-native codecs with no fallback chain:

- **macOS**: ImageIO framework (always available, via ctypes)
- **Windows**: WIC — Windows Imaging Component (always available, via raw COM vtable calls with ctypes). Uses `IWICImagingFactory`/`IWICBitmapEncoder`/`IWICBitmapFrameEncode`. The factory is created lazily on first encode to avoid COM apartment conflicts with comtypes (which defaults to STA). Quality is set via `IPropertyBag2::Write` with `"ImageQuality"` property. Color images require RGB→BGR conversion via `rgb_to_bgr` from `_scanlib_accel`.
- **Linux**: libjpeg, compiled into the `_scanlib_accel` C extension at build time (requires `libjpeg-dev` headers). The `_jpeg.py` Linux branch is a thin wrapper that calls `_scanlib_accel.encode_jpeg`.

The file is structured as a single `if sys.platform` block that defines `encode_jpeg()` and `decode_jpeg()` directly for each platform — no dispatch function or boolean flags. `decode_jpeg()` returns `(raw_pixels, width, height, components)` and is used by the eSCL backend to decode JPEG responses from scanners.

PNG encoding is handled in `build_pdf()` (`_types.py`) using stdlib `zlib` for deflate compression — no external dependency. Each `ScannedPage` exposes both `to_jpeg(quality)` and `to_png()` methods. When `image_format` is not specified, `build_pdf()` defaults to PNG for BW mode (1-bit packs much smaller than JPEG's 8-bit grayscale) and JPEG for color/grayscale.

### Backend selection and thread dispatch

`_get_backend()` in `__init__.py` selects the backend by `sys.platform` and caches it globally. On Linux and Windows, a `_CompositeBackend` wraps the platform backend together with the eSCL backend — `list_scanners()` runs both in parallel and deduplicates by IP address. On macOS, only `MacOSBackend` is used by default (ImageCaptureCore already handles eSCL natively); setting `SCANLIB_ESCL=1` enables the composite backend on macOS too. Each backend handles its own thread safety internally:

- **SANE**: used directly (synchronous ctypes, thread-safe)
- **macOS**: `MacOSBackend` uses a lock and main-thread dispatch — from the main thread, calls run directly; from a background thread, calls are forwarded via `performSelectorOnMainThread:withObject:waitUntilDone:` (ImageCaptureCore delivers callbacks via the main dispatch queue). Background-thread usage assumes the main thread is running a run loop.
- **WIA**: `WiaBackend` owns a dedicated STA worker thread with a Win32 message pump (`MsgWaitForMultipleObjects` + `PeekMessage`/`DispatchMessage`) — all calls are marshalled to the worker thread which owns the COM apartment. The message pump is required because WIA COM objects are apartment-threaded and need message processing for COM marshaling. A Win32 event is used to signal work availability to the message loop.
- **eSCL**: `EsclBackend` uses direct HTTP(S) via stdlib `http.client`. No threading requirements — all calls are synchronous on the caller's thread. Self-signed TLS certificates are accepted (common for consumer scanners).

Both macOS and WIA backends patch `scanner._backend_impl` on returned Scanner objects so subsequent calls route through the dispatch layer. The composite backend delegates each scanner's operations to whichever backend discovered it.

### Scanner lifecycle

1. `list_scanners()` returns lightweight `Scanner` objects (no device session). `str(scanner)` returns `location` if available, otherwise `vendor model` or `name`. Each Scanner has `id` (unique per device: device URI on SANE, UUID on macOS, WIA device ID on Windows, `escl:{uuid}` or `escl:{ip}:{port}` on eSCL), `name` (platform-specific: device URI on SANE, display name on macOS/WIA, `ty` TXT record on eSCL), `vendor` (scanner manufacturer when available: SANE and macOS; None on WIA/eSCL), `model` (SANE only; None on macOS/WIA/eSCL), and `location` (free-form string from mDNS `note` or macOS `locationDescription`).
2. `scanner.open()` / `with scanner:` opens a device session; the backend populates `sources` (list of `SourceInfo`) and `defaults`
3. `scanner.scan(...)` calls `scanner.scan_pages()` which yields `ScannedPage` objects (raw pixels), then `build_pdf()` converts them into a single PDF
4. `scanner.scan_pages(...)` yields individual `ScannedPage` objects for preview/reordering workflows
5. `scanner.abort()` cancels an in-progress scan from any thread; raises `ScanAborted` on the scanning thread
6. `scanner.close()` releases the session

`scanner.sources` returns `list[SourceInfo]` where each `SourceInfo` bundles `type` (ScanSource), `resolutions`, `color_modes`, and `max_scan_area` for one source. `sources` and `defaults` raise `ScannerNotOpenError` if accessed before `open()`.

### ScannedPage and build_pdf

Backends yield `ScannedPage` objects containing raw pixel data (no PNG wrapper). Each page has a `color_mode` field (`ColorMode.COLOR` for 3-byte RGB, `ColorMode.GRAY` for 1-byte grayscale, `ColorMode.BW` for 1-bit packed). Each `ScannedPage` has `to_jpeg(quality)` and `to_png()` methods for encoding and a `rotate(degrees)` method for clockwise rotation (90/180/270). The public `build_pdf()` function in `_types.py` consumes an iterable of `ScannedPage` objects, applies color mode conversion if needed (using `rgb_to_gray`/`gray_to_bw` from `_scanlib_accel`), encodes each page as JPEG or PNG, and writes a minimal PDF 1.4 file. The streaming design means only one page's raw pixels live in memory at a time.

`scanner.scan()` is a convenience that calls `scan_pages()` + `build_pdf()` internally. For page preview/rearrangement workflows, call `scan_pages()` directly, then `build_pdf()` after reordering.

### Multi-page scanning

- **Feeder**: backends loop automatically (SANE detects "no docs" error, WIA catches `WIA_ERROR_PAPER_EMPTY` exception, macOS receives all pages in one `requestScan()` with page boundaries detected by `dataStartRow` resetting to 0)
- **Flatbed multi-page**: `Scanner.scan_pages()` owns the `next_page` loop — each page is yielded before the callback is invoked, so the caller can preview/process the page before deciding whether to continue. Backends scan one flatbed round per call; `scan_pages()` calls the backend again if `next_page` returns `True`

### macOS memory-based transfer

The macOS backend uses `ICScannerTransferModeMemoryBased` with `scannerDevice:didScanToBandData:` delegate callbacks. Band data is accumulated per-page in `_ScanDelegate`, then stitched into complete raw images by `_assemble_image()`.

Key implementation details:
- **Bit depth must be set** on the functional unit before scanning — the backend queries `supportedBitDepths` (an `NSIndexSet`) and picks 1-bit for BW or 8-bit for gray/color. Without setting bit depth the scanner may complete instantly with no data
- A **fresh delegate** is created for each phase (open, scan, close) — reusing delegates across phases causes `requestScan()` to complete instantly with no data
- Session open has **retry logic** (up to 3 attempts with 2s delays) because network scanners may refuse reopening immediately after a close
- Open sessions are tracked via a simple `_open_sessions` set (no delegate caching needed)
- The scanner may return extra components (e.g. 4-component RGBX for RGB mode); `_assemble_image()` strips the extra channel

### WIA 2.0 streamed transfer

The WIA backend uses the WIA 2.0 low-level COM interfaces (`IWiaTransfer` + `IWiaTransferCallback`) for streamed, memory-based transfers with progress reporting. COM interface definitions are declared using comtypes with vtable order verified against the Windows SDK `wia_lh.h` header.

Key interfaces:
- **`IWiaDevMgr2`** — device enumeration (`EnumDeviceInfo`) and connection (`CreateDevice`)
- **`IWiaPropertyStorage`** — property access via `ReadMultiple`/`WriteMultiple`/`GetPropertyAttributes` with `PROPSPEC`/`PROPVARIANT` ctypes structures
- **`IWiaItem2`** — represents device/scan items; `EnumChildItems` to get scannable items
- **`IWiaTransfer`** — `Download()` initiates a blocking scan that invokes callbacks
- **`IWiaTransferCallback`** — implemented as a `comtypes.COMObject`; `TransferCallback` receives progress (`lPercentComplete` 0-100), `GetNextStream` is called once per page to provide a memory-backed `IStream` (via `CreateStreamOnHGlobal`)

Transfer flow: `Download()` blocks while invoking `GetNextStream` (once per page) and `TransferCallback` (progress + end-of-stream signals). On `END_OF_STREAM`, the BMP data is read from the `IStream` via `GetHGlobalFromStream` and converted to raw pixels using `_bmp_to_raw` from the C accelerator extension. BMP format is used for maximum device compatibility.

Max scan area is determined via a 4-level fallback chain in `_read_wia_max_scan_area`: (1) WIA 2.0 item-level `WIA_IPS_MAX_HORIZONTAL/VERTICAL_SIZE`, (2) WIA 1.0 device-level `WIA_DPS_MAX_HORIZONTAL/VERTICAL_SIZE`, (3) derived from `XEXTENT`/`YEXTENT` property range max + current resolution, (4) fallback to the bounding box of US Letter and A4 (2159 x 2970 in 1/10 mm). This ensures `max_scan_area` is always populated.

The module guards all Windows-specific imports (`comtypes`, `ctypes.wintypes`, `ctypes.windll`) with try/except and provides stubs so it can be imported on non-Windows for testing.

### eSCL (AirScan) direct HTTP backend

The eSCL backend (`backends/_escl.py`) communicates directly with network scanners using the eSCL protocol over HTTP/HTTPS. No OS-level scanner drivers are required. Uses only stdlib modules (`http.client`, `xml.etree`, `ssl`).

**Discovery**: Uses `discover_escl_services()` from `_mdns.py` to browse for `_uscan._tcp` and `_uscans._tcp` mDNS services. Extracts IP, port, TLS flag, resource path (`rs` TXT record), device name (`ty`), location (`note`), and UUID for deduplication. Services discovered under `_uscans._tcp` use HTTPS.

**Capabilities**: `GET /<rs>/ScannerCapabilities` returns XML with `<scan:Platen>` and `<scan:Adf>` elements. The parser extracts discrete resolutions or normalizes resolution ranges, color modes (`BlackAndWhite1`/`Grayscale8`/`RGB24`), and max scan area. All eSCL units are 1/300 inch; conversion to scanlib's 1/10 mm: `tenths_mm = round(escl * 254 / 300)`.

**Scanning**: `POST /<rs>/ScanJobs` with XML settings creates a job (201 + Location header). `GET <job>/NextDocument` retrieves pages as JPEG. For feeder scanning, NextDocument is called in a loop until 404 (no more pages). For flatbed, one page per job. Abort sends `DELETE <job>`.

**Image decoding**: Scanner JPEG responses are decoded to raw pixels using `decode_jpeg()` from `_jpeg.py` (platform-native: ImageIO on macOS, WIC on Windows, libjpeg on Linux). If BW mode was requested, the decoded grayscale is converted to 1-bit packed using `gray_to_bw` from the C extension.

### mDNS service discovery (`_mdns.py`)

Built-in multicast DNS client using only `socket` and `struct`. Sends PTR queries for `_uscan._tcp.local.` and `_uscans._tcp.local.` to `224.0.0.251:5353` and parses responses for PTR, TXT, SRV, A, and AAAA records.

Two public APIs:
- `get_location_map(timeout)` → `LocationMap` with IP→note and name→note mappings (used by SANE/WIA backends for the `location` property)
- `discover_escl_services(timeout)` → `list[EsclServiceInfo]` with full service details (used by the eSCL backend)

Both share a common `_browse_mdns()` that performs the network I/O. Results from `get_location_map` are cached for 60 seconds via `browse_in_thread()`. SRV records provide both the port and the target hostname for address resolution.

## Conventions

- All scan areas are in 1/10 millimeters (e.g., full A4 = `ScanArea(0, 0, 2100, 2970)`)
- Backends implement the `ScanBackend` Protocol (5 methods: `list_scanners`, `open_scanner`, `close_scanner`, `scan_pages`, `abort_scan`)
- Backend modules are prefixed with `_` (private); the public API is only what `__init__.py` exports via `__all__`
- Hardware tests use `@pytest.mark.hardware` and auto-skip when no scanner is detected
- Page encoding supports JPEG via `_jpeg.py` (platform-native: ImageIO on macOS, WIC on Windows, libjpeg on Linux) and lossless PNG via stdlib `zlib`; pixel conversion is in `_scanlib_accel`; JPEG decoding via `decode_jpeg()` in `_jpeg.py`; PDF assembly is in `build_pdf()` (`_types.py`)
- `_types.py` contains all public types, exceptions, the `ScanBackend` protocol, `build_pdf()`, and shared utilities (`check_progress`, `MM_PER_INCH`)
