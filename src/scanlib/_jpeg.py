"""JPEG encoding with platform-optimized acceleration.

On macOS, encoding uses the built-in ImageIO framework (always available).
On other platforms, libjpeg-turbo is used when installed, otherwise falls
back to the bundled toojpeg encoder via _scanlib_accel.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

from _scanlib_accel import encode_jpeg as _toojpeg_encode

# ------------------------------------------------------------------ #
# macOS ImageIO encoder (ctypes)                                      #
# ------------------------------------------------------------------ #

_has_imageio = False

if sys.platform == "darwin":
    try:
        _cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        _cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        _io = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ImageIO.framework/ImageIO"
        )

        _CFIndex = ctypes.c_long

        _cf.CFDataCreateMutable.restype = ctypes.c_void_p
        _cf.CFDataCreateMutable.argtypes = [ctypes.c_void_p, _CFIndex]
        _cf.CFDataGetBytePtr.restype = ctypes.c_void_p
        _cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        _cf.CFDataGetLength.restype = _CFIndex
        _cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        _cf.CFRelease.argtypes = [ctypes.c_void_p]
        _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        _cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
        ]
        _cf.CFDictionaryCreate.restype = ctypes.c_void_p
        _cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            _CFIndex,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        _cf.CFNumberCreate.restype = ctypes.c_void_p
        _cf.CFNumberCreate.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ]

        _cg.CGColorSpaceCreateWithName.restype = ctypes.c_void_p
        _cg.CGColorSpaceCreateWithName.argtypes = [ctypes.c_void_p]
        _cg.CGDataProviderCreateWithData.restype = ctypes.c_void_p
        _cg.CGDataProviderCreateWithData.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
        ]
        _cg.CGImageCreate.restype = ctypes.c_void_p
        _cg.CGImageCreate.argtypes = [
            ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
            ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_bool, ctypes.c_int,
        ]
        _cg.CGImageRelease.argtypes = [ctypes.c_void_p]
        _cg.CGColorSpaceRelease.argtypes = [ctypes.c_void_p]
        _cg.CGDataProviderRelease.argtypes = [ctypes.c_void_p]

        _io.CGImageDestinationCreateWithData.restype = ctypes.c_void_p
        _io.CGImageDestinationCreateWithData.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
        ]
        _io.CGImageDestinationAddImage.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        _io.CGImageDestinationFinalize.restype = ctypes.c_bool
        _io.CGImageDestinationFinalize.argtypes = [ctypes.c_void_p]

        _kCGColorSpaceGenericGray = ctypes.c_void_p.in_dll(
            _cg, "kCGColorSpaceGenericGray"
        )
        _kCGColorSpaceSRGB = ctypes.c_void_p.in_dll(_cg, "kCGColorSpaceSRGB")

        _kCFStringEncodingUTF8 = 0x08000100
        _kCFNumberDoubleType = 13
        _kCGImageAlphaNone = 0

        _jpeg_type_str = _cf.CFStringCreateWithCString(
            None, b"public.jpeg", _kCFStringEncodingUTF8,
        )
        _quality_key_str = _cf.CFStringCreateWithCString(
            None, b"kCGImageDestinationLossyCompressionQuality",
            _kCFStringEncodingUTF8,
        )

        _has_imageio = True
    except (OSError, AttributeError, ValueError):
        _has_imageio = False


def _imageio_encode(
    pixels: bytes, width: int, height: int, color_type: int, quality: int,
) -> bytes:
    """Encode using macOS ImageIO framework via ctypes."""
    if color_type == 0:
        components = 1
        cs_name = _kCGColorSpaceGenericGray
    elif color_type == 2:
        components = 3
        cs_name = _kCGColorSpaceSRGB
    else:
        raise ValueError(f"color_type must be 0 or 2, got {color_type}")

    bytes_per_row = width * components
    bits_per_pixel = 8 * components

    color_space = _cg.CGColorSpaceCreateWithName(cs_name)
    provider = _cg.CGDataProviderCreateWithData(
        None, pixels, len(pixels), None,
    )
    image = _cg.CGImageCreate(
        width, height, 8, bits_per_pixel, bytes_per_row,
        color_space, _kCGImageAlphaNone, provider, None, False, 0,
    )

    data = _cf.CFDataCreateMutable(None, 0)
    dest = _io.CGImageDestinationCreateWithData(
        data, _jpeg_type_str, 1, None,
    )

    # Build options dict with compression quality.
    q = ctypes.c_double(quality / 100.0)
    q_num = _cf.CFNumberCreate(None, _kCFNumberDoubleType, ctypes.byref(q))
    keys = (ctypes.c_void_p * 1)(_quality_key_str)
    values = (ctypes.c_void_p * 1)(q_num)
    options = _cf.CFDictionaryCreate(None, keys, values, 1, None, None)

    _io.CGImageDestinationAddImage(dest, image, options)
    ok = _io.CGImageDestinationFinalize(dest)

    if ok:
        length = _cf.CFDataGetLength(data)
        ptr = _cf.CFDataGetBytePtr(data)
        result = ctypes.string_at(ptr, length)
    else:
        result = None

    # Cleanup CF/CG objects.
    _cf.CFRelease(options)
    _cf.CFRelease(q_num)
    _cf.CFRelease(dest)
    _cf.CFRelease(data)
    _cg.CGImageRelease(image)
    _cg.CGDataProviderRelease(provider)
    _cg.CGColorSpaceRelease(color_space)

    if result is None:
        raise RuntimeError("ImageIO JPEG encoding failed")
    return result


# ------------------------------------------------------------------ #
# libjpeg-turbo encoder (Linux / Windows)                            #
# ------------------------------------------------------------------ #

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

    if sys.platform == "win32":
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
    """Initialize TurboJPEG at module load time (non-macOS only)."""
    global _tj_lib, _tj_handle
    if _has_imageio:
        return
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


# ------------------------------------------------------------------ #
# Public API                                                          #
# ------------------------------------------------------------------ #

def encode_jpeg(
    pixels: bytes, width: int, height: int, color_type: int, quality: int,
) -> bytes:
    """Encode raw pixels as baseline JPEG.

    Uses ImageIO on macOS, libjpeg-turbo on other platforms if available,
    otherwise falls back to the bundled toojpeg encoder.
    """
    if _has_imageio:
        return _imageio_encode(pixels, width, height, color_type, quality)
    if _tj_handle is not None:
        return _turbo_encode(pixels, width, height, color_type, quality)
    return _toojpeg_encode(pixels, width, height, color_type, quality)


#: Whether ImageIO (macOS) is used for JPEG encoding.
has_imageio: bool = _has_imageio

#: Whether libjpeg-turbo is used for JPEG encoding (non-macOS).
has_turbo: bool = _tj_handle is not None
