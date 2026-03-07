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

`_get_backend()` in `__init__.py` selects the backend by `sys.platform` and caches it globally. macOS and TWAIN backends are wrapped in thread dispatchers (`_dispatch.py`) because they have thread-affine event loops:

- **SANE**: used directly (synchronous ctypes, thread-safe)
- **macOS**: wrapped in `RunLoopDispatcher` — worker thread spins an NSRunLoop so delegate callbacks arrive on the correct thread
- **TWAIN**: wrapped in `ThreadDispatcher` — worker thread owns the hidden window handle that TWAIN requires

The dispatchers marshal all `ScanBackend` protocol calls through a `queue.Queue` to the worker thread. They also patch `scanner._backend_impl` on returned Scanner objects so subsequent calls route through the dispatcher.

### Scanner lifecycle

1. `list_scanners()` returns lightweight `Scanner` objects (no device session)
2. `scanner.open()` / `with scanner:` opens a device session; the backend populates `sources`, `resolutions`, `color_modes`, `max_page_sizes`, `defaults`
3. `scanner.scan(...)` calls the backend's `scan_pages()` which returns `list[ScannedPage]` (PNG data), then `_pdf.py` converts them into a single PDF
4. `scanner.close()` releases the session

Properties like `sources`, `resolutions`, `color_modes` raise `ScannerNotOpenError` if accessed before `open()`.

### Pages to PDF pipeline

Backends produce `ScannedPage` objects containing raw PNG bytes. `_pdf.py` parses these PNGs (implements all 5 PNG row filters), applies color mode conversion if needed (using `rgb_to_gray`/`gray_to_bw` from `_util.py`), and writes a minimal PDF 1.4 file. No external image or PDF libraries are used.

### Multi-page scanning

- **Feeder**: backends loop automatically (SANE detects "no docs" error, TWAIN checks `more_pending` flag, macOS receives all pages in one `requestScan()` with page boundaries detected by `dataStartRow` resetting to 0)
- **Flatbed multi-page**: the `next_page` callback is called after each page; return `True` to scan another

### macOS memory-based transfer

The macOS backend uses `ICScannerTransferModeMemoryBased` with `didScanToBandData:` delegate callbacks. Band data is accumulated per-page in `_ScanDelegate`, then stitched into complete images by `_assemble_image()` which produces PNG-filter-prefixed data for `raw_to_png()`.

## Conventions

- All page sizes are in 1/10 millimeters (e.g., A4 = `PageSize(2100, 2970)`)
- Backends implement the `ScanBackend` Protocol (4 methods: `list_scanners`, `open_scanner`, `close_scanner`, `scan_pages`)
- Backend modules are prefixed with `_` (private); the public API is only what `__init__.py` exports via `__all__`
- Hardware tests use `@pytest.mark.hardware` and auto-skip when no scanner is detected
- The codebase uses no external image/PDF processing libraries; all conversion is pure stdlib (struct, zlib)
