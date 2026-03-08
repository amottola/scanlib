"""JPEG encoding with optional libjpeg-turbo acceleration.

When libturbojpeg is available on the system, JPEG encoding uses its
SIMD-optimized path (~16x faster than the bundled toojpeg encoder).
Otherwise, falls back to the vendored toojpeg via _scanlib_accel.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

from _scanlib_accel import encode_jpeg as _toojpeg_encode

# TurboJPEG constants
_TJPF_RGB = 0
_TJPF_GRAY = 6
_TJSAMP_420 = 2
_TJSAMP_GRAY = 3

# Module-level state
_tj_lib = None  # ctypes.CDLL or None
_tj_handle = None  # tjhandle (c_void_p) or None


def _find_turbojpeg() -> ctypes.CDLL | None:
    """Try to load the TurboJPEG shared library."""
    candidates: list[str] = []

    # Standard system search
    path = ctypes.util.find_library("turbojpeg")
    if path:
        candidates.append(path)

    if sys.platform == "darwin":
        # Homebrew (Apple Silicon and Intel)
        for prefix in ("/opt/homebrew/lib", "/usr/local/lib"):
            p = os.path.join(prefix, "libturbojpeg.dylib")
            if os.path.exists(p):
                candidates.append(p)
    elif sys.platform == "win32":
        # Common Windows install locations
        for prog in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            if prog:
                p = os.path.join(prog, "libjpeg-turbo64", "bin", "turbojpeg.dll")
                if os.path.exists(p):
                    candidates.append(p)

    for candidate in candidates:
        try:
            lib = ctypes.CDLL(candidate)
            # Verify it has the TurboJPEG API
            lib.tjInitCompress.restype = ctypes.c_void_p
            lib.tjCompress2.restype = ctypes.c_int
            lib.tjCompress2.argtypes = [
                ctypes.c_void_p,                        # handle
                ctypes.POINTER(ctypes.c_ubyte),         # srcBuf
                ctypes.c_int,                           # width
                ctypes.c_int,                           # pitch
                ctypes.c_int,                           # height
                ctypes.c_int,                           # pixelFormat
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),  # jpegBuf
                ctypes.POINTER(ctypes.c_ulong),         # jpegSize
                ctypes.c_int,                           # jpegSubsamp
                ctypes.c_int,                           # jpegQual
                ctypes.c_int,                           # flags
            ]
            lib.tjFree.argtypes = [ctypes.c_void_p]
            lib.tjDestroy.argtypes = [ctypes.c_void_p]
            lib.tjDestroy.restype = ctypes.c_int
            return lib
        except (OSError, AttributeError):
            continue

    return None


def _init_turbo() -> None:
    """Initialize TurboJPEG at module load time."""
    global _tj_lib, _tj_handle
    _tj_lib = _find_turbojpeg()
    if _tj_lib is not None:
        _tj_handle = _tj_lib.tjInitCompress()
        if not _tj_handle:
            _tj_lib = None
            _tj_handle = None


_init_turbo()


def _turbo_encode(
    pixels: bytes, width: int, height: int, color_type: int, quality: int,
) -> bytes:
    """Encode using TurboJPEG."""
    if color_type == 0:
        pixel_format = _TJPF_GRAY
        subsamp = _TJSAMP_GRAY
        pitch = width
    elif color_type == 2:
        pixel_format = _TJPF_RGB
        subsamp = _TJSAMP_420
        pitch = width * 3
    else:
        raise ValueError(f"color_type must be 0 or 2, got {color_type}")

    src = (ctypes.c_ubyte * len(pixels)).from_buffer_copy(pixels)
    jpeg_buf = ctypes.POINTER(ctypes.c_ubyte)()
    jpeg_size = ctypes.c_ulong(0)

    ret = _tj_lib.tjCompress2(
        _tj_handle, src, width, pitch, height, pixel_format,
        ctypes.byref(jpeg_buf), ctypes.byref(jpeg_size),
        subsamp, quality, 0,
    )
    if ret != 0:
        raise RuntimeError("TurboJPEG compression failed")

    size = jpeg_size.value
    result = ctypes.string_at(jpeg_buf, size)
    _tj_lib.tjFree(jpeg_buf)
    return result


def encode_jpeg(
    pixels: bytes, width: int, height: int, color_type: int, quality: int,
) -> bytes:
    """Encode raw pixels as baseline JPEG.

    Uses libjpeg-turbo if available, otherwise falls back to toojpeg.
    """
    if _tj_handle is not None:
        return _turbo_encode(pixels, width, height, color_type, quality)
    return _toojpeg_encode(pixels, width, height, color_type, quality)


#: Whether the fast TurboJPEG backend is active.
has_turbo: bool = _tj_handle is not None
