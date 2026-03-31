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

Backends
^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 12 18 18 15 37

   * - Platform
     - Backend
     - Scanner types
     - JPEG codec
     - System packages
   * - **macOS 10.7+**
     - ImageCaptureCore (pyobjc)
     - USB + network
     - ImageIO
     - None
   * - **Windows 10+**
     - WIA 2.0 (`comtypes <https://github.com/enthought/comtypes>`_)
     - USB only
     - WIC
     - None
   * - **Linux**
     - SANE (ctypes)
     - USB only
     - libjpeg (C ext)
     - ``libsane-dev libjpeg-dev``
   * - **All platforms**
     - eSCL / AirScan (HTTP)
     - Network only
     - per-platform
     - None

Platform backends and their dependencies are installed automatically by
pip.  The eSCL backend is enabled automatically on Linux and Windows
(where it complements the platform backend for network scanners).
On macOS it is opt-in via ``SCANLIB_ESCL=1`` since ImageCaptureCore
already handles network scanners natively.

On Linux, SANE must be installed at the system level:

.. code-block:: bash

   # Debian / Ubuntu
   apt install libsane-dev libjpeg-dev

   # Fedora / RHEL
   dnf install sane-backends libjpeg-turbo-devel

Page encoding supports JPEG and lossless PNG (PNG uses stdlib ``zlib``,
no external dependency).

.. toctree::
   :maxdepth: 2

   guide
   cli
   api
