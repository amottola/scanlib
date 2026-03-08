scanlib
=======

A multiplatform document scanning library for Python.

scanlib provides a unified API for document scanning across Linux (SANE),
macOS (ImageCaptureCore), and Windows (TWAIN). It returns scanned documents
as PDF files and handles platform differences transparently. JPEG encoding
and pixel conversion are handled by a bundled C++ extension, with optional
`libjpeg-turbo <https://libjpeg-turbo.org/>`_ acceleration for high-resolution scans.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   quickstart
   api
