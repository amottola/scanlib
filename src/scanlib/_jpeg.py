"""JPEG encoding with platform-native acceleration.

Each platform uses a mandatory native encoder — no fallback chain:
- macOS: ImageIO framework (always available)
- Windows: WIC (Windows Imaging Component, always available)
- Linux: libjpeg (standard IJG API, universally available)
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
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint32,
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
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
    ]

    _cg.CGColorSpaceCreateWithName.restype = ctypes.c_void_p
    _cg.CGColorSpaceCreateWithName.argtypes = [ctypes.c_void_p]
    _cg.CGDataProviderCreateWithData.restype = ctypes.c_void_p
    _cg.CGDataProviderCreateWithData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    _cg.CGImageCreate.restype = ctypes.c_void_p
    _cg.CGImageCreate.argtypes = [
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_int,
    ]
    _cg.CGImageRelease.argtypes = [ctypes.c_void_p]
    _cg.CGColorSpaceRelease.argtypes = [ctypes.c_void_p]
    _cg.CGDataProviderRelease.argtypes = [ctypes.c_void_p]

    _io.CGImageDestinationCreateWithData.restype = ctypes.c_void_p
    _io.CGImageDestinationCreateWithData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    _io.CGImageDestinationAddImage.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _io.CGImageDestinationFinalize.restype = ctypes.c_bool
    _io.CGImageDestinationFinalize.argtypes = [ctypes.c_void_p]

    _kCGColorSpaceGenericGray = ctypes.c_void_p.in_dll(_cg, "kCGColorSpaceGenericGray")
    _kCGColorSpaceSRGB = ctypes.c_void_p.in_dll(_cg, "kCGColorSpaceSRGB")

    _kCFStringEncodingUTF8 = 0x08000100
    _kCFNumberDoubleType = 13
    _kCGImageAlphaNone = 0

    _jpeg_type_str = _cf.CFStringCreateWithCString(
        None,
        b"public.jpeg",
        _kCFStringEncodingUTF8,
    )
    _quality_key_str = _cf.CFStringCreateWithCString(
        None,
        b"kCGImageDestinationLossyCompressionQuality",
        _kCFStringEncodingUTF8,
    )

    def encode_jpeg(
        pixels: bytes,
        width: int,
        height: int,
        color_mode: ColorMode,
        quality: int,
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
            None,
            pixels,
            len(pixels),
            None,
        )
        image = _cg.CGImageCreate(
            width,
            height,
            8,
            bits_per_pixel,
            bytes_per_row,
            color_space,
            _kCGImageAlphaNone,
            provider,
            None,
            False,
            0,
        )

        data = _cf.CFDataCreateMutable(None, 0)
        dest = _io.CGImageDestinationCreateWithData(
            data,
            _jpeg_type_str,
            1,
            None,
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
        d4 = (ctypes.c_ubyte * 8)(
            *[int(d4_hex[i : i + 2], 16) for i in range(0, 16, 2)]
        )
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
        ctypes.POINTER(_GUID),
        c_void_p,
        DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(c_void_p),
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
        pixels: bytes,
        width: int,
        height: int,
        color_mode: ColorMode,
        quality: int,
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
                HRESULT,
                c_void_p,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(_GUID),
                ctypes.POINTER(c_void_p),
            )
            guid_jpeg = _GUID_ContainerFormatJpeg
            hr = _vtbl_call(
                factory, 8, _CreateEncoder, byref(guid_jpeg), None, byref(encoder)
            )
            if hr < 0:
                raise RuntimeError(f"CreateEncoder failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapEncoder::Initialize (slot 3)
            _Initialize_Enc = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, DWORD)
            hr = _vtbl_call(encoder, 3, _Initialize_Enc, stream, 0x2)
            if hr < 0:
                raise RuntimeError(
                    f"Encoder.Initialize failed: 0x{hr & 0xFFFFFFFF:08x}"
                )

            # IWICBitmapEncoder::CreateNewFrame (slot 10)
            _CreateNewFrame = ctypes.WINFUNCTYPE(
                HRESULT,
                c_void_p,
                ctypes.POINTER(c_void_p),
                ctypes.POINTER(c_void_p),
            )
            hr = _vtbl_call(encoder, 10, _CreateNewFrame, byref(frame), byref(prop_bag))
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
                HRESULT,
                c_void_p,
                ULONG,
                ctypes.POINTER(_PROPBAG2),
                ctypes.POINTER(_VARIANT),
            )
            _vtbl_call(prop_bag, 4, _PB2_Write, 1, byref(pb), byref(var))

            # IWICBitmapFrameEncode::Initialize (slot 3)
            _Initialize_Frame = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p)
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
                HRESULT, c_void_p, ctypes.c_double, ctypes.c_double
            )
            _vtbl_call(frame, 5, _SetResolution, 96.0, 96.0)

            # IWICBitmapFrameEncode::SetPixelFormat (slot 6)
            _SetPixelFormat = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, ctypes.POINTER(_GUID)
            )
            fmt = _GUID()
            ctypes.memmove(byref(fmt), byref(pixel_fmt), ctypes.sizeof(_GUID))
            hr = _vtbl_call(frame, 6, _SetPixelFormat, byref(fmt))
            if hr < 0:
                raise RuntimeError(f"SetPixelFormat failed: 0x{hr & 0xFFFFFFFF:08x}")

            # IWICBitmapFrameEncode::WritePixels (slot 10)
            _WritePixels = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, DWORD, DWORD, DWORD, ctypes.c_char_p
            )
            hr = _vtbl_call(
                frame, 10, _WritePixels, height, stride, len(pixels), pixels
            )
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
    # Linux libjpeg encoder (standard IJG API via ctypes)                 #
    # ------------------------------------------------------------------ #

    import struct as _struct

    _JCS_GRAYSCALE = 1
    _JCS_RGB = 2
    _JERR_SIZE = 1024
    _PTR = ctypes.sizeof(ctypes.c_void_p)

    # Offset of image_width in jpeg_compress_struct (stable since 6b):
    # 4 pointers (err, mem, progress, client_data) + 2 ints
    # (is_decompressor, global_state) + 1 pointer (dest)
    _IMG_W_OFF = 5 * _PTR + 8

    def _load_libjpeg() -> tuple[ctypes.CDLL, int, int]:
        """Load libjpeg and return (lib, version, struct_size)."""
        import subprocess
        import textwrap

        path = ctypes.util.find_library("jpeg")
        if not path:
            raise RuntimeError(
                "libjpeg is required on Linux but was not found. "
                "Install it with: apt install libjpeg-dev"
            )
        lib = ctypes.CDLL(path)

        lib.jpeg_std_error.restype = ctypes.c_void_p
        lib.jpeg_std_error.argtypes = [ctypes.c_void_p]
        lib.jpeg_CreateCompress.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_size_t,
        ]
        lib.jpeg_set_defaults.argtypes = [ctypes.c_void_p]
        lib.jpeg_set_quality.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.jpeg_start_compress.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.jpeg_write_scanlines.restype = ctypes.c_uint
        lib.jpeg_write_scanlines.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.c_uint,
        ]
        lib.jpeg_finish_compress.argtypes = [ctypes.c_void_p]
        lib.jpeg_destroy_compress.argtypes = [ctypes.c_void_p]
        lib.jpeg_mem_dest.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.POINTER(ctypes.c_ulong),
        ]

        # Probe the library version and struct size in a subprocess.
        # libjpeg's default error handler calls exit() on version/size
        # mismatch, which kills the process.  Each (version, size) pair
        # is tested in its own subprocess to isolate from that.
        _PROBE = (
            "import ctypes,sys;"
            "l=ctypes.CDLL({path!r});"
            "l.jpeg_std_error.restype=ctypes.c_void_p;"
            "l.jpeg_std_error.argtypes=[ctypes.c_void_p];"
            "l.jpeg_CreateCompress.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_size_t];"
            "l.jpeg_destroy_compress.argtypes=[ctypes.c_void_p];"
            "P=ctypes.sizeof(ctypes.c_void_p);"
            "e=(ctypes.c_ubyte*1024)();"
            "l.jpeg_std_error(ctypes.cast(e,ctypes.c_void_p));"
            "c=(ctypes.c_ubyte*{{sz}})();"
            "ctypes.memmove(c,ctypes.byref(ctypes.c_void_p(ctypes.addressof(e))),P);"
            "l.jpeg_CreateCompress(ctypes.cast(c,ctypes.c_void_p),{{ver}},{{sz}});"
            "l.jpeg_destroy_compress(ctypes.cast(c,ctypes.c_void_p));"
            "print('OK')"
        ).format(path=path)

        # Most-likely combos first: libjpeg-turbo 64-bit, IJG 6b 64-bit,
        # then 32-bit variants, then less common versions.
        _COMBOS = [
            (80, 584),
            (62, 520),
            (80, 560),
            (62, 464),
            (90, 600),
            (90, 592),
            (70, 568),
            (70, 560),
        ]
        for ver, sz in _COMBOS:
            script = _PROBE.format(ver=ver, sz=sz)
            try:
                r = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode == 0 and "OK" in r.stdout:
                    return lib, ver, sz
            except Exception:
                continue

        raise RuntimeError("Could not determine libjpeg struct layout")

    _jpeg, _JPEG_LIB_VERSION, _CINFO_SIZE = _load_libjpeg()

    def encode_jpeg(
        pixels: bytes,
        width: int,
        height: int,
        color_mode: ColorMode,
        quality: int,
    ) -> bytes:
        """Encode raw pixels as baseline JPEG using libjpeg."""
        if color_mode == ColorMode.COLOR:
            components, color_space = 3, _JCS_RGB
        else:
            components, color_space = 1, _JCS_GRAYSCALE

        row_stride = width * components
        cinfo = (ctypes.c_ubyte * _CINFO_SIZE)()
        jerr = (ctypes.c_ubyte * _JERR_SIZE)()

        _jpeg.jpeg_std_error(ctypes.cast(jerr, ctypes.c_void_p))
        ctypes.memmove(
            cinfo,
            ctypes.byref(ctypes.c_void_p(ctypes.addressof(jerr))),
            _PTR,
        )

        cinfo_ptr = ctypes.cast(cinfo, ctypes.c_void_p)
        _jpeg.jpeg_CreateCompress(cinfo_ptr, _JPEG_LIB_VERSION, _CINFO_SIZE)

        try:
            out_buf = ctypes.POINTER(ctypes.c_ubyte)()
            out_size = ctypes.c_ulong(0)
            _jpeg.jpeg_mem_dest(
                cinfo_ptr, ctypes.byref(out_buf), ctypes.byref(out_size)
            )

            # Set image_width, image_height, input_components, in_color_space
            _struct.pack_into(
                "IIiI", cinfo, _IMG_W_OFF, width, height, components, color_space
            )

            _jpeg.jpeg_set_defaults(cinfo_ptr)
            _jpeg.jpeg_set_quality(cinfo_ptr, quality, 1)
            _jpeg.jpeg_start_compress(cinfo_ptr, 1)

            src = (ctypes.c_ubyte * len(pixels)).from_buffer_copy(pixels)
            row_arr = (ctypes.POINTER(ctypes.c_ubyte) * 1)()

            for y in range(height):
                row_arr[0] = ctypes.cast(
                    ctypes.addressof(src) + y * row_stride,
                    ctypes.POINTER(ctypes.c_ubyte),
                )
                _jpeg.jpeg_write_scanlines(cinfo_ptr, row_arr, 1)

            _jpeg.jpeg_finish_compress(cinfo_ptr)
            return ctypes.string_at(out_buf, out_size.value)
        finally:
            _jpeg.jpeg_destroy_compress(cinfo_ptr)
