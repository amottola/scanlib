# scanlib

A multiplatform document scanning library for Python.

scanlib provides a unified API for document scanning across Windows, macOS, and Linux, using platform-native scanning backends:

- **Linux**: SANE via ctypes (requires `libsane`)
- **macOS**: ImageCaptureCore via pyobjc (no extra install needed)
- **Windows**: TWAIN via pytwain (installed automatically)

## Installation

```bash
pip install scanlib
```

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

All scanlib operations can be called from any thread. The library internally dispatches to the correct thread for backends that require it (macOS ImageCaptureCore, Windows TWAIN).

## License

MIT
