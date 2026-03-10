"""JPEG encoding with platform-native acceleration.

Each platform uses a mandatory native encoder — no fallback chain:
- macOS: ImageIO framework (always available)
- Windows: WIC (Windows Imaging Component, always available)
- Linux: libjpeg-turbo (required at runtime)
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

from ._types import ColorMode

if sys.platform == "darwin":
    # ------------------------------------------------------------------ #
    # macOS ImageIO encoder (ctypes)                                      #
    # ------------------------------------------------------------------ #

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

    def encode_jpeg(
        pixels: bytes, width: int, height: int, color_mode: ColorMode, quality: int,
    ) -> bytes:
        """Encode raw pixels as baseline JPEG using macOS ImageIO."""
        if color_mode == ColorMode.COLOR:
            components = 3
            cs_name = _kCGColorSpaceSRGB
        else:
            components = 1
            cs_name = _kCGColorSpaceGenericGray

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

elif sys.platform == "win32":
    # ------------------------------------------------------------------ #
    # Windows WIC encoder (raw ctypes COM vtable calls)                   #
    # ------------------------------------------------------------------ #

    from ctypes import HRESULT, byref, c_float, c_void_p, c_wchar_p, windll
    from ctypes.wintypes import BOOL, DWORD, ULONG

    LPCOLESTR = c_wchar_p

    _ole32 = windll.ole32
    _kernel32 = windll.kernel32

    _CLSCTX_INPROC_SERVER = 0x1

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def _make_guid(s: str) -> _GUID:
        s = s.strip("{}")
        parts = s.split("-")
        d1 = int(parts[0], 16)
        d2 = int(parts[1], 16)
        d3 = int(parts[2], 16)
        d4_hex = parts[3] + parts[4]
        d4 = (ctypes.c_ubyte * 8)(*[int(d4_hex[i:i+2], 16) for i in range(0, 16, 2)])
        return _GUID(d1, d2, d3, d4)

    _CLSID_WICImagingFactory = _make_guid("{cacaf262-9370-4615-a13b-9f5539da4c0a}")
    _IID_IWICImagingFactory = _make_guid("{ec5ec8a9-c395-4314-9c77-54d7a935ff70}")
    _GUID_WICPixelFormat8bppGray = _make_guid("{6fddc324-4e03-4bfe-b185-3d77768dc908}")
    _GUID_WICPixelFormat24bppBGR = _make_guid("{6fddc324-4e03-4bfe-b185-3d77768dc90c}")
    _GUID_ContainerFormatJpeg = _make_guid("{19e4a5aa-5662-4fc5-a0c0-1758028e1057}")

    class _PROPBAG2(ctypes.Structure):
        _fields_ = [
            ("dwType", DWORD),
            ("vt", ctypes.c_ushort),
            ("cfType", ctypes.c_ushort),
            ("dwHint", DWORD),
            ("pstrName", LPCOLESTR),
            ("clsid", _GUID),
        ]

    _VT_R4 = 4

    class _VARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("wReserved1", ctypes.c_ushort),
            ("wReserved2", ctypes.c_ushort),
            ("wReserved3", ctypes.c_ushort),
            ("fltVal", c_float),
            ("padding", ctypes.c_ubyte * 8),
        ]

    def _vtbl_call(ptr, slot, proto, *args):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
        func_ptr = ctypes.cast(
            vtbl + slot * ctypes.sizeof(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ).contents.value
        return proto(func_ptr)(ptr, *args)

    _Release_proto = ctypes.WINFUNCTYPE(ULONG, c_void_p)

    def _release(ptr):
        if ptr:
            _vtbl_call(ptr, 2, _Release_proto)

    _ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), c_void_p, DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(c_void_p),
    ]
    _ole32.CoCreateInstance.restype = HRESULT
    _ole32.CreateStreamOnHGlobal.argtypes = [c_void_p, BOOL, ctypes.POINTER(c_void_p)]
    _ole32.CreateStreamOnHGlobal.restype = HRESULT
    _ole32.GetHGlobalFromStream.argtypes = [c_void_p, ctypes.POINTER(c_void_p)]
    _ole32.GetHGlobalFromStream.restype = HRESULT
    _kernel32.GlobalSize.argtypes = [c_void_p]
    _kernel32.GlobalSize.restype = ctypes.c_size_t
    _kernel32.GlobalLock.argtypes = [c_void_p]
    _kernel32.GlobalLock.restype = c_void_p
    _kernel32.GlobalUnlock.argtypes = [c_void_p]
    _kernel32.GlobalUnlock.restype = BOOL

    _wic_factory = None

    def _get_wic_factory():
        """Lazily create the WIC factory on first use.

        COM is already initialized by the WIA backend (via comtypes) before
        any encoding happens, so we just create the factory here.
        """
        global _wic_factory
        if _wic_factory is not None:
            return _wic_factory

        _wic_factory = c_void_p()
        hr = _ole32.CoCreateInstance(
            byref(_CLSID_WICImagingFactory),
            None,
            _CLSCTX_INPROC_SERVER,
            byref(_IID_IWICImagingFactory),
            byref(_wic_factory),
        )
        if hr < 0:
            _wic_factory = None
            raise RuntimeError(
                f"CoCreateInstance WICImagingFactory failed: 0x{hr & 0xFFFFFFFF:08x}"
            )
        return _wic_factory

    def encode_jpeg(
        pixels: bytes, width: int, height: int, color_mode: ColorMode, quality: int,
    ) -> bytes:
        """Encode raw pixels as baseline JPEG using Windows WIC."""
        from _scanlib_accel import rgb_to_bgr

        if color_mode == ColorMode.COLOR:
            pixel_fmt = _GUID_WICPixelFormat24bppBGR
            stride = width * 3
            pixels = rgb_to_bgr(pixels, width, height)
        else:
            pixel_fmt = _GUID_WICPixelFormat8bppGray
            stride = width

        factory = _get_wic_factory()
        stream = c_void_p()
        encoder = c_void_p()
        frame = c_void_p()
        prop_bag = c_void_p()

        try:
            # Create memory-backed IStream
            _ole32.CreateStreamOnHGlobal(None, True, byref(stream))

            # IWICImagingFactory::CreateEncoder (slot 8)
            _CreateEncoder = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p,
                ctypes.POINTER(_GUID), ctypes.POINTER(_GUID),
                ctypes.POINTER(c_void_p),
            )
            guid_jpeg = _GUID_ContainerFormatJpeg
            hr = _vtbl_call(factory, 8, _CreateEncoder,
                             byref(guid_jpeg), None, byref(encoder))
            if hr < 0:
                raise RuntimeError(f"CreateEncoder failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapEncoder::Initialize (slot 3)
            _Initialize_Enc = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_void_p, DWORD)
            hr = _vtbl_call(encoder, 3, _Initialize_Enc, stream, 0x2)
            if hr < 0:
                raise RuntimeError(f"Encoder.Initialize failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapEncoder::CreateNewFrame (slot 10)
            _CreateNewFrame = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p,
                ctypes.POINTER(c_void_p), ctypes.POINTER(c_void_p),
            )
            hr = _vtbl_call(encoder, 10, _CreateNewFrame,
                             byref(frame), byref(prop_bag))
            if hr < 0:
                raise RuntimeError(f"CreateNewFrame failed: 0x{hr & 0xFFFFFFFF:08x}")

            # Set JPEG quality via IPropertyBag2::Write (slot 4)
            pb = _PROPBAG2()
            pb.dwType = 0
            pb.vt = _VT_R4
            pb.pstrName = "ImageQuality"
            var = _VARIANT()
            var.vt = _VT_R4
            var.fltVal = quality / 100.0
            _PB2_Write = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, ULONG,
                ctypes.POINTER(_PROPBAG2), ctypes.POINTER(_VARIANT),
            )
            _vtbl_call(prop_bag, 4, _PB2_Write, 1, byref(pb), byref(var))

            # IWICBitmapFrameEncode::Initialize (slot 3)
            _Initialize_Frame = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_void_p)
            hr = _vtbl_call(frame, 3, _Initialize_Frame, prop_bag)
            if hr < 0:
                raise RuntimeError(f"Frame.Initialize failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapFrameEncode::SetSize (slot 4)
            _SetSize = ctypes.WINFUNCTYPE(HRESULT, c_void_p, DWORD, DWORD)
            hr = _vtbl_call(frame, 4, _SetSize, width, height)
            if hr < 0:
                raise RuntimeError(f"SetSize failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapFrameEncode::SetResolution (slot 5)
            _SetResolution = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, ctypes.c_double, ctypes.c_double)
            _vtbl_call(frame, 5, _SetResolution, 96.0, 96.0)

            # IWICBitmapFrameEncode::SetPixelFormat (slot 6)
            _SetPixelFormat = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, ctypes.POINTER(_GUID))
            fmt = _GUID()
            ctypes.memmove(byref(fmt), byref(pixel_fmt), ctypes.sizeof(_GUID))
            hr = _vtbl_call(frame, 6, _SetPixelFormat, byref(fmt))
            if hr < 0:
                raise RuntimeError(f"SetPixelFormat failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapFrameEncode::WritePixels (slot 10)
            _WritePixels = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, DWORD, DWORD, DWORD, ctypes.c_char_p)
            hr = _vtbl_call(frame, 10, _WritePixels,
                             height, stride, len(pixels), pixels)
            if hr < 0:
                raise RuntimeError(f"WritePixels failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapFrameEncode::Commit (slot 12)
            _Commit = ctypes.WINFUNCTYPE(HRESULT, c_void_p)
            hr = _vtbl_call(frame, 12, _Commit)
            if hr < 0:
                raise RuntimeError(f"Frame.Commit failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapEncoder::Commit (slot 11)
            hr = _vtbl_call(encoder, 11, _Commit)
            if hr < 0:
                raise RuntimeError(f"Encoder.Commit failed: 0x{hr & 0xFFFFFFFF:08x}")

            # Read JPEG data from IStream
            hglobal = c_void_p()
            _ole32.GetHGlobalFromStream(stream, byref(hglobal))
            size = _kernel32.GlobalSize(hglobal)
            ptr = _kernel32.GlobalLock(hglobal)
            result = ctypes.string_at(ptr, size)
            _kernel32.GlobalUnlock(hglobal)
            return result

        finally:
            _release(prop_bag)
            _release(frame)
            _release(encoder)
            _release(stream)

else:
    # ------------------------------------------------------------------ #
    # Linux libjpeg-turbo encoder (required)                              #
    # ------------------------------------------------------------------ #

    _TJPF_RGB = 0
    _TJPF_GRAY = 6
    _TJSAMP_420 = 2
    _TJSAMP_GRAY = 3

    def _find_turbojpeg() -> ctypes.CDLL | None:
        path = ctypes.util.find_library("turbojpeg")
        if not path:
            return None
        try:
            lib = ctypes.CDLL(path)
            lib.tjInitCompress.restype = ctypes.c_void_p
            lib.tjCompress2.restype = ctypes.c_int
            lib.tjCompress2.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ]
            lib.tjFree.argtypes = [ctypes.c_void_p]
            lib.tjDestroy.argtypes = [ctypes.c_void_p]
            lib.tjDestroy.restype = ctypes.c_int
            return lib
        except (OSError, AttributeError):
            return None

    _tj_lib = _find_turbojpeg()
    if _tj_lib is not None:
        _tj_handle = _tj_lib.tjInitCompress()
        if not _tj_handle:
            _tj_lib = None
            _tj_handle = None
    else:
        _tj_handle = None

    if _tj_handle is None:
        raise RuntimeError(
            "libjpeg-turbo is required on Linux but was not found. "
            "Install it with: apt install libturbojpeg0-dev"
        )

    def encode_jpeg(
        pixels: bytes, width: int, height: int, color_mode: ColorMode, quality: int,
    ) -> bytes:
        """Encode raw pixels as baseline JPEG using libjpeg-turbo."""
        if color_mode == ColorMode.COLOR:
            pixel_format = _TJPF_RGB
            subsamp = _TJSAMP_420
            pitch = width * 3
        else:
            pixel_format = _TJPF_GRAY
            subsamp = _TJSAMP_GRAY
            pitch = width

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
