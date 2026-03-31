# Changelog

## 1.2.0

### New features

- **eSCL (AirScan) backend** — network scanners are discovered via mDNS
  and driven directly over HTTP/HTTPS using the eSCL protocol.  No OS-level
  scanner drivers needed for network devices.  Enabled automatically on
  Linux and Windows; opt-in on macOS via `SCANLIB_ESCL=1`.
- **Command-line interface** — `scanlib list`, `scanlib info`, and
  `scanlib scan` for listing scanners, viewing capabilities, and scanning
  from the shell.  Installed as a console script via pip.  Supports all
  scan options (DPI, color mode, source, scan area, format, quality,
  multi-page) with progress reporting.
- **JPEG decoding** — platform-native JPEG decoders added to `_jpeg.py`
  (ImageIO on macOS, WIC on Windows, libjpeg on Linux) for the eSCL
  backend.

### Improvements

- SANE and WIA backends no longer discover network scanners — this is
  now handled by the eSCL backend via a composite backend that runs
  both in parallel and deduplicates by IP.

## 1.1.0

### New features

- **`scanner.id`** — unique device identifier (SANE device URI, macOS UUID,
  WIA device ID).  Use this to distinguish identical scanner models on a
  network.
- **`scanner.location`** — free-form location string.  On macOS this comes
  from `ICDevice.locationDescription`; on Linux/Windows from the mDNS `note`
  TXT record via a built-in multicast DNS client (no external dependencies).
- **`scanner.abort()`** — cancel an in-progress scan from any thread.
  Triggers `ScanAborted` on the scanning thread.  Safe to call even when no
  scan is running.
- **`list_scanners(cancel=...)`** — pass a `threading.Event` to interrupt
  discovery early from another thread.

### Improvements

- **macOS backend no longer freezes GUI applications.**  All
  ImageCaptureCore work now runs on a background worker thread; the main
  thread is only used for short ICC API dispatches.  Qt, Tk, and other
  event-loop-based applications remain responsive during scanning.
- **`scan_pages()` yields each page before calling `next_page`**, so callers
  can preview or process a page before deciding whether to continue.
  Previously the callback was invoked inside the backend before the page was
  returned.
- **Resolution lists are normalized** — backends that report a continuous
  range (e.g. 75–1200 step 1) now return standard DPI values
  (75, 100, 150, 200, 300, 600, 1200, …) instead of huge lists.
- **`__str__`** returns `location` when available, falling back to
  vendor/model or name.
- **WIA `max_scan_area` always populated** via a 4-level property fallback.
- All `Scanner` properties now have docstrings for Sphinx autodoc.

### Bug fixes

- Fixed macOS `ICDevice.location` — was using the wrong property name.
- Generalized URI IP extraction to work with any SANE backend, not just HP.
- `next_page` removed from `ScanOptions` (no longer leaks into backends).
- WIA backend no longer stores a private `_device_id` attribute; uses
  `scanner.id` instead.

## 1.0.0

Initial release.

- Unified Python API for document scanning across SANE (Linux),
  ImageCaptureCore (macOS), and WIA 2.0 (Windows).
- C accelerator extension (`_scanlib_accel`) for pixel conversion, BMP
  parsing, and JPEG encoding.
- Platform-native JPEG encoding (ImageIO on macOS, WIC on Windows,
  libjpeg on Linux) and lossless PNG via stdlib zlib.
- `Scanner` class with context-manager protocol, per-source capabilities
  (`SourceInfo`), and device defaults (`ScannerDefaults`).
- `scan_pages()` for page-level access with `ScannedPage.to_jpeg()`,
  `to_png()`, and `rotate()`.
- `build_pdf()` for assembling pages into a minimal PDF 1.4 file.
- Multi-page scanning via document feeder (automatic) and flatbed
  (`next_page` callback).
- Progress reporting and abort via callback.
- Thread-safe on all platforms (macOS main-thread dispatch, WIA STA
  worker thread).
- Pre-built wheels for Python 3.9–3.14 on Linux, macOS, and Windows
  (including free-threaded builds).
