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

# Install for development
pip install -e ".[dev]"
```

## Architecture

Scanlib is a multiplatform document scanning library. It provides a unified Python API across three platform-native backends: SANE (Linux, ctypes to libsane), ImageCaptureCore (macOS, pyobjc), and TWAIN (Windows, pytwain).

### Backend selection and thread dispatch

`_get_backend()` in `__init__.py` selects the backend by `sys.platform` and caches it globally. Each backend handles its own thread safety internally:

- **SANE**: used directly (synchronous ctypes, thread-safe)
- **macOS**: `MacOSBackend` uses a lock and main-thread dispatch — from the main thread, calls run directly; from a background thread, calls are forwarded via `performSelectorOnMainThread:withObject:waitUntilDone:` (ImageCaptureCore delivers callbacks via the main dispatch queue). Background-thread usage assumes the main thread is running a run loop.
- **TWAIN**: `TwainBackend` owns a dedicated worker thread with a `queue.Queue` — all calls are marshalled to the worker thread which owns the hidden window handle TWAIN requires

Both macOS and TWAIN backends patch `scanner._backend_impl` on returned Scanner objects so subsequent calls route through the dispatch layer.

### Scanner lifecycle

1. `list_scanners()` returns lightweight `Scanner` objects (no device session)
2. `scanner.open()` / `with scanner:` opens a device session; the backend populates `sources`, `resolutions`, `color_modes`, `max_page_sizes`, `defaults`
3. `scanner.scan(...)` calls the backend's `scan_pages()` which returns `list[ScannedPage]` (PNG data), then `_pdf.py` converts them into a single PDF
4. `scanner.close()` releases the session

Properties like `sources`, `resolutions`, `color_modes` raise `ScannerNotOpenError` if accessed before `open()`.

### Pages to PDF pipeline

Backends produce `ScannedPage` objects containing raw PNG bytes. `_pdf.py` parses these PNGs (implements all 5 PNG row filters), applies color mode conversion if needed (using `rgb_to_gray`/`gray_to_bw` from `_util.py`), and writes a minimal PDF 1.4 file. No external image or PDF libraries are used.

### Multi-page scanning

- **Feeder**: backends loop automatically (SANE detects "no docs" error, TWAIN checks `more_pending` flag, macOS receives one file per page via `didScanToURL:`)
- **Flatbed multi-page**: the `next_page` callback is called after each page; return `True` to scan another

### macOS memory-based transfer

The macOS backend uses `ICScannerTransferModeMemoryBased` with `didScanToBandData:` delegate callbacks. Band data is accumulated per-page in `_ScanDelegate`, then stitched into complete images by `_assemble_image()` which produces PNG-filter-prefixed data for `raw_to_png()`.

Key implementation details:
- `setBitDepth_(8)` **must** be called on the functional unit before scanning — without it the scanner may complete instantly with no data
- A **fresh delegate** is created for each scan in `_scan_pages_impl` — reusing the delegate from `_open_scanner_impl` (which goes through FU probing) causes `requestScan()` to complete instantly with no data
- Session open has **retry logic** (up to 3 attempts with 2s delays) because network scanners may refuse reopening immediately after a close
- The scanner may return extra components (e.g. 4-component RGBX for RGB mode); `_assemble_image()` strips the extra channel

## Conventions

- All page sizes are in 1/10 millimeters (e.g., A4 = `PageSize(2100, 2970)`)
- Backends implement the `ScanBackend` Protocol (4 methods: `list_scanners`, `open_scanner`, `close_scanner`, `scan_pages`)
- Backend modules are prefixed with `_` (private); the public API is only what `__init__.py` exports via `__all__`
- Hardware tests use `@pytest.mark.hardware` and auto-skip when no scanner is detected
- The codebase uses no external image/PDF processing libraries; all conversion is pure stdlib (struct, zlib)
