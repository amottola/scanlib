scanlib
=======

A multiplatform document scanning library for Python.

scanlib provides a unified API for document scanning across Linux (SANE),
macOS (ImageCaptureCore), and Windows (WIA 2.0). It returns scanned
documents as PDF files and handles platform differences transparently.
Scanned pages can be encoded as JPEG or lossless PNG. JPEG encoding uses
platform-native encoders (ImageIO on macOS, WIC on Windows,
`libjpeg-turbo <https://libjpeg-turbo.org/>`_ on Linux), PNG encoding
uses stdlib ``zlib``, pixel conversion is handled by a bundled C++
extension, and PDF assembly uses only the standard library.

Requirements & Installation
---------------------------

.. code-block:: bash

   pip install scanlib

**Python 3.9** or later is required. Pre-built wheels are available for all
major platforms. When installing from source, a C++11 compiler is needed to
build the bundled accelerator extension.

Platform backends and their Python bindings are installed automatically by pip:

- **Linux** — `SANE <http://www.sane-project.org/>`_ via ctypes.
  Requires ``libsane`` (``apt install libsane-dev`` / ``dnf install sane-backends``).
- **macOS 10.7+** — ImageCaptureCore via pyobjc.
- **Windows 10+** — WIA 2.0 via `comtypes <https://github.com/enthought/comtypes>`_.
- **Linux** also requires `libjpeg-turbo <https://libjpeg-turbo.org/>`_
  for JPEG encoding (``apt install libturbojpeg0-dev``).

Page encoding supports JPEG (platform-native: ImageIO on macOS, WIC on
Windows, libjpeg-turbo on Linux) and lossless PNG (stdlib ``zlib``, no
external dependency).

.. toctree::
   :maxdepth: 2

   guide
   api
