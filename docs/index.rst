scanlib
=======

A multiplatform document scanning library for Python.

scanlib provides a unified API for document scanning across Linux (SANE),
macOS (ImageCaptureCore), and Windows (WIA 2.0), plus a cross-platform
eSCL (AirScan) backend for network scanners. It returns scanned
documents as PDF files and handles platform differences transparently.
Scanned pages can be encoded as JPEG or lossless PNG. JPEG encoding uses
platform-native encoders (ImageIO on macOS, WIC on Windows,
libjpeg compiled into the C extension on Linux), PNG encoding
uses stdlib ``zlib``, pixel conversion is handled by a bundled C
extension, and PDF assembly uses only the standard library.

scanlib also installs a ``scanlib`` command-line utility for listing
scanners, viewing capabilities, and scanning from the shell.

The project is hosted on `GitHub <https://github.com/amottola/scanlib>`_.

Requirements & Installation
---------------------------

.. code-block:: bash

   pip install scanlib

**Python 3.9** or later is required. Pre-built wheels are available for all
major platforms. When installing from source, a C compiler is needed to
build the bundled accelerator extension.

Platform backends and their Python bindings are installed automatically by pip:

- **macOS 10.7+** — ImageCaptureCore via pyobjc. JPEG encoding uses the
  built-in ImageIO framework. No additional system packages required.
- **Windows 10+** — WIA 2.0 via `comtypes <https://github.com/enthought/comtypes>`_.
  JPEG encoding uses the built-in WIC (Windows Imaging Component).
  No additional system packages required.
- **Linux** — `SANE <http://www.sane-project.org/>`_ via ctypes. JPEG
  encoding is compiled into the bundled C extension (linked against
  libjpeg at build time). Pre-built wheels include the JPEG encoder;
  building from source requires ``libjpeg-dev`` headers. SANE must be
  installed:

  .. code-block:: bash

     # Debian / Ubuntu
     apt install libsane-dev libjpeg-dev

     # Fedora / RHEL
     dnf install sane-backends libjpeg-turbo-devel

Page encoding supports JPEG and lossless PNG (this last one uses stdlib ``zlib``,
no external dependency).

.. toctree::
   :maxdepth: 2

   guide
   cli
   api
