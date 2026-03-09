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
```

## Architecture

Scanlib is a multiplatform document scanning library. It provides a unified Python API across three platform-native backends: SANE (Linux, ctypes to libsane), ImageCaptureCore (macOS, pyobjc), and TWAIN (Windows, pytwain).

### C accelerator extension (`_scanlib_accel`)

A required CPython C++ extension provides the performance-critical functions:

- **`encode_jpeg`** — JPEG encoding via vendored `toojpeg` (zlib license). Supports true 1-component grayscale and 4:2:0 chroma subsampling for RGB. Used as fallback when libjpeg-turbo is unavailable.
- **`rgb_to_gray`** — RGB to grayscale conversion using integer luminance formula
- **`gray_to_bw`** — grayscale to 1-bit packed conversion (threshold at 128)
- **`trim_rows`** — removes row padding from raw scan data

The extension is built from `src/accel/_scanlib_accel.cpp` and `src/accel/toojpeg.cpp`. Build configuration is in `setup.py`. The GIL is released during computation in all functions. Thread safety for the toojpeg callback (which has no context parameter) is handled via `thread_local` storage.

### JPEG acceleration (`_jpeg.py`)

`_jpeg.py` provides a unified `encode_jpeg()` that tries libjpeg-turbo (via ctypes to `libturbojpeg`) at import time and falls back to `_scanlib_accel.encode_jpeg` (toojpeg). The TurboJPEG path is ~16x faster due to SIMD. Library search uses `ctypes.util.find_library("turbojpeg")` plus platform-specific fallback paths. The module exposes `has_turbo: bool` to check which backend is active.

### Backend selection and thread dispatch

`_get_backend()` in `__init__.py` selects the backend by `sys.platform` and caches it globally. Each backend handles its own thread safety internally:

- **SANE**: used directly (synchronous ctypes, thread-safe)
- **macOS**: `MacOSBackend` uses a lock and main-thread dispatch — from the main thread, calls run directly; from a background thread, calls are forwarded via `performSelectorOnMainThread:withObject:waitUntilDone:` (ImageCaptureCore delivers callbacks via the main dispatch queue). Background-thread usage assumes the main thread is running a run loop.
- **TWAIN**: `TwainBackend` owns a dedicated worker thread with a `queue.Queue` — all calls are marshalled to the worker thread which owns the hidden window handle TWAIN requires

Both macOS and TWAIN backends patch `scanner._backend_impl` on returned Scanner objects so subsequent calls route through the dispatch layer.

### Scanner lifecycle

1. `list_scanners()` returns lightweight `Scanner` objects (no device session)
2. `scanner.open()` / `with scanner:` opens a device session; the backend populates `sources`, `resolutions`, `color_modes`, `max_page_sizes`, `defaults`
3. `scanner.scan(...)` calls `scanner.scan_pages()` which yields `ScannedPage` objects (raw pixels), then `build_pdf()` converts them into a single PDF
4. `scanner.scan_pages(...)` yields individual `ScannedPage` objects for preview/reordering workflows
5. `scanner.close()` releases the session

Properties like `sources`, `resolutions`, `color_modes` raise `ScannerNotOpenError` if accessed before `open()`.

### ScannedPage and build_pdf

Backends yield `ScannedPage` objects containing raw pixel data (no PNG wrapper). Each `ScannedPage` has `to_jpeg(quality)` and `to_png()` methods for encoding, and a `color_mode` property. The public `build_pdf()` function in `_types.py` consumes an iterable of `ScannedPage` objects, applies color mode conversion if needed (using `rgb_to_gray`/`gray_to_bw` from `_scanlib_accel`), encodes each page as JPEG or PNG, and writes a minimal PDF 1.4 file. The streaming design means only one page's raw pixels live in memory at a time.

`scanner.scan()` is a convenience that calls `scan_pages()` + `build_pdf()` internally. For page preview/rearrangement workflows, call `scan_pages()` directly, then `build_pdf()` after reordering.

### Multi-page scanning

- **Feeder**: backends loop automatically (SANE detects "no docs" error, TWAIN checks `more_pending` flag, macOS receives all pages in one `requestScan()` with page boundaries detected by `dataStartRow` resetting to 0)
- **Flatbed multi-page**: the `next_page` callback is called after each page; return `True` to scan another

### macOS memory-based transfer

The macOS backend uses `ICScannerTransferModeMemoryBased` with `scannerDevice:didScanToBandData:` delegate callbacks. Band data is accumulated per-page in `_ScanDelegate`, then stitched into complete raw images by `_assemble_image()`.

Key implementation details:
- **Bit depth must be set** on the functional unit before scanning — the backend queries `supportedBitDepths` (an `NSIndexSet`) and picks 1-bit for BW or 8-bit for gray/color. Without setting bit depth the scanner may complete instantly with no data
- A **fresh delegate** is created for each phase (open, scan, close) — reusing delegates across phases causes `requestScan()` to complete instantly with no data
- Session open has **retry logic** (up to 3 attempts with 2s delays) because network scanners may refuse reopening immediately after a close
- Open sessions are tracked via a simple `_open_sessions` set (no delegate caching needed)
- The scanner may return extra components (e.g. 4-component RGBX for RGB mode); `_assemble_image()` strips the extra channel

## Conventions

- All page sizes are in 1/10 millimeters (e.g., A4 = `PageSize(2100, 2970)`)
- Backends implement the `ScanBackend` Protocol (4 methods: `list_scanners`, `open_scanner`, `close_scanner`, `scan_pages`)
- Backend modules are prefixed with `_` (private); the public API is only what `__init__.py` exports via `__all__`
- Hardware tests use `@pytest.mark.hardware` and auto-skip when no scanner is detected
- JPEG encoding goes through `_jpeg.py` (libjpeg-turbo if available, toojpeg fallback); pixel conversion is in `_scanlib_accel`; PDF assembly is in `build_pdf()` (`_types.py`) using stdlib `zlib` for the PNG path
- `_types.py` contains all public types, exceptions, the `ScanBackend` protocol, `build_pdf()`, and shared utilities (`check_progress`, `MM_PER_INCH`)
