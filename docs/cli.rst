Command-Line Interface
======================

scanlib installs a ``scanlib`` command for scanning from the shell.
It can also be invoked as ``python3 -m scanlib``.

Listing Scanners
----------------

.. code-block:: bash

   scanlib list

Prints a table of all available scanners with index, name, ID,
location, and backend.  The index can be used with ``-s`` in other
commands.

Viewing Capabilities
--------------------

.. code-block:: bash

   scanlib info -s 0

Opens the scanner and displays its capabilities: supported sources,
resolutions, color modes, maximum scan area, and defaults.  The
``-s`` flag accepts a numeric index, a scanner ID, or a substring
of the scanner name.

Scanning
--------

.. code-block:: bash

   scanlib scan -o document.pdf

Scans a document and writes a PDF.  All scan options can be
configured via flags:

.. code-block:: bash

   scanlib scan -o scan.pdf \
       --dpi 600 \
       --color-mode gray \
       --source flatbed \
       --format jpeg \
       --jpeg-quality 90

Options
^^^^^^^

``-s``, ``--scanner``
   Scanner index, ID, or name substring.  Default: ``0`` (first scanner).

``-o``, ``--output``
   Output PDF file path.  Default: ``scan.pdf``.

``--dpi``
   Scan resolution in DPI.  Default: scanner default.

``--color-mode``
   Color mode: ``color``, ``gray``, or ``bw``.  Default: scanner default.

``--source``
   Scan source: ``flatbed`` or ``feeder``.  Default: scanner default.

``--scan-area``
   Scan region as ``x,y,width,height`` in 1/10 millimeters.
   Example: ``--scan-area 0,0,2100,2970`` for full A4.

``--format``
   Image format inside the PDF: ``jpeg`` or ``png``.
   Default: auto (PNG for BW, JPEG otherwise).

``--jpeg-quality``
   JPEG quality from 1 to 100.  Default: 85.

``--pages``
   Number of flatbed pages to scan, or ``ask`` for interactive
   prompting between pages.  Ignored when source is feeder.

Multi-Page Scanning
^^^^^^^^^^^^^^^^^^^

Scan a fixed number of flatbed pages:

.. code-block:: bash

   scanlib scan -o multipage.pdf --pages 3

Prompt between pages interactively:

.. code-block:: bash

   scanlib scan -o multipage.pdf --pages ask

When using a document feeder, all pages are scanned automatically:

.. code-block:: bash

   scanlib scan -o feeder.pdf --source feeder

Progress and Output
^^^^^^^^^^^^^^^^^^^

Progress is reported on stderr so it does not interfere with stdout.
A summary is printed when the scan completes:

.. code-block:: text

   Scanning with HP Officejet @ 300 DPI, color...
   Scanning... 100%
   Saved 1 page(s) to scan.pdf (54321 bytes, 2480x3508 px)
