User Guide
==========

Basic Usage
-----------

List available scanners and scan a document:

.. code-block:: python

   import scanlib

   # Discover scanners
   scanners = scanlib.list_scanners()
   for s in scanners:
       print(s)  # e.g. "2nd Floor" or "HP Officejet Pro 8500"

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

   from scanlib import ColorMode, ScanArea, ScanSource

   with scanners[0] as scanner:
       doc = scanner.scan(
           dpi=600,
           color_mode=ColorMode.GRAY,
           scan_area=ScanArea(0, 0, 2100, 2970),  # full A4 in 1/10 mm
           source=ScanSource.FLATBED,
       )

Black & White Threshold
-----------------------

When scanning in BW mode, grayscale pixels are converted to 1-bit
black or white using a threshold.  Pixels with a value **≥ threshold**
become white; below become black.  The default is 128:

.. code-block:: python

   with scanners[0] as scanner:
       # Lower threshold = more white (lighter output)
       doc = scanner.scan(color_mode=ColorMode.BW, bw_threshold=100)

       # Higher threshold = more black (darker output)
       doc = scanner.scan(color_mode=ColorMode.BW, bw_threshold=180)

The threshold applies both to ``scan()``/``scan_pages()`` and to
``build_pdf()`` when converting grayscale pages to BW.

Opening a Scanner by ID
-----------------------

If you already know a scanner's ID from a previous discovery, you can
open it directly without running ``list_scanners()`` again:

.. code-block:: python

   import scanlib

   scanner = scanlib.open_scanner("escl:192.168.1.5:443")
   with scanner:
       doc = scanner.scan()

This skips the mDNS/platform discovery step and connects immediately.
On macOS with native ImageCaptureCore, a quick targeted discovery is
run behind the scenes to resolve the UUID to a device object.

Scanner Capabilities
--------------------

After opening a scanner, you can query its capabilities:

.. code-block:: python

   with scanners[0] as scanner:
       for si in scanner.sources:
           print(si.type)           # ScanSource.FLATBED
           print(si.resolutions)    # [150, 300, 600, 1200]
           print(si.color_modes)    # [ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW]
           print(si.max_scan_area)  # ScanArea(x=0, y=0, width=2159, height=2972)
       print(scanner.defaults)      # ScannerDefaults(dpi=300, ...)

The first entry in ``sources`` is the scanner's primary source (typically
flatbed).  When ``scan()`` is called without an explicit ``source``, the
first entry is used for parameter validation.

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

   # Rotate a page 90° clockwise
   pages[0] = pages[0].rotate(90)

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

Aborting a Scan
---------------

Call ``abort()`` from any thread to cancel an in-progress scan.  The
running ``scan()`` or ``scan_pages()`` call will raise
:class:`ScanAborted` shortly after:

.. code-block:: python

   import threading

   with scanners[0] as scanner:
       # Abort after 5 seconds from another thread
       threading.Timer(5, scanner.abort).start()
       try:
           doc = scanner.scan()
       except scanlib.ScanAborted:
           print("Scan was cancelled")

``abort()`` is safe to call even when no scan is running.

Cancelling Discovery
--------------------

Pass a :class:`threading.Event` to ``list_scanners()`` to cancel a
long-running discovery from another thread:

.. code-block:: python

   import threading
   import scanlib

   cancel = threading.Event()

   # Cancel after 5 seconds from another thread
   threading.Timer(5, cancel.set).start()

   scanners = scanlib.list_scanners(timeout=120, cancel=cancel)

When the event is set, ``list_scanners()`` returns immediately with
whatever scanners have been found (or an empty list).

eSCL / AirScan Network Scanners
-------------------------------

On Linux and Windows, scanlib automatically discovers network scanners
that advertise via mDNS (``_uscan._tcp`` / ``_uscans._tcp``) and
communicates with them directly over HTTP using the eSCL protocol.  No
OS-level scanner drivers are required for network scanners — only USB
scanners still need SANE or WIA.

On macOS, ImageCaptureCore already handles eSCL natively, so the
built-in eSCL backend is disabled by default.  To enable it (e.g. if
ICC doesn't discover a scanner), set the environment variable:

.. code-block:: bash

   export SCANLIB_ESCL=1

When enabled, eSCL discovery runs in parallel with the native backend
and results are deduplicated by IP address.  Each scanner's ``backend``
property indicates which backend discovered it (``"sane"``,
``"imagecapture"``, ``"wia"``, or ``"escl"``).

Thread Safety
-------------

All scanlib operations can be called from any thread. The library
internally dispatches operations to the correct thread for backends that
require it (macOS ImageCaptureCore, Windows WIA).

Note that ``progress`` callbacks may execute on an internal thread.
If your callback updates a GUI, dispatch to your UI thread accordingly.
The ``next_page`` callback always runs on the caller's thread.
