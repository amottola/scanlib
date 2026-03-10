We want to create a multiplatform document scanning library in Python, named "scanlib".
Here are the requirements:

- The library should be backend-based, with each backend based on an existing document scanning library as found on PyPI:
    - on Windows, use WIA via the "comtypes" library
    - on macOS, use the "pyobjc-framework-ImageCaptureCore" library
    - on Linux, use the "python-sane" library

- Each backend should be implemented as a separate module.

- scanlib should provide a unified API across all backends.

- There should be a single public function for scanning documents that works across all backends and returns a platform-agnostic document object.

- The library should be easy to use and have a simple API.

- The library should be well-documented.

- The library should have tests.

- The license must be MIT.
