"""JPEG encoding and decoding with platform-native acceleration.

Each platform uses a mandatory native codec — no fallback chain:
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
    _kCGImageAlphaNoneSkipLast = 5

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

    # ------------------------------------------------------------------ #
    # macOS ImageIO decoder (ctypes)                                      #
    # ------------------------------------------------------------------ #

    _cf.CFDataCreate.restype = ctypes.c_void_p
    _cf.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, _CFIndex]

    _io.CGImageSourceCreateWithData.restype = ctypes.c_void_p
    _io.CGImageSourceCreateWithData.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _io.CGImageSourceCreateImageAtIndex.restype = ctypes.c_void_p
    _io.CGImageSourceCreateImageAtIndex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]

    _cg.CGImageGetWidth.restype = ctypes.c_size_t
    _cg.CGImageGetWidth.argtypes = [ctypes.c_void_p]
    _cg.CGImageGetHeight.restype = ctypes.c_size_t
    _cg.CGImageGetHeight.argtypes = [ctypes.c_void_p]
    _cg.CGImageGetBitsPerPixel.restype = ctypes.c_size_t
    _cg.CGImageGetBitsPerPixel.argtypes = [ctypes.c_void_p]
    _cg.CGImageGetBytesPerRow.restype = ctypes.c_size_t
    _cg.CGImageGetBytesPerRow.argtypes = [ctypes.c_void_p]
    _cg.CGImageGetDataProvider.restype = ctypes.c_void_p
    _cg.CGImageGetDataProvider.argtypes = [ctypes.c_void_p]
    _cg.CGDataProviderCopyData.restype = ctypes.c_void_p
    _cg.CGDataProviderCopyData.argtypes = [ctypes.c_void_p]

    def decode_jpeg(data: bytes) -> tuple[bytes, int, int, int]:
        """Decode JPEG bytes to raw pixels using macOS ImageIO.

        Returns ``(raw_pixels, width, height, components)`` where
        *components* is 1 for grayscale or 3 for RGB.
        """
        cf_data = _cf.CFDataCreate(None, data, len(data))
        if not cf_data:
            raise RuntimeError("CFDataCreate failed")

        try:
            source = _io.CGImageSourceCreateWithData(cf_data, None)
            if not source:
                raise RuntimeError("CGImageSourceCreateWithData failed")

            try:
                image = _io.CGImageSourceCreateImageAtIndex(source, 0, None)
                if not image:
                    raise RuntimeError("CGImageSourceCreateImageAtIndex failed")

                try:
                    width = _cg.CGImageGetWidth(image)
                    height = _cg.CGImageGetHeight(image)
                    bpp = _cg.CGImageGetBitsPerPixel(image)
                    row_bytes = _cg.CGImageGetBytesPerRow(image)
                    src_components = bpp // 8

                    # Get raw pixel data directly from the decoded CGImage
                    provider = _cg.CGImageGetDataProvider(image)
                    if not provider:
                        raise RuntimeError("CGImageGetDataProvider failed")
                    pixel_cf = _cg.CGDataProviderCopyData(provider)
                    if not pixel_cf:
                        raise RuntimeError("CGDataProviderCopyData failed")

                    try:
                        length = _cf.CFDataGetLength(pixel_cf)
                        ptr = _cf.CFDataGetBytePtr(pixel_cf)
                        raw = ctypes.string_at(ptr, length)
                    finally:
                        _cf.CFRelease(pixel_cf)

                    if src_components <= 1:
                        components = 1
                        expected_row = width
                    else:
                        components = 3
                        expected_row = width * src_components

                    # Strip row padding if stride != expected
                    if row_bytes != expected_row:
                        from _scanlib_accel import trim_rows

                        raw = trim_rows(raw, height, row_bytes, expected_row)

                    # Strip extra channels (e.g. RGBX → RGB)
                    if src_components > 3:
                        from _scanlib_accel import strip_alpha

                        raw = strip_alpha(raw, width, height, src_components)

                    return raw, width, height, components
                finally:
                    _cg.CGImageRelease(image)
            finally:
                _cf.CFRelease(source)
        finally:
            _cf.CFRelease(cf_data)

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

    # Use c_long (not HRESULT) so ctypes doesn't auto-raise on negative
    # returns like RPC_E_CHANGED_MODE — we handle those ourselves.
    _ole32.CoInitializeEx.argtypes = [c_void_p, DWORD]
    _ole32.CoInitializeEx.restype = ctypes.c_long
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
    _com_initialized = False

    _COINIT_APARTMENTTHREADED = 0x2

    def _ensure_com():
        """Initialize COM on the current thread if not already done.

        When scanning via the eSCL backend the WIA backend is never loaded,
        so COM may not have been initialized.  Tries STA to match what the
        WIA backend uses.  If the thread already has a COM apartment
        (``RPC_E_CHANGED_MODE``), that's fine — WIC works in any mode.
        """
        global _com_initialized
        if _com_initialized:
            return
        _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        _com_initialized = True

    def _get_wic_factory():
        """Lazily create the WIC factory on first use."""
        global _wic_factory
        if _wic_factory is not None:
            return _wic_factory

        _ensure_com()

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

    # ------------------------------------------------------------------ #
    # Windows WIC decoder (raw ctypes COM vtable calls)                   #
    # ------------------------------------------------------------------ #

    _GUID_WICPixelFormat24bppRGB = _make_guid("{6fddc324-4e03-4bfe-b185-3d77768dc90d}")
    _IID_IWICFormatConverter = _make_guid("{00000301-a8f2-4877-ba0a-fd2b6645fb94}")

    # IStream::Write prototype (slot 4)
    _IStream_Write = ctypes.WINFUNCTYPE(
        HRESULT, c_void_p, ctypes.c_void_p, ULONG, ctypes.POINTER(ULONG)
    )
    # IStream::Seek prototype (slot 5)
    _LARGE_INTEGER = ctypes.c_int64
    _ULARGE_INTEGER = ctypes.c_uint64
    _IStream_Seek = ctypes.WINFUNCTYPE(
        HRESULT,
        c_void_p,
        _LARGE_INTEGER,
        DWORD,
        ctypes.POINTER(_ULARGE_INTEGER),
    )

    def decode_jpeg(data: bytes) -> tuple[bytes, int, int, int]:
        """Decode JPEG bytes to raw pixels using Windows WIC.

        Returns ``(raw_pixels, width, height, components)`` where
        *components* is 1 for grayscale or 3 for RGB.
        """
        from _scanlib_accel import rgb_to_bgr

        factory = _get_wic_factory()
        stream = c_void_p()
        decoder = c_void_p()
        frame = c_void_p()
        converter = c_void_p()

        try:
            # Create IStream and write JPEG data into it
            _ole32.CreateStreamOnHGlobal(None, True, byref(stream))
            written = ULONG()
            _vtbl_call(
                stream,
                4,
                _IStream_Write,
                data,
                len(data),
                byref(written),
            )
            # Seek back to start
            _vtbl_call(stream, 5, _IStream_Seek, _LARGE_INTEGER(0), 0, None)

            # IWICImagingFactory::CreateDecoderFromStream (slot 4)
            _CreateDecoder = ctypes.WINFUNCTYPE(
                HRESULT,
                c_void_p,
                c_void_p,
                ctypes.POINTER(_GUID),
                DWORD,
                ctypes.POINTER(c_void_p),
            )
            hr = _vtbl_call(
                factory,
                4,
                _CreateDecoder,
                stream,
                None,
                0,  # WICDecodeMetadataCacheOnDemand
                byref(decoder),
            )
            if hr < 0:
                raise RuntimeError(
                    f"CreateDecoderFromStream failed: 0x{hr & 0xFFFFFFFF:08x}"
                )

            # IWICBitmapDecoder::GetFrame(0) (slot 13)
            _GetFrame = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, DWORD, ctypes.POINTER(c_void_p)
            )
            hr = _vtbl_call(decoder, 13, _GetFrame, 0, byref(frame))
            if hr < 0:
                raise RuntimeError(f"GetFrame failed: 0x{hr & 0xFFFFFFFF:08x}")

            # Get size from frame: IWICBitmapSource::GetSize (slot 3)
            _GetSize = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, ctypes.POINTER(DWORD), ctypes.POINTER(DWORD)
            )
            w = DWORD()
            h = DWORD()
            hr = _vtbl_call(frame, 3, _GetSize, byref(w), byref(h))
            if hr < 0:
                raise RuntimeError(f"GetSize failed: 0x{hr & 0xFFFFFFFF:08x}")
            width = w.value
            height = h.value

            # Get pixel format: IWICBitmapSource::GetPixelFormat (slot 4)
            _GetPixelFormat = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, ctypes.POINTER(_GUID)
            )
            pf = _GUID()
            _vtbl_call(frame, 4, _GetPixelFormat, byref(pf))

            # Determine target format
            is_gray = (
                pf.Data4[7] == _GUID_WICPixelFormat8bppGray.Data4[7]
                and pf.Data1 == _GUID_WICPixelFormat8bppGray.Data1
            )

            if is_gray:
                target_fmt = _GUID_WICPixelFormat8bppGray
                components = 1
                stride = width
            else:
                target_fmt = _GUID_WICPixelFormat24bppBGR
                components = 3
                stride = width * 3

            # Create format converter:
            # IWICImagingFactory::CreateFormatConverter (slot 10)
            _CreateConverter = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, ctypes.POINTER(c_void_p)
            )
            hr = _vtbl_call(factory, 10, _CreateConverter, byref(converter))
            if hr < 0:
                raise RuntimeError(
                    f"CreateFormatConverter failed: 0x{hr & 0xFFFFFFFF:08x}"
                )

            # IWICFormatConverter::Initialize (slot 8)
            _InitConverter = ctypes.WINFUNCTYPE(
                HRESULT,
                c_void_p,
                c_void_p,
                ctypes.POINTER(_GUID),
                DWORD,
                c_void_p,
                ctypes.c_double,
                DWORD,
            )
            hr = _vtbl_call(
                converter,
                8,
                _InitConverter,
                frame,
                byref(target_fmt),
                0,  # WICBitmapDitherTypeNone
                None,
                0.0,
                0,  # WICBitmapPaletteTypeCustom
            )
            if hr < 0:
                raise RuntimeError(
                    f"FormatConverter.Initialize failed: 0x{hr & 0xFFFFFFFF:08x}"
                )

            # IWICBitmapSource::CopyPixels (slot 7)
            buf_size = stride * height
            buf = (ctypes.c_ubyte * buf_size)()
            _CopyPixels = ctypes.WINFUNCTYPE(
                HRESULT, c_void_p, c_void_p, DWORD, DWORD, ctypes.c_void_p
            )
            hr = _vtbl_call(converter, 7, _CopyPixels, None, stride, buf_size, buf)
            if hr < 0:
                raise RuntimeError(f"CopyPixels failed: 0x{hr & 0xFFFFFFFF:08x}")

            result = bytes(buf)
            # Convert BGR to RGB for color images
            if components == 3:
                result = rgb_to_bgr(result, width, height)
            return result, width, height, components

        finally:
            _release(converter)
            _release(frame)
            _release(decoder)
            _release(stream)

else:
    # ------------------------------------------------------------------ #
    # Linux libjpeg encoder (compiled into _scanlib_accel C extension)    #
    # ------------------------------------------------------------------ #

    from _scanlib_accel import decode_jpeg as _c_decode_jpeg
    from _scanlib_accel import encode_jpeg as _c_encode_jpeg

    def encode_jpeg(
        pixels: bytes,
        width: int,
        height: int,
        color_mode: ColorMode,
        quality: int,
    ) -> bytes:
        """Encode raw pixels as baseline JPEG using libjpeg."""
        components = 3 if color_mode == ColorMode.COLOR else 1
        return _c_encode_jpeg(pixels, width, height, components, quality)

    def decode_jpeg(data: bytes) -> tuple[bytes, int, int, int]:
        """Decode JPEG bytes to raw pixels using libjpeg.

        Returns ``(raw_pixels, width, height, components)`` where
        *components* is 1 for grayscale or 3 for RGB.
        """
        return _c_decode_jpeg(data)
