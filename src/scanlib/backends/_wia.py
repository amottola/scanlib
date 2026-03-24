from __future__ import annotations

import ctypes
import math
import queue
import threading
from collections.abc import Iterator
from ctypes import HRESULT, POINTER, Structure, Union, byref, c_long, c_ulong
from ctypes import c_ulonglong, c_ushort, c_void_p

try:
    import comtypes
    from comtypes import COMMETHOD, GUID, COMObject, IUnknown
except ImportError:
    # Stubs so the module can be imported on non-Windows for testing
    class GUID:  # type: ignore[no-redef]
        def __init__(self, s: str = "") -> None:
            pass

    class IUnknown:  # type: ignore[no-redef]
        _iid_ = None

    class COMObject:  # type: ignore[no-redef]
        pass

    def COMMETHOD(*args, **kwargs):  # type: ignore[no-redef]
        return None

    class _ComtypesStub:
        GUID = GUID
        IUnknown = IUnknown
        COMObject = COMObject
        COMMETHOD = staticmethod(COMMETHOD)
        BSTR = c_void_p
        CLSCTX_LOCAL_SERVER = 4

        @staticmethod
        def CoInitialize() -> None:
            pass

        @staticmethod
        def CoCreateInstance(*a: object, **kw: object) -> None:
            pass

    comtypes = _ComtypesStub()  # type: ignore[assignment]

try:
    import ctypes.wintypes as wt

    _HAS_WIN32 = True
except (ImportError, ValueError):
    _HAS_WIN32 = False

try:
    from _scanlib_accel import bmp_to_raw as _bmp_to_raw
except ImportError:
    _bmp_to_raw = None  # type: ignore[assignment]

from .._types import (
    DISCOVERY_TIMEOUT,
    MM_PER_INCH,
    ColorMode,
    FeederEmptyError,
    ScanArea,
    ScanAborted,
    ScanError,
    ScannedPage,
    Scanner,
    ScannerDefaults,
    ScanOptions,
    ScanSource,
    SourceInfo,
    check_progress,
)

# ---------------------------------------------------------------------------
# WIA property IDs
# ---------------------------------------------------------------------------

_WIA_DIP_DEV_ID = 2
_WIA_DIP_DEV_NAME = 7
_WIA_DIP_DEV_TYPE = 5
_WIA_DIP_VEND_DESC = 3

_WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = 3086
_WIA_DPS_DOCUMENT_HANDLING_SELECT = 3088
_WIA_DPS_MAX_HORIZONTAL_SIZE = 3074
_WIA_DPS_MAX_VERTICAL_SIZE = 3075

_WIA_IPS_XRES = 6147
_WIA_IPS_YRES = 6148
_WIA_IPS_XPOS = 6149
_WIA_IPS_YPOS = 6150
_WIA_IPS_XEXTENT = 6151
_WIA_IPS_YEXTENT = 6152
_WIA_IPA_DATATYPE = 4103
_WIA_IPA_FORMAT = 4106
_WIA_IPS_PAGES = 3096

_WIA_DATA_BW = 0
_WIA_DATA_GRAY = 2
_WIA_DATA_COLOR = 3

_FLAT = 0x001
_FEED = 0x002
_FLATBED = 1
_FEEDER = 2

_WIA_FORMAT_BMP = GUID("{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}")

_WIA_ERROR_PAPER_EMPTY = -2145320957  # 0x80210003

# Property attribute flags (from WiaDef.h)
_WIA_PROP_RANGE = 0x10
_WIA_PROP_LIST = 0x20

# WIA transfer message constants
_WIA_TRANSFER_MSG_STATUS = 0x00001
_WIA_TRANSFER_MSG_END_OF_STREAM = 0x00002
_WIA_TRANSFER_MSG_DEVICE_STATUS = 0x00005

# Device type
_StiDeviceTypeScanner = 1

# Enumeration flags
_WIA_DEVINFO_ENUM_LOCAL = 0x10

_S_OK = 0

# PROPVARIANT type tags
_VT_EMPTY = 0
_VT_I2 = 2
_VT_I4 = 3
_VT_BSTR = 8
_VT_UI2 = 18
_VT_UI4 = 19
_VT_VECTOR = 0x1000

# PROPSPEC kind
_PRSPEC_PROPID = 1

# Mappings
_WIA_DATATYPE_TO_COLOR = {
    _WIA_DATA_BW: ColorMode.BW,
    _WIA_DATA_GRAY: ColorMode.GRAY,
    _WIA_DATA_COLOR: ColorMode.COLOR,
}
_COLOR_TO_WIA_DATATYPE = {v: k for k, v in _WIA_DATATYPE_TO_COLOR.items()}

# Map bmp_to_raw's (color_type, bit_depth) return to ColorMode
_BMP_COLOR_MODE = {
    (0, 1): ColorMode.BW,
    (0, 8): ColorMode.GRAY,
    (2, 8): ColorMode.COLOR,
    (6, 8): ColorMode.COLOR,  # RGBA → treated as COLOR
}

_MM10_PER_THOUSANDTH_INCH = 0.254

# ---------------------------------------------------------------------------
# ctypes structures for OLE property access
# ---------------------------------------------------------------------------


class _PROPSPEC_UNION(Union):
    _fields_ = [("propid", c_ulong), ("lpwstr", c_void_p)]


class _PROPSPEC(Structure):
    _fields_ = [("ulKind", c_ulong), ("u", _PROPSPEC_UNION)]


class _CAL(Structure):
    """Counted array of LONGs (VT_VECTOR|VT_I4)."""

    _fields_ = [("cElems", c_ulong), ("pElems", POINTER(c_long))]


class _CAUL(Structure):
    """Counted array of ULONGs (VT_VECTOR|VT_UI4)."""

    _fields_ = [("cElems", c_ulong), ("pElems", POINTER(c_ulong))]


class _PV_Union(Union):
    _fields_ = [
        ("lVal", c_long),
        ("ulVal", c_ulong),
        ("iVal", ctypes.c_short),
        ("uiVal", ctypes.c_ushort),
        ("bstrVal", c_void_p),
        ("cal", _CAL),
        ("caul", _CAUL),
    ]


class _PROPVARIANT(Structure):
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("_value", _PV_Union),
    ]


class _WiaTransferParams(Structure):
    _fields_ = [
        ("lMessage", c_long),
        ("lPercentComplete", c_long),
        ("ulTransferredBytes", c_ulonglong),
        ("hrErrorStatus", HRESULT),
    ]


# ---------------------------------------------------------------------------
# COM interface definitions (vtable order from wia_lh.h)
# ---------------------------------------------------------------------------


# Forward declarations
class IWiaPropertyStorage(IUnknown):
    _iid_ = GUID("{98B5E8A0-29CC-491a-AAC0-E6DB4FDCCEB6}")


class IEnumWIA_DEV_INFO(IUnknown):
    _iid_ = GUID("{5e38b83c-8cf1-11d1-bf92-0060081ed811}")


class IEnumWiaItem2(IUnknown):
    _iid_ = GUID("{59970AF4-CD0D-44d9-AB24-52295630E582}")


class IWiaItem2(IUnknown):
    _iid_ = GUID("{6CBA0075-1287-407d-9B77-CF0E030435CC}")


class IWiaTransferCallback(IUnknown):
    _iid_ = GUID("{27d4eaaf-28a6-4ca5-9aab-e678168b9527}")


class IStream(IUnknown):
    _iid_ = GUID("{0000000C-0000-0000-C000-000000000046}")


class IWiaTransfer(IUnknown):
    _iid_ = GUID("{c39d6942-2f4e-4d04-92fe-4ef4d3a1de5a}")


class IWiaDevMgr2(IUnknown):
    _iid_ = GUID("{79C07CF1-CBDD-41ee-8EC3-F00080CADA7A}")


_CLSID_WiaDevMgr2 = GUID("{B6C292BC-7C88-41ee-8B54-8EC92617E599}")

# --- IEnumWIA_DEV_INFO methods (vtable slots 3-7) ---
IEnumWIA_DEV_INFO._methods_ = [
    COMMETHOD(
        [],
        HRESULT,
        "Next",
        (["in"], c_ulong, "celt"),
        (["out"], POINTER(POINTER(IWiaPropertyStorage)), "rgelt"),
        (["out"], POINTER(c_ulong), "pceltFetched"),
    ),
    COMMETHOD([], HRESULT, "Skip", (["in"], c_ulong, "celt")),
    COMMETHOD([], HRESULT, "Reset"),
    COMMETHOD(
        [], HRESULT, "Clone", (["out"], POINTER(POINTER(IEnumWIA_DEV_INFO)), "ppIEnum")
    ),
    COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "celt")),
]

# --- IWiaPropertyStorage methods (vtable slots 3-17) ---
# Extends IUnknown directly in WIA (not IPropertyStorage, despite similar API)
IWiaPropertyStorage._methods_ = [
    # [3] ReadMultiple
    COMMETHOD(
        [],
        HRESULT,
        "ReadMultiple",
        (["in"], c_ulong, "cpspec"),
        (["in"], POINTER(_PROPSPEC), "rgpspec"),
        (["out"], POINTER(_PROPVARIANT), "rgpropvar"),
    ),
    # [4] WriteMultiple
    COMMETHOD(
        [],
        HRESULT,
        "WriteMultiple",
        (["in"], c_ulong, "cpspec"),
        (["in"], POINTER(_PROPSPEC), "rgpspec"),
        (["in"], POINTER(_PROPVARIANT), "rgpropvar"),
        (["in"], c_ulong, "propidNameFirst"),
    ),
    # [5] DeleteMultiple
    COMMETHOD(
        [],
        HRESULT,
        "DeleteMultiple",
        (["in"], c_ulong, "cpspec"),
        (["in"], POINTER(_PROPSPEC), "rgpspec"),
    ),
    # [6] ReadPropertyNames
    COMMETHOD(
        [],
        HRESULT,
        "ReadPropertyNames",
        (["in"], c_ulong, "cpropid"),
        (["in"], POINTER(c_ulong), "rgpropid"),
        (["out"], POINTER(c_void_p), "rglpwstrName"),
    ),
    # [7] WritePropertyNames
    COMMETHOD(
        [],
        HRESULT,
        "WritePropertyNames",
        (["in"], c_ulong, "cpropid"),
        (["in"], POINTER(c_ulong), "rgpropid"),
        (["in"], POINTER(c_void_p), "rglpwstrName"),
    ),
    # [8] DeletePropertyNames
    COMMETHOD(
        [],
        HRESULT,
        "DeletePropertyNames",
        (["in"], c_ulong, "cpropid"),
        (["in"], POINTER(c_ulong), "rgpropid"),
    ),
    # [9] Commit
    COMMETHOD([], HRESULT, "Commit", (["in"], c_ulong, "grfCommitFlags")),
    # [10] Revert
    COMMETHOD([], HRESULT, "Revert"),
    # [11] Enum
    COMMETHOD([], HRESULT, "Enum", (["out"], POINTER(c_void_p), "ppenum")),
    # [12] SetTimes
    COMMETHOD(
        [],
        HRESULT,
        "SetTimes",
        (["in"], c_void_p, "pctime"),
        (["in"], c_void_p, "patime"),
        (["in"], c_void_p, "pmtime"),
    ),
    # [13] SetClass
    COMMETHOD([], HRESULT, "SetClass", (["in"], POINTER(GUID), "clsid")),
    # [14] Stat
    COMMETHOD([], HRESULT, "Stat", (["out"], c_void_p, "pstatpsstg")),
    # [15] GetPropertyAttributes (WIA extension)
    COMMETHOD(
        [],
        HRESULT,
        "GetPropertyAttributes",
        (["in"], c_ulong, "cpspec"),
        (["in"], POINTER(_PROPSPEC), "rgpspec"),
        (["out"], POINTER(c_ulong), "rgflags"),
        (["out"], POINTER(_PROPVARIANT), "rgpropvar"),
    ),
    # [16] GetCount (WIA extension)
    COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "pulNumProps")),
    # [17] GetPropertyStream
    COMMETHOD(
        [],
        HRESULT,
        "GetPropertyStream",
        (["out"], POINTER(GUID), "pCompatibilityId"),
        (["out"], POINTER(POINTER(IStream)), "ppIStream"),
    ),
    # [18] SetPropertyStream
    COMMETHOD(
        [],
        HRESULT,
        "SetPropertyStream",
        (["in"], POINTER(GUID), "pCompatibilityId"),
        (["in"], POINTER(IStream), "pIStream"),
    ),
]

# --- IEnumWiaItem2 methods (vtable slots 3-7) ---
IEnumWiaItem2._methods_ = [
    COMMETHOD(
        [],
        HRESULT,
        "Next",
        (["in"], c_ulong, "cElt"),
        (["out"], POINTER(POINTER(IWiaItem2)), "ppIWiaItem2"),
        (["out"], POINTER(c_ulong), "pcEltFetched"),
    ),
    COMMETHOD([], HRESULT, "Skip", (["in"], c_ulong, "cElt")),
    COMMETHOD([], HRESULT, "Reset"),
    COMMETHOD(
        [], HRESULT, "Clone", (["out"], POINTER(POINTER(IEnumWiaItem2)), "ppIEnum")
    ),
    COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "cElt")),
]

# --- IWiaItem2 methods (vtable slots 3-18+) ---
IWiaItem2._methods_ = [
    # [3] CreateChildItem
    COMMETHOD(
        [],
        HRESULT,
        "CreateChildItem",
        (["in"], c_long, "lItemFlags"),
        (["in"], c_long, "lCreationFlags"),
        (["in"], comtypes.BSTR, "bstrItemName"),
        (["out"], POINTER(POINTER(IWiaItem2)), "ppIWiaItem2"),
    ),
    # [4] DeleteItem
    COMMETHOD([], HRESULT, "DeleteItem", (["in"], c_long, "lFlags")),
    # [5] EnumChildItems
    COMMETHOD(
        [],
        HRESULT,
        "EnumChildItems",
        (["in"], POINTER(GUID), "pCategoryGUID"),
        (["out"], POINTER(POINTER(IEnumWiaItem2)), "ppIEnumWiaItem2"),
    ),
    # [6] FindItemByName
    COMMETHOD(
        [],
        HRESULT,
        "FindItemByName",
        (["in"], c_long, "lFlags"),
        (["in"], comtypes.BSTR, "bstrFullItemName"),
        (["out"], POINTER(POINTER(IWiaItem2)), "ppIWiaItem2"),
    ),
    # [7] GetItemCategory
    COMMETHOD(
        [], HRESULT, "GetItemCategory", (["out"], POINTER(GUID), "pItemCategoryGUID")
    ),
    # [8] GetItemType
    COMMETHOD([], HRESULT, "GetItemType", (["out"], POINTER(c_long), "pItemType")),
]

# --- IWiaTransfer methods (vtable slots 3-6) ---
IWiaTransfer._methods_ = [
    COMMETHOD(
        [],
        HRESULT,
        "Download",
        (["in"], c_long, "lFlags"),
        (["in"], POINTER(IWiaTransferCallback), "pIWiaTransferCallback"),
    ),
    COMMETHOD(
        [],
        HRESULT,
        "Upload",
        (["in"], c_long, "lFlags"),
        (["in"], POINTER(IStream), "pSource"),
        (["in"], POINTER(IWiaTransferCallback), "pIWiaTransferCallback"),
    ),
    COMMETHOD([], HRESULT, "Cancel"),
    COMMETHOD(
        [], HRESULT, "EnumWIA_FORMAT_INFO", (["out"], POINTER(c_void_p), "ppEnum")
    ),
]

# --- IWiaTransferCallback methods (vtable slots 3-4) ---
IWiaTransferCallback._methods_ = [
    COMMETHOD(
        [],
        HRESULT,
        "TransferCallback",
        (["in"], c_long, "lFlags"),
        (["in"], POINTER(_WiaTransferParams), "pWiaTransferParams"),
    ),
    COMMETHOD(
        [],
        HRESULT,
        "GetNextStream",
        (["in"], c_long, "lFlags"),
        (["in"], comtypes.BSTR, "bstrItemName"),
        (["in"], comtypes.BSTR, "bstrFullItemName"),
        (["out"], POINTER(POINTER(IStream)), "ppDestination"),
    ),
]

# --- IWiaDevMgr2 methods (vtable slots 3+) ---
IWiaDevMgr2._methods_ = [
    # [3] EnumDeviceInfo
    COMMETHOD(
        [],
        HRESULT,
        "EnumDeviceInfo",
        (["in"], c_long, "lFlags"),
        (["out"], POINTER(POINTER(IEnumWIA_DEV_INFO)), "ppIEnum"),
    ),
    # [4] CreateDevice
    COMMETHOD(
        [],
        HRESULT,
        "CreateDevice",
        (["in"], c_long, "lFlags"),
        (["in"], comtypes.BSTR, "bstrDeviceID"),
        (["out"], POINTER(POINTER(IWiaItem2)), "ppWiaItem2Root"),
    ),
]


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------

if _HAS_WIN32:
    _ole32 = ctypes.windll.ole32
    _oleaut32 = ctypes.windll.oleaut32
    _kernel32 = ctypes.windll.kernel32

    _ole32.CoTaskMemFree.argtypes = [c_void_p]
    _ole32.CoTaskMemFree.restype = None
    _ole32.PropVariantClear.argtypes = [ctypes.POINTER(_PROPVARIANT)]
    _ole32.PropVariantClear.restype = HRESULT
    _oleaut32.SysFreeString.argtypes = [c_void_p]
    _oleaut32.SysFreeString.restype = None

    _ole32.CreateStreamOnHGlobal.restype = HRESULT
    _ole32.GetHGlobalFromStream.restype = HRESULT
    _kernel32.GlobalSize.restype = ctypes.c_size_t
    _kernel32.GlobalSize.argtypes = [c_void_p]
    _kernel32.GlobalLock.restype = c_void_p
    _kernel32.GlobalLock.argtypes = [c_void_p]
    _kernel32.GlobalUnlock.argtypes = [c_void_p]


def _make_propspec(prop_id: int) -> _PROPSPEC:
    ps = _PROPSPEC()
    ps.ulKind = _PRSPEC_PROPID
    ps.u.propid = prop_id
    return ps


def _read_prop(storage, prop_id: int, default: object = None) -> object:
    """Read a single property value (int or str)."""
    spec = _make_propspec(prop_id)
    try:
        var = storage.ReadMultiple(1, byref(spec))
    except Exception:
        return default
    vt = var.vt
    if vt in (_VT_I4, _VT_I2):
        return var._value.lVal
    if vt in (_VT_UI4, _VT_UI2):
        return var._value.ulVal
    if vt == _VT_BSTR and var._value.bstrVal:
        result = ctypes.wstring_at(var._value.bstrVal)
        _oleaut32.SysFreeString(var._value.bstrVal)
        return result
    if vt == _VT_EMPTY:
        return default
    return default


def _write_prop(storage, prop_id: int, value: int) -> None:
    """Write a single LONG property."""
    spec = _make_propspec(prop_id)
    var = _PROPVARIANT()
    var.vt = _VT_I4
    var._value.lVal = value
    storage.WriteMultiple(1, byref(spec), byref(var), 2)


def _write_prop_guid(storage, prop_id: int, value: GUID) -> None:
    """Write a GUID property (e.g. WIA_IPA_FORMAT) as a CLSID PROPVARIANT."""
    # VT_CLSID = 72; the value is a pointer to a GUID
    spec = _make_propspec(prop_id)
    var = _PROPVARIANT()
    var.vt = 72  # VT_CLSID
    guid_copy = GUID()
    ctypes.memmove(byref(guid_copy), byref(value), ctypes.sizeof(GUID))
    var._value.bstrVal = ctypes.cast(ctypes.pointer(guid_copy), c_void_p).value
    storage.WriteMultiple(1, byref(spec), byref(var), 2)


def _read_prop_attributes(storage, prop_id: int) -> tuple[int, list[int]]:
    """Read property attributes (flags and valid values).

    Returns (flags, values) where values is a list of ints for
    WIA_PROP_LIST or [min, max, step] for WIA_PROP_RANGE.
    """
    spec = _make_propspec(prop_id)
    try:
        flags_val, var = storage.GetPropertyAttributes(1, byref(spec))
    except Exception:
        return (0, [])

    result: list[int] = []
    flags_int = int(flags_val)
    vt = var.vt
    if vt == (_VT_VECTOR | _VT_I4) and var._value.cal.cElems > 0:
        n = var._value.cal.cElems
        result = [var._value.cal.pElems[i] for i in range(n)]
    elif vt == (_VT_VECTOR | _VT_UI4) and var._value.caul.cElems > 0:
        n = var._value.caul.cElems
        result = [var._value.caul.pElems[i] for i in range(n)]
    # Free the PROPVARIANT's allocated memory (vector arrays, etc.)
    _ole32.PropVariantClear(byref(var))
    return (flags_int, result)


# ---------------------------------------------------------------------------
# Capability readers
# ---------------------------------------------------------------------------


def _read_wia_resolutions(storage) -> list[int]:
    """Read supported DPI values."""
    from .._types import normalize_resolutions

    flags, values = _read_prop_attributes(storage, _WIA_IPS_XRES)
    if flags & _WIA_PROP_RANGE and len(values) >= 3:
        lo, hi, step = values[0], values[1], max(1, values[2])
        return normalize_resolutions(list(range(lo, hi + 1, step)))
    if flags & _WIA_PROP_LIST and len(values) > 2:
        # WIA list format: [count, nominal, value1, value2, ...]
        return sorted(values[2:])
    # Fallback: return current value
    val = _read_prop(storage, _WIA_IPS_XRES)
    return [int(val)] if val is not None else []


def _read_wia_color_modes(storage) -> list[ColorMode]:
    """Read supported color modes."""
    flags, values = _read_prop_attributes(storage, _WIA_IPA_DATATYPE)
    if flags & _WIA_PROP_LIST and len(values) > 2:
        # WIA list format: [count, nominal, value1, value2, ...]
        modes: list[ColorMode] = []
        for v in values[2:]:
            mapped = _WIA_DATATYPE_TO_COLOR.get(int(v))
            if mapped is not None and mapped not in modes:
                modes.append(mapped)
        return modes
    val = _read_prop(storage, _WIA_IPA_DATATYPE)
    if val is not None:
        mapped = _WIA_DATATYPE_TO_COLOR.get(int(val))
        return [mapped] if mapped else []
    return []


def _read_wia_sources(storage) -> list[ScanSource]:
    """Determine available scan sources from device capabilities."""
    caps = _read_prop(storage, _WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES, 0)
    sources: list[ScanSource] = []
    if caps & _FLAT:
        sources.append(ScanSource.FLATBED)
    if caps & _FEED:
        sources.append(ScanSource.FEEDER)
    if not sources:
        sources.append(ScanSource.FLATBED)
    return sources


def _read_wia_max_scan_area(storage) -> ScanArea | None:
    """Read max scan area (thousandths of inch -> 1/10mm)."""
    max_h = _read_prop(storage, _WIA_DPS_MAX_HORIZONTAL_SIZE)
    max_v = _read_prop(storage, _WIA_DPS_MAX_VERTICAL_SIZE)
    if max_h is not None and max_v is not None:
        width = math.ceil(int(max_h) * _MM10_PER_THOUSANDTH_INCH)
        height = math.ceil(int(max_v) * _MM10_PER_THOUSANDTH_INCH)
        return ScanArea(x=0, y=0, width=width, height=height)
    return None


def _read_wia_defaults(storage, sources: list[ScanSource]) -> ScannerDefaults | None:
    """Read default settings from WIA item properties."""
    try:
        dpi = int(_read_prop(storage, _WIA_IPS_XRES, 300))
        dt = _read_prop(storage, _WIA_IPA_DATATYPE, _WIA_DATA_COLOR)
        color_mode = _WIA_DATATYPE_TO_COLOR.get(int(dt), ColorMode.COLOR)
        source = sources[0] if sources else None
        return ScannerDefaults(dpi=dpi, color_mode=color_mode, source=source)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Transfer callback
# ---------------------------------------------------------------------------


_ADDREF_TYPE = ctypes.CFUNCTYPE(c_ulong, c_void_p)


def _com_addref(ptr: int) -> None:
    """Call IUnknown::AddRef on a raw COM pointer."""
    vtbl = ctypes.cast(ptr, POINTER(c_void_p))[0]
    fn = _ADDREF_TYPE(ctypes.cast(vtbl, POINTER(c_void_p))[1])
    fn(ptr)


def _com_release(ptr: int) -> None:
    """Call IUnknown::Release on a raw COM pointer."""
    vtbl = ctypes.cast(ptr, POINTER(c_void_p))[0]
    fn = _ADDREF_TYPE(ctypes.cast(vtbl, POINTER(c_void_p))[2])
    fn(ptr)


def _read_stream_data(stream_ptr: int) -> bytes:
    """Read all data from a memory-backed IStream and release it."""
    hglobal = c_void_p()
    _ole32.GetHGlobalFromStream(c_void_p(stream_ptr), byref(hglobal))
    size = _kernel32.GlobalSize(hglobal)
    if size == 0:
        _com_release(stream_ptr)
        return b""
    ptr = _kernel32.GlobalLock(hglobal)
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.GlobalUnlock(hglobal)
        _com_release(stream_ptr)


class _TransferCallback(COMObject):
    """IWiaTransferCallback implementation for streamed page transfers."""

    _com_interfaces_ = [IWiaTransferCallback]

    def __init__(self, progress_fn, abort_event=None):
        super().__init__()
        self.pages: list[ScannedPage] = []
        self._progress = progress_fn
        self._abort_event = abort_event
        self._current_stream: int | None = None
        self._aborted = False

    def IWiaTransferCallback_TransferCallback(self, this, lFlags, pWiaTransferParams):
        params = pWiaTransferParams[0]
        msg = params.lMessage

        if msg == _WIA_TRANSFER_MSG_STATUS:
            if self._abort_event is not None and self._abort_event.is_set():
                self._aborted = True
                return _WIA_ERROR_PAPER_EMPTY
            try:
                check_progress(self._progress, params.lPercentComplete)
            except ScanAborted:
                self._aborted = True
                return _WIA_ERROR_PAPER_EMPTY  # signal abort to WIA
        elif msg == _WIA_TRANSFER_MSG_END_OF_STREAM:
            if self._current_stream is not None:
                bmp_data = _read_stream_data(self._current_stream)
                if bmp_data:
                    raw, w, h, ct, bd = _bmp_to_raw(bmp_data)
                    mode = _BMP_COLOR_MODE.get((ct, bd), ColorMode.COLOR)
                    self.pages.append(
                        ScannedPage(
                            data=raw,
                            width=w,
                            height=h,
                            color_mode=mode,
                        )
                    )
                self._current_stream = None
        elif msg == _WIA_TRANSFER_MSG_DEVICE_STATUS:
            hr = params.hrErrorStatus
            if hr == _WIA_ERROR_PAPER_EMPTY:
                pass  # feeder empty — handled after Download returns
        return _S_OK

    def IWiaTransferCallback_GetNextStream(
        self, this, lFlags, bstrItemName, bstrFullItemName, ppDestination
    ):
        stream = c_void_p()
        hr = _ole32.CreateStreamOnHGlobal(None, True, byref(stream))
        if hr != _S_OK:
            return hr
        self._current_stream = stream.value
        # AddRef so the stream survives WIA's Release; our _read_stream_data
        # will call Release when it's done reading.
        _com_addref(stream.value)
        ctypes.cast(ppDestination, POINTER(c_void_p))[0] = stream.value
        return _S_OK


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class WiaBackend:
    """Windows scanning backend using WIA 2.0 low-level COM interfaces.

    Thread-safe: all operations execute on a dedicated STA worker thread
    with a Win32 message pump for COM apartment marshaling.
    """

    def __init__(self) -> None:
        self._handles: dict[str, tuple] = {}  # name -> (root_item, child_item)
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._work_event = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        comtypes.CoInitialize()  # STA for apartment-threaded COM objects
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
        except (ImportError, AttributeError):
            self._ready.set()
            self._run_simple()
            return

        kernel32.CreateEventW.restype = c_void_p
        work_event = kernel32.CreateEventW(None, False, False, None)
        self._work_event = work_event
        self._ready.set()

        handles = (wt.HANDLE * 1)(work_event)
        msg = wt.MSG()

        while True:
            user32.MsgWaitForMultipleObjects(1, handles, False, 0xFFFFFFFF, 0x04FF)
            while user32.PeekMessageW(byref(msg), None, 0, 0, 0x0001):
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
            while True:
                try:
                    func, args, event, box = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    box["value"] = func(*args)
                except BaseException as exc:
                    box["error"] = exc
                event.set()

    def _run_simple(self) -> None:
        """Simple event loop without Win32 message pump (test fallback)."""
        while True:
            func, args, event, box = self._queue.get()
            try:
                box["value"] = func(*args)
            except BaseException as exc:
                box["error"] = exc
            event.set()

    def _signal_work(self) -> None:
        evt = self._work_event
        if evt is not None:
            ctypes.windll.kernel32.SetEvent(evt)

    def _dispatch(self, func, *args):
        event = threading.Event()
        box: dict = {}
        self._queue.put((func, args, event, box))
        self._signal_work()
        event.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # --- Public ScanBackend protocol ---

    def list_scanners(self, timeout: float = DISCOVERY_TIMEOUT) -> list[Scanner]:
        from .._mdns import browse_in_thread

        t_mdns, loc_box = browse_in_thread(timeout)

        event = threading.Event()
        box: dict = {}
        self._queue.put((self._list_scanners_impl, (), event, box))
        self._signal_work()
        if not event.wait(timeout):
            return []
        if "error" in box:
            raise box["error"]
        scanners = box.get("value", [])

        t_mdns.join(timeout=0.5)
        locations = loc_box[0]

        # Match WIA scanners to mDNS location by device name.
        # The mDNS ``ty`` TXT record often matches the WIA device name.
        name_locations = locations.by_name if locations else {}
        for s in scanners:
            s._backend_impl = self
            loc = name_locations.get(s.name)
            if loc:
                s._location = loc
        return scanners

    def open_scanner(self, scanner: Scanner) -> None:
        return self._dispatch(self._open_scanner_impl, scanner)

    def close_scanner(self, scanner: Scanner) -> None:
        return self._dispatch(self._close_scanner_impl, scanner)

    def abort_scan(self, scanner: Scanner) -> None:
        # Set the abort event; the transfer callback checks it and
        # signals WIA to stop the transfer on the next progress tick.
        scanner._abort_event.set()

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]:
        pages = self._dispatch(self._scan_pages_impl, scanner, options)
        yield from pages

    # --- Implementation (runs on worker thread) ---

    def _create_device_manager(self) -> IWiaDevMgr2:
        return comtypes.CoCreateInstance(
            _CLSID_WiaDevMgr2,
            interface=IWiaDevMgr2,
            clsctx=comtypes.CLSCTX_LOCAL_SERVER,
        )

    def _list_scanners_impl(self) -> list[Scanner]:
        dm = self._create_device_manager()
        enum = dm.EnumDeviceInfo(_WIA_DEVINFO_ENUM_LOCAL)

        scanners: list[Scanner] = []
        count = enum.GetCount()

        for _ in range(count):
            try:
                storage, fetched = enum.Next(1)
            except Exception:
                break
            if not fetched:
                break

            dev_type = _read_prop(storage, _WIA_DIP_DEV_TYPE, 0)
            # WIA device type is encoded: low word is STI type
            if (int(dev_type) & 0xFFFF) != _StiDeviceTypeScanner:
                continue

            dev_id = _read_prop(storage, _WIA_DIP_DEV_ID, "")
            name = _read_prop(storage, _WIA_DIP_DEV_NAME, "Unknown Scanner")

            scanner = Scanner(
                name=str(name),
                vendor=None,
                model=None,
                backend="wia",
                scanner_id=str(dev_id),
            )
            scanners.append(scanner)
        return scanners

    def _open_scanner_impl(self, scanner: Scanner) -> None:
        try:
            dm = self._create_device_manager()
            root_item = dm.CreateDevice(0, scanner.id)
        except Exception as exc:
            raise ScanError(f"Failed to open scanner {scanner.id!r}: {exc}") from exc

        # Read device-level properties from root item
        try:
            root_storage = root_item.QueryInterface(IWiaPropertyStorage)
        except Exception:
            root_storage = None

        source_types: list[ScanSource] = []
        device_area: ScanArea | None = None
        if root_storage is not None:
            source_types = _read_wia_sources(root_storage)
            device_area = _read_wia_max_scan_area(root_storage)
        if not source_types:
            source_types = [ScanSource.FLATBED]

        # Get first child item for item-level properties
        child_item = None
        try:
            enum_items = root_item.EnumChildItems(None)
            child, fetched = enum_items.Next(1)
            if fetched:
                child_item = child
        except Exception:
            pass

        source_infos: list[SourceInfo] = []
        if child_item is not None:
            try:
                item_storage = child_item.QueryInterface(IWiaPropertyStorage)

                # Read per-source resolutions and color modes.
                for source in source_types:
                    if root_storage is not None:
                        try:
                            select_val = (
                                _FEEDER if source == ScanSource.FEEDER else _FLATBED
                            )
                            _write_prop(
                                root_storage,
                                _WIA_DPS_DOCUMENT_HANDLING_SELECT,
                                select_val,
                            )
                        except Exception:
                            pass
                    source_infos.append(
                        SourceInfo(
                            type=source,
                            resolutions=_read_wia_resolutions(item_storage),
                            color_modes=_read_wia_color_modes(item_storage),
                            max_scan_area=device_area,
                        )
                    )

                scanner._defaults = _read_wia_defaults(item_storage, source_types)
            except Exception:
                scanner._defaults = None
        else:
            # No child item — build SourceInfo with device-level area only
            for source in source_types:
                source_infos.append(
                    SourceInfo(
                        type=source,
                        resolutions=[],
                        color_modes=[],
                        max_scan_area=device_area,
                    )
                )
            scanner._defaults = None

        scanner._sources = source_infos
        self._handles[scanner.id] = (root_item, child_item)

    def _close_scanner_impl(self, scanner: Scanner) -> None:
        self._handles.pop(scanner.id, None)

    def _scan_pages_impl(
        self, scanner: Scanner, options: ScanOptions
    ) -> list[ScannedPage]:
        items = self._handles.get(scanner.id)
        if items is None:
            raise ScanError("Scanner is not open")

        root_item, child_item = items
        if child_item is None:
            raise ScanError("Scanner has no scan items")

        try:
            # Get property storage for the child item
            item_storage = child_item.QueryInterface(IWiaPropertyStorage)

            # Set source on root item
            if options.source is not None:
                try:
                    root_storage = root_item.QueryInterface(IWiaPropertyStorage)
                    if options.source == ScanSource.FEEDER:
                        _write_prop(
                            root_storage, _WIA_DPS_DOCUMENT_HANDLING_SELECT, _FEEDER
                        )
                    elif options.source == ScanSource.FLATBED:
                        _write_prop(
                            root_storage, _WIA_DPS_DOCUMENT_HANDLING_SELECT, _FLATBED
                        )
                except Exception:
                    pass

            # Set resolution
            _write_prop(item_storage, _WIA_IPS_XRES, options.dpi)
            _write_prop(item_storage, _WIA_IPS_YRES, options.dpi)

            # Set color mode
            wia_dt = _COLOR_TO_WIA_DATATYPE.get(options.color_mode)
            if wia_dt is not None:
                _write_prop(item_storage, _WIA_IPA_DATATYPE, wia_dt)

            # Set scan area (convert 1/10mm to pixels)
            if options.scan_area is not None:
                sa = options.scan_area
                px_per_mm10 = options.dpi / (MM_PER_INCH * 10)
                x_px = int(sa.x * px_per_mm10)
                y_px = int(sa.y * px_per_mm10)
                width_px = int(sa.width * px_per_mm10)
                height_px = int(sa.height * px_per_mm10)
                _write_prop(item_storage, _WIA_IPS_XPOS, x_px)
                _write_prop(item_storage, _WIA_IPS_YPOS, y_px)
                _write_prop(item_storage, _WIA_IPS_XEXTENT, width_px)
                _write_prop(item_storage, _WIA_IPS_YEXTENT, height_px)

            # Set format to BMP
            _write_prop_guid(item_storage, _WIA_IPA_FORMAT, _WIA_FORMAT_BMP)

            is_feeder = options.source == ScanSource.FEEDER

            # For feeder: scan all pages
            if is_feeder:
                _write_prop(item_storage, _WIA_IPS_PAGES, 0)

            check_progress(options.progress, 0)

            # Get IWiaTransfer and run download
            transfer = child_item.QueryInterface(IWiaTransfer)
            all_pages: list[ScannedPage] = []

            while True:
                callback = _TransferCallback(options.progress, scanner._abort_event)
                try:
                    transfer.Download(0, callback)
                except Exception as exc:
                    hr = getattr(exc, "hresult", None)
                    msg_text = str(exc).lower()
                    if callback._aborted:
                        raise ScanAborted("Scan aborted") from exc
                    if (
                        hr == _WIA_ERROR_PAPER_EMPTY
                        or "paper" in msg_text
                        or "empty" in msg_text
                    ):
                        if is_feeder and not all_pages and not callback.pages:
                            raise FeederEmptyError("No documents in feeder") from exc
                        # Feeder empty after some pages — that's normal
                    elif "cancel" in msg_text or "abort" in msg_text:
                        raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                    elif not callback.pages and not all_pages:
                        raise ScanError(f"Scan failed: {exc}") from exc

                all_pages.extend(callback.pages)

                if not is_feeder:
                    break

            if not all_pages:
                raise ScanError("No pages were scanned")

            check_progress(options.progress, 100)
            return all_pages

        except (ScanAborted, ScanError, FeederEmptyError):
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
