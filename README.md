# scanlib

[![Tests](https://github.com/amottola/scanlib/actions/workflows/test.yml/badge.svg)](https://github.com/amottola/scanlib/actions/workflows/test.yml)
[![Build & Publish](https://github.com/amottola/scanlib/actions/workflows/wheels.yml/badge.svg)](https://github.com/amottola/scanlib/actions/workflows/wheels.yml)
[![Documentation](https://readthedocs.org/projects/python-scanlib/badge/?version=latest)](https://python-scanlib.readthedocs.io/)

A multiplatform document scanning library for Python with platform-native scanning backends and minimal dependencies.

## Features

- **Cross-platform** — unified API across Windows (WIA 2.0), macOS (ImageCaptureCore), and Linux (SANE)
- **Output to PDF** — assemble scanned pages into a PDF and control page encoding (JPEG or PNG)
- **Minimal dependencies** — no external image or PDF processing libraries; JPEG uses platform-native encoders, PNG uses stdlib `zlib`, PDF assembly uses only the standard library
- **Multi-page scanning** — automatic document feeder support and flatbed multi-page with a simple callback
- **Page-level control** — preview, rotate, reorder, and encode individual pages as JPEG or PNG before assembling the final PDF
- **Thread-safe** — call from any thread; backend threading is handled internally
- **Progress & cancellation** — monitor scan progress and abort mid-scan via callback

## Installation

```bash
pip install scanlib
```

Python 3.9+. Pre-built wheels available for all major platforms. On Linux, `libsane` and `libjpeg-turbo` are required at runtime (`apt install libsane-dev libturbojpeg0-dev`); on other platforms, no additional dependencies are required.

## Quick Start

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
