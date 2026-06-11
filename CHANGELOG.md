# Changelog

## 1.3.0 (unreleased)

### New features

- **`ScannerBusyError`** — raised when a scanner is already in use by
  another session or application (most scanners, network ones especially,
  allow only one scan session at a time).  Subclass of `ScanError`, so
  existing `except ScanError` handlers keep working.  Mapped from each
  backend's native busy signal: ImageCaptureCore `-47`/in-use codes
  (macOS), `SANE_STATUS_DEVICE_BUSY`, eSCL HTTP 409/503, and WIA
  `WIA_ERROR_BUSY`/`WIA_ERROR_DEVICE_LOCKED`.

### Improvements

- **The requested color mode is always honoured.**  Some scanners
  (notably over eSCL) return a richer mode than requested — e.g. RGB when
  grayscale was asked for.  `scan()` and `scan_pages()` now down-convert
  such pages to the requested mode consistently across all backends
  (COLOR → GRAY via luminance, GRAY/COLOR → BW via threshold).  The
  conversion runs only when the scanner actually returns a richer mode,
  so there is no extra cost in the common case.
- **eSCL discovery is far more reliable.**  The mDNS query is now
  retransmitted with growing intervals until a scanner resolves or the
  timeout elapses — multicast is lossy (especially over Wi-Fi) and a
  single query/response can be dropped or suppressed as a duplicate — and
  address records carried under the SRV target hostname are linked to the
  service instance across packets.
- **eSCL on macOS (`SCANLIB_ESCL=1`) now works under the composite
  backend.**  The composite pumps the ImageCaptureCore run loop while
  waiting for discovery (it was previously starved and returned nothing).
- **A network scanner found by both the platform backend and eSCL now
  appears once on every platform.**  Deduplication matches a platform
  scanner against an eSCL one by device UUID or by IP — including on
  Windows, where WIA exposes the WSD device UUID (the same UUID the
  scanner advertises over mDNS) that previously went unmatched, leaving
  the device listed twice.  By default the platform driver wins the
  duplicate on Linux/Windows and the eSCL driver wins on macOS; setting
  `SCANLIB_ESCL=1` makes eSCL win everywhere.
- **New `Scanner.uuid` property** — the lower-cased device UUID when
  available (macOS, WIA network scanners, eSCL), or `None` (SANE).  Used
  internally for the cross-backend deduplication above.
- **SANE discovery no longer probes the network.**  It now passes
  `local_only`, so SANE's network backends are not asked to enumerate
  network scanners (which the eSCL backend handles).  This also silences
  the HTTP 404 messages those backends printed to stdout/stderr during
  discovery.

### Bug fixes

- eSCL discovery no longer reports unusable, duplicate entries for
  scanners that advertise a link-local (`fe80::…`) address — link-local
  addresses are not connectable without a zone scope, so they are skipped
  and IPv4 is preferred.

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
- **`scanlib.open_scanner(id)`** — open a scanner directly by its ID
  without running discovery.  Instant for eSCL and SANE; runs quick
  targeted discovery on macOS ImageCaptureCore.
- **`scanlib reset`** — CLI command to cancel stale eSCL scan jobs.
- **Configurable BW threshold** — `bw_threshold` parameter (0–255) on
  `scan()`, `scan_pages()`, and `build_pdf()` controls the
  grayscale-to-BW cutoff.  CLI: `--bw-threshold`.  Default is 128.

### Improvements

- SANE and WIA backends no longer discover network scanners — this is
  now handled by the eSCL backend via a composite backend that runs
  both in parallel and deduplicates by IP.
- mDNS browse exits early after a 0.5s quiet period instead of waiting
  the full timeout.

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
