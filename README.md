# scanlib

[![Tests](https://github.com/amottola/scanlib/actions/workflows/test.yml/badge.svg)](https://github.com/amottola/scanlib/actions/workflows/test.yml)
[![Build & Publish](https://github.com/amottola/scanlib/actions/workflows/wheels.yml/badge.svg)](https://github.com/amottola/scanlib/actions/workflows/wheels.yml)
[![Documentation](https://readthedocs.org/projects/python-scanlib/badge/?version=latest)](https://python-scanlib.readthedocs.io/)

A multiplatform document scanning library for Python.

scanlib provides a unified API for document scanning across Windows, macOS, and Linux, using platform-native scanning backends. It was designed with minimal dependencies as a core goal — the library uses no external image or PDF processing libraries. Scanned pages can be encoded as JPEG or lossless PNG, and assembled into multi-page PDFs. JPEG encoding uses platform-native encoders (ImageIO on macOS, WIC on Windows, [libjpeg-turbo](https://libjpeg-turbo.org/) on Linux), PNG encoding uses stdlib `zlib`, pixel conversion is handled by a bundled C extension, and PDF assembly uses only the standard library.

## Requirements & Installation

```bash
pip install scanlib
```

**Python 3.9** or later is required. Pre-built wheels are available for all major platforms. When installing from source, a C compiler is needed to build the bundled accelerator extension.

Platform backends and their Python bindings are installed automatically by pip:

| Platform | Backend | Python dependency | System requirement |
|----------|---------|-------------------|--------------------|
| **Linux** | SANE (ctypes) | *none* | `libsane` (`apt install libsane-dev`), `libjpeg-turbo` (`apt install libturbojpeg0-dev`) |
| **macOS 10.7+** | ImageCaptureCore (pyobjc) | `pyobjc-framework-ImageCaptureCore` (auto) | *none* |
| **Windows 10+** | WIA 2.0 ([comtypes](https://github.com/enthought/comtypes)) | `comtypes` (auto) | *none* |

Page encoding supports JPEG (platform-native: ImageIO on macOS, WIC on Windows, libjpeg-turbo on Linux) and lossless PNG (stdlib `zlib`, no external dependency).

## Quick Start

```python
import scanlib

# Discover scanners
scanners = scanlib.list_scanners()
print(scanners)  # [Scanner(name='...', backend='sane', closed)]

# Scan a document
with scanners[0] as scanner:
    doc = scanner.scan()

# doc.data contains PDF bytes
with open("output.pdf", "wb") as f:
    f.write(doc.data)
```

## Scan Options

```python
from scanlib import ColorMode, PageSize, ScanSource

with scanners[0] as scanner:
    doc = scanner.scan(
        dpi=600,
        color_mode=ColorMode.GRAY,
        page_size=PageSize(2100, 2970),  # A4 in 1/10 mm
        source=ScanSource.FLATBED,
    )
```

## Scanner Capabilities

After opening a scanner you can query its capabilities:

```python
with scanners[0] as scanner:
    print(scanner.sources)        # [ScanSource.FLATBED, ScanSource.FEEDER]
    print(scanner.resolutions)    # [150, 300, 600, 1200]
    print(scanner.color_modes)    # [ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW]
    print(scanner.max_page_sizes) # {ScanSource.FLATBED: PageSize(2159, 2972)}
    print(scanner.defaults)       # ScannerDefaults(dpi=300, ...)
```

## Feeder Scanning

When scanning from a document feeder, all pages are scanned automatically:

```python
with scanners[0] as scanner:
    doc = scanner.scan(source=ScanSource.FEEDER)
    print(doc.page_count)
```

## Multi-Page Flatbed Scanning

Use the `next_page` callback to scan multiple pages one at a time:

```python
def prompt_next(pages_so_far: int) -> bool:
    return input(f"{pages_so_far} page(s) scanned. Add another? [y/n] ") == "y"

with scanners[0] as scanner:
    doc = scanner.scan(next_page=prompt_next)
    # doc is a single multi-page PDF
```

## Page-Level Scanning

Use `scan_pages()` for page preview, rotation, and reordering before assembling the final PDF:

```python
import scanlib

with scanners[0] as scanner:
    pages = list(scanner.scan_pages())

# Preview each page
for i, page in enumerate(pages):
    with open(f"page_{i}.jpg", "wb") as f:
        f.write(page.to_jpeg())

# Rotate a page 90° clockwise
pages[0] = pages[0].rotate(90)

# Reorder, filter, then build the final PDF
pages.reverse()
doc = scanlib.build_pdf(pages, dpi=300)
with open("output.pdf", "wb") as f:
    f.write(doc.data)
```

## Progress Callback

Monitor scan progress. Return `False` to abort:

```python
def on_progress(percent: int) -> bool:
    print(f"Scanning... {percent}%")
    return True  # return False to abort

with scanners[0] as scanner:
    doc = scanner.scan(progress=on_progress)
```

## Thread Safety

All scanlib operations can be called from any thread. The library internally dispatches to the correct thread for backends that require it (macOS ImageCaptureCore, Windows WIA).

## Documentation

Full documentation is available at [python-scanlib.readthedocs.io](https://python-scanlib.readthedocs.io/).

## About

Created by Angelo Mottola, with the help of [Claude Code](https://claude.ai/code).

This project was started to fill a void in the Python scanning ecosystem, which was very much fragmented. It is also my first experiment in heavily AI-assisted software development (I still hesitate to use the term "vibe" coding), where I mostly did code review and direction.

## License

MIT
