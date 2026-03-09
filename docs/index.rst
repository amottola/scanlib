scanlib
=======

A multiplatform document scanning library for Python.

scanlib provides a unified API for document scanning across Linux (SANE),
macOS (ImageCaptureCore), and Windows (TWAIN). It returns scanned documents
as PDF files and handles platform differences transparently. JPEG encoding
and pixel conversion are handled by a bundled C++ extension, with optional
`libjpeg-turbo <https://libjpeg-turbo.org/>`_ acceleration for high-resolution scans.

Requirements & Installation
--------------------------

.. code-block:: bash

   pip install scanlib

**Python 3.9** or later is required. Pre-built wheels are available for all
major platforms. When installing from source, a C++11 compiler is needed to
build the bundled accelerator extension.

Platform backends and their Python bindings are installed automatically by pip:

- **Linux** — `SANE <http://www.sane-project.org/>`_ via ctypes.
  Requires ``libsane`` (``apt install libsane-dev`` / ``dnf install sane-backends``).
- **macOS 10.7+** — ImageCaptureCore via pyobjc.
- **Windows 7+** — TWAIN via `pytwain <https://github.com/denisenkom/pytwain>`_.

On macOS, JPEG encoding uses the built-in ImageIO framework automatically.
On Linux and Windows, install `libjpeg-turbo <https://libjpeg-turbo.org/>`_
for faster JPEG encoding (``apt install libturbojpeg0-dev`` on Linux).
It is detected automatically at runtime.

Basic Usage
-----------

List available scanners and scan a document:

.. code-block:: python

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

Scan Options
------------

Customize the scan with keyword arguments:

.. code-block:: python

   from scanlib import ColorMode, PageSize, ScanSource

   with scanners[0] as scanner:
       doc = scanner.scan(
           dpi=600,
           color_mode=ColorMode.GRAY,
           page_size=PageSize(2100, 2970),  # A4 in 1/10 mm
           source=ScanSource.FLATBED,
       )

Scanner Capabilities
--------------------

After opening a scanner, you can query its capabilities:

.. code-block:: python

   with scanners[0] as scanner:
       print(scanner.sources)        # [ScanSource.FLATBED, ScanSource.FEEDER]
       print(scanner.resolutions)    # [150, 300, 600, 1200]
       print(scanner.color_modes)    # [ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW]
       print(scanner.max_page_sizes) # {ScanSource.FLATBED: PageSize(2159, 2972)}
       print(scanner.defaults)       # ScannerDefaults(dpi=300, ...)

Feeder Scanning
---------------

When scanning from a document feeder, all pages are scanned automatically:

.. code-block:: python

   with scanners[0] as scanner:
       doc = scanner.scan(source=ScanSource.FEEDER)
       print(doc.page_count)  # Number of pages in the feeder

Multi-Page Flatbed Scanning
---------------------------

Use the ``next_page`` callback to scan multiple pages one at a time on a
flatbed scanner. The callback receives the number of pages scanned so far
and returns ``True`` to continue or ``False`` to stop:

.. code-block:: python

   def prompt_next(pages_so_far: int) -> bool:
       return input(f"{pages_so_far} page(s) scanned. Add another? [y/n] ") == "y"

   with scanners[0] as scanner:
       doc = scanner.scan(next_page=prompt_next)
       # doc is a single multi-page PDF

Page-Level Scanning
-------------------

Use ``scan_pages()`` to receive individual pages as they arrive.
Each ``ScannedPage`` carries raw pixel data and can be encoded as
JPEG or PNG for previewing. After reviewing and reordering, assemble
a PDF with ``build_pdf()``:

.. code-block:: python

   import scanlib

   with scanners[0] as scanner:
       pages = list(scanner.scan_pages())

   # Preview each page
   for i, page in enumerate(pages):
       with open(f"page_{i}.jpg", "wb") as f:
           f.write(page.to_jpeg())

   # Reorder, filter, then build the final PDF
   pages.reverse()
   doc = scanlib.build_pdf(pages, dpi=300)
   with open("output.pdf", "wb") as f:
       f.write(doc.data)

Progress Callback
-----------------

Monitor scan progress with a callback. Return ``False`` to abort:

.. code-block:: python

   def on_progress(percent: int) -> bool:
       print(f"Scanning... {percent}%")
       return True  # return False to abort

   with scanners[0] as scanner:
       doc = scanner.scan(progress=on_progress)

Thread Safety
-------------

All scanlib operations can be called from any thread. The library
internally dispatches operations to the correct thread for backends that
require it (macOS ImageCaptureCore, Windows TWAIN).

Note that ``progress`` and ``next_page`` callbacks may execute on an
internal thread. If your callbacks update a GUI, dispatch to your UI
thread accordingly.

API Reference
-------------

Scanner Discovery
~~~~~~~~~~~~~~~~~

.. autofunction:: scanlib.list_scanners

Scanner
~~~~~~~

.. autoclass:: scanlib.Scanner
   :members:
   :undoc-members:

Scanned Pages
~~~~~~~~~~~~~

.. autoclass:: scanlib.ScannedPage
   :members:

.. autofunction:: scanlib.build_pdf

Scan Result
~~~~~~~~~~~

.. autoclass:: scanlib.ScannedDocument
   :members:

Options & Configuration
~~~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: scanlib.ScanOptions
   :members:

.. autoclass:: scanlib.ColorMode
   :members:
   :undoc-members:

.. autoclass:: scanlib.ScanSource
   :members:
   :undoc-members:

.. autoclass:: scanlib.PageSize
   :members:

.. autoclass:: scanlib.ScannerDefaults
   :members:

Exceptions
~~~~~~~~~~

.. autoclass:: scanlib.ScanLibError

.. autoclass:: scanlib.ScanError

.. autoclass:: scanlib.ScanAborted

.. autoclass:: scanlib.FeederEmptyError

.. autoclass:: scanlib.ScannerNotOpenError

.. autoclass:: scanlib.NoScannerFoundError

.. autoclass:: scanlib.BackendNotAvailableError
