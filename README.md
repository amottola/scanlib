# scanlib

[![Tests](https://github.com/amottola/scanlib/actions/workflows/test.yml/badge.svg)](https://github.com/amottola/scanlib/actions/workflows/test.yml)
[![Build & Publish](https://github.com/amottola/scanlib/actions/workflows/wheels.yml/badge.svg)](https://github.com/amottola/scanlib/actions/workflows/wheels.yml)
[![Documentation](https://readthedocs.org/projects/python-scanlib/badge/?version=latest)](https://python-scanlib.readthedocs.io/)

A multiplatform document scanning library for Python with platform-native scanning backends and minimal dependencies.

## Features

- **Cross-platform** — unified API across Windows (WIA 2.0), macOS (ImageCaptureCore), and Linux (SANE)
- **eSCL / AirScan** — direct HTTP scanning of network scanners without OS drivers, enabled automatically on Linux and Windows
- **Command-line interface** — `scanlib list`, `scanlib info`, and `scanlib scan` for quick scanning from the shell
- **Output to PDF** — assemble scanned pages into a PDF and control page encoding (JPEG or PNG)
- **Minimal dependencies** — no external image or PDF processing libraries; JPEG uses platform-native encoders, PNG uses stdlib `zlib`, PDF assembly uses only the standard library
- **Multi-page scanning** — automatic document feeder support and flatbed multi-page with a simple callback
- **Page-level control** — preview, rotate, reorder, and encode individual pages as JPEG or PNG before assembling the final PDF
- **Thread-safe** — call from any thread; backend threading is handled internally
- **Progress & cancellation** — monitor scan progress and abort mid-scan via callback

## Backends

| Platform | Backend | Scanner types | eSCL | System packages |
|---|---|---|---|---|
| **macOS 10.7+** | ImageCaptureCore | USB + network | Opt-in (`SCANLIB_ESCL=1`) | None |
| **Windows 10+** | WIA 2.0 | USB | Always enabled | None |
| **Linux** | SANE | USB | Always enabled | `libsane-dev libjpeg-dev` |

The eSCL (AirScan) backend discovers and drives network scanners directly over HTTP — no OS-level scanner drivers needed. On Linux and Windows it runs alongside the platform backend automatically. On macOS, ImageCaptureCore already handles network scanners natively; set `SCANLIB_ESCL=1` to use the eSCL backend instead.

## Installation

```bash
pip install scanlib
```

Python 3.9+. Pre-built wheels available for all major platforms. On Linux, SANE and libjpeg must be installed at the system level:

```bash
# Debian / Ubuntu
apt install libsane-dev libjpeg-dev

# Fedora / RHEL
dnf install sane-backends libjpeg-turbo-devel
```

On macOS and Windows, no additional system packages are required.

## Quick Start

### Command line

```bash
# List available scanners
scanlib list

# Show scanner capabilities
scanlib info -s 0

# Scan to PDF
scanlib scan -o document.pdf --dpi 300 --color-mode gray

# Multi-page flatbed scan with interactive prompting
scanlib scan -o multipage.pdf --pages ask
```

### Python API

```python
import scanlib

scanners = scanlib.list_scanners()

with scanners[0] as scanner:
    doc = scanner.scan()

with open("output.pdf", "wb") as f:
    f.write(doc.data)
```

## Documentation

Full documentation is available at [python-scanlib.readthedocs.io](https://python-scanlib.readthedocs.io/).

## About

Created by Angelo Mottola, with the help of [Claude Code](https://claude.ai/code).

This project was started to fill a void in the Python scanning ecosystem, which I found to be very much fragmented. It is also my first experiment in heavily AI-assisted software development (I still hesitate to use the term "vibe" coding), where I mostly did code review and direction.

## License

MIT
