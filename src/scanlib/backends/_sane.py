"""Linux scanning backend using SANE (via ctypes, no external dependencies)."""

from __future__ import annotations

import ctypes
import ctypes.util
import math
import re
import threading
from collections import namedtuple
from collections.abc import Iterator
from typing import Any

from _scanlib_accel import trim_rows

from .._types import (
    DISCOVERY_TIMEOUT,
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
    wait_or_cancel,
)

# ---------------------------------------------------------------------------
# Load libsane
# ---------------------------------------------------------------------------

_lib_path = ctypes.util.find_library("sane")
_lib = ctypes.CDLL(_lib_path) if _lib_path else None

# ---------------------------------------------------------------------------
# SANE constants
# ---------------------------------------------------------------------------

_STATUS_GOOD = 0
_STATUS_UNSUPPORTED = 1
_STATUS_CANCELLED = 2
_STATUS_DEVICE_BUSY = 3
_STATUS_INVAL = 4
_STATUS_EOF = 5
_STATUS_JAMMED = 6
_STATUS_NO_DOCS = 7
_STATUS_COVER_OPEN = 8
_STATUS_IO_ERROR = 9
_STATUS_NO_MEM = 10
_STATUS_ACCESS_DENIED = 11

_TYPE_BOOL = 0
_TYPE_INT = 1
_TYPE_FIXED = 2
_TYPE_STRING = 3
_TYPE_BUTTON = 4
_TYPE_GROUP = 5

_CONSTRAINT_NONE = 0
_CONSTRAINT_RANGE = 1
_CONSTRAINT_WORD_LIST = 2
_CONSTRAINT_STRING_LIST = 3

_FRAME_GRAY = 0
_FRAME_RGB = 1

_ACTION_GET_VALUE = 0
_ACTION_SET_VALUE = 1

_FIXED_SCALE_SHIFT = 16

_STATUS_NAMES = {
    _STATUS_GOOD: "good",
    _STATUS_UNSUPPORTED: "unsupported",
    _STATUS_CANCELLED: "cancelled",
    _STATUS_DEVICE_BUSY: "device busy",
    _STATUS_INVAL: "invalid argument",
    _STATUS_EOF: "end of file",
    _STATUS_JAMMED: "jammed",
    _STATUS_NO_DOCS: "no docs",
    _STATUS_COVER_OPEN: "cover open",
    _STATUS_IO_ERROR: "I/O error",
    _STATUS_NO_MEM: "out of memory",
    _STATUS_ACCESS_DENIED: "access denied",
}


def _fixed_to_float(v: int) -> float:
    return v / (1 << _FIXED_SCALE_SHIFT)


def _float_to_fixed(v: float) -> int:
    return int(round(v * (1 << _FIXED_SCALE_SHIFT)))


# ---------------------------------------------------------------------------
# SANE C structures
# ---------------------------------------------------------------------------


class _SANE_Device(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("vendor", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("type", ctypes.c_char_p),
    ]


class _SANE_Range(ctypes.Structure):
    _fields_ = [
        ("min", ctypes.c_int),
        ("max", ctypes.c_int),
        ("quant", ctypes.c_int),
    ]


class _SANE_Constraint(ctypes.Union):
    _fields_ = [
        ("string_list", ctypes.POINTER(ctypes.c_char_p)),
        ("word_list", ctypes.POINTER(ctypes.c_int)),
        ("range", ctypes.POINTER(_SANE_Range)),
    ]


class _SANE_Option_Descriptor(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("title", ctypes.c_char_p),
        ("desc", ctypes.c_char_p),
        ("type", ctypes.c_int),
        ("unit", ctypes.c_int),
        ("size", ctypes.c_int),
        ("cap", ctypes.c_int),
        ("constraint_type", ctypes.c_int),
        ("constraint", _SANE_Constraint),
    ]


class _SANE_Parameters(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_int),
        ("last_frame", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("pixels_per_line", ctypes.c_int),
        ("lines", ctypes.c_int),
        ("depth", ctypes.c_int),
    ]


_SANE_Handle = ctypes.c_void_p

Parameters = namedtuple(
    "Parameters",
    ["format", "last_frame", "bytes_per_line", "pixels_per_line", "lines", "depth"],
)

# ---------------------------------------------------------------------------
# Function signatures
# ---------------------------------------------------------------------------

if _lib is not None:
    _lib.sane_init.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_void_p]
    _lib.sane_init.restype = ctypes.c_int

    _lib.sane_exit.argtypes = []
    _lib.sane_exit.restype = None

    _lib.sane_get_devices.argtypes = [
        ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(_SANE_Device))),
        ctypes.c_int,
    ]
    _lib.sane_get_devices.restype = ctypes.c_int

    _lib.sane_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(_SANE_Handle)]
    _lib.sane_open.restype = ctypes.c_int

    _lib.sane_close.argtypes = [_SANE_Handle]
    _lib.sane_close.restype = None

    _lib.sane_get_option_descriptor.argtypes = [_SANE_Handle, ctypes.c_int]
    _lib.sane_get_option_descriptor.restype = ctypes.POINTER(_SANE_Option_Descriptor)

    _lib.sane_control_option.argtypes = [
        _SANE_Handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    _lib.sane_control_option.restype = ctypes.c_int

    _lib.sane_start.argtypes = [_SANE_Handle]
    _lib.sane_start.restype = ctypes.c_int

    _lib.sane_get_parameters.argtypes = [_SANE_Handle, ctypes.POINTER(_SANE_Parameters)]
    _lib.sane_get_parameters.restype = ctypes.c_int

    _lib.sane_read.argtypes = [
        _SANE_Handle,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    _lib.sane_read.restype = ctypes.c_int

    _lib.sane_cancel.argtypes = [_SANE_Handle]
    _lib.sane_cancel.restype = None


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _check_status(status: int, context: str = "") -> None:
    if status != _STATUS_GOOD:
        name = _STATUS_NAMES.get(status, f"unknown ({status})")
        msg = f"SANE error: {name}"
        if context:
            msg = f"{context}: {msg}"
        raise ScanError(msg)


def _ensure_lib() -> None:
    if _lib is None:
        raise ScanError(
            "libsane not found. Install SANE (e.g. 'apt install libsane-dev')."
        )


def _init() -> None:
    _ensure_lib()
    version = ctypes.c_int()
    status = _lib.sane_init(ctypes.byref(version), None)
    _check_status(status, "sane_init")


def _get_devices() -> list[tuple[str, str, str, str]]:
    _ensure_lib()
    device_list = ctypes.POINTER(ctypes.POINTER(_SANE_Device))()
    status = _lib.sane_get_devices(ctypes.byref(device_list), ctypes.c_int(0))
    _check_status(status, "sane_get_devices")

    result = []
    i = 0
    while device_list[i]:
        dev = device_list[i].contents
        result.append(
            (
                dev.name.decode("utf-8", errors="replace") if dev.name else "",
                dev.vendor.decode("utf-8", errors="replace") if dev.vendor else "",
                dev.model.decode("utf-8", errors="replace") if dev.model else "",
                dev.type.decode("utf-8", errors="replace") if dev.type else "",
            )
        )
        i += 1
    return result


_LIBUSB_RE = re.compile(r"libusb:(\d+:\d+)")
_HPAIO_SERIAL_RE = re.compile(r"hpaio:/usb/.*\?serial=(\S+)")
_IP_RE = re.compile(r"(?:\?ip=|://)((?:\d{1,3}\.){3}\d{1,3})\b")


def _extract_device_id(device_name: str) -> str | None:
    """Extract an identifier for deduplicating SANE devices.

    Returns a canonical string when possible, or None when the device
    cannot be matched.  Handles USB and network patterns:

    - ``backend:libusb:BUS:DEV`` — USB id from most SANE backends
    - ``hpaio:/usb/MODEL?serial=SN`` — HP HPLIP USB; serial resolved
      to bus:dev via sysfs
    - ``hpaio:/net/MODEL?ip=ADDR``, ``escl://ADDR:PORT/...``,
      ``airscan:... http://ADDR:PORT/...`` — network scanners; the
      IP address is used as the dedup key
    """
    m = _LIBUSB_RE.search(device_name)
    if m:
        return f"usb:{m.group(1)}"

    m = _HPAIO_SERIAL_RE.match(device_name)
    if m:
        serial = m.group(1)
        try:
            from pathlib import Path

            for dev_dir in Path("/sys/bus/usb/devices").iterdir():
                serial_file = dev_dir / "serial"
                if serial_file.exists() and serial_file.read_text().strip() == serial:
                    busnum = (dev_dir / "busnum").read_text().strip()
                    devnum = (dev_dir / "devnum").read_text().strip()
                    return f"usb:{int(busnum):03d}:{int(devnum):03d}"
        except (OSError, ValueError):
            pass

    m = _IP_RE.search(device_name)
    if m:
        return f"ip:{m.group(1)}"

    return None


def _open_device(name: str) -> _SaneDevice:
    _ensure_lib()
    handle = _SANE_Handle()
    status = _lib.sane_open(name.encode("utf-8"), ctypes.byref(handle))
    _check_status(status, f"sane_open({name!r})")
    return _SaneDevice(handle)


# ---------------------------------------------------------------------------
# SANE device wrapper
# ---------------------------------------------------------------------------


class _SaneDevice:
    """Wrapper around a SANE device handle."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self._option_map: dict[str, int] | None = None

    def close(self) -> None:
        if self._handle is not None:
            _lib.sane_close(self._handle)
            self._handle = None

    def cancel(self) -> None:
        if self._handle is not None:
            _lib.sane_cancel(self._handle)

    def has_option(self, name: str) -> bool:
        self._build_option_map()
        return name in self._option_map

    def _build_option_map(self) -> None:
        if self._option_map is not None:
            return
        self._option_map = {}
        i = 0
        while True:
            desc_p = _lib.sane_get_option_descriptor(self._handle, i)
            if not desc_p:
                break
            desc = desc_p.contents
            if desc.name:
                name = desc.name.decode("utf-8", errors="replace")
                self._option_map[name] = i
            i += 1

    def _get_descriptor(self, option_num: int) -> _SANE_Option_Descriptor:
        desc_p = _lib.sane_get_option_descriptor(self._handle, option_num)
        if not desc_p:
            raise ScanError(f"Option {option_num} not found")
        return desc_p.contents

    def _read_constraint(self, desc: _SANE_Option_Descriptor) -> Any:
        if desc.constraint_type == _CONSTRAINT_RANGE:
            r = desc.constraint.range.contents
            if desc.type == _TYPE_FIXED:
                return (
                    _fixed_to_float(r.min),
                    _fixed_to_float(r.max),
                    _fixed_to_float(r.quant),
                )
            return (r.min, r.max, r.quant)

        if desc.constraint_type == _CONSTRAINT_STRING_LIST:
            result = []
            i = 0
            while desc.constraint.string_list[i]:
                result.append(
                    desc.constraint.string_list[i].decode("utf-8", errors="replace")
                )
                i += 1
            return result

        if desc.constraint_type == _CONSTRAINT_WORD_LIST:
            count = desc.constraint.word_list[0]
            values = [desc.constraint.word_list[i + 1] for i in range(count)]
            if desc.type == _TYPE_FIXED:
                return [_fixed_to_float(v) for v in values]
            return values

        return None

    def get_options(self) -> list[tuple]:
        """Return options as tuples: (name, title, desc, type, unit, size, cap, constraint)."""
        result = []
        i = 1  # option 0 is the count
        while True:
            desc_p = _lib.sane_get_option_descriptor(self._handle, i)
            if not desc_p:
                break
            desc = desc_p.contents
            name = desc.name.decode("utf-8", errors="replace") if desc.name else ""
            title = desc.title.decode("utf-8", errors="replace") if desc.title else ""
            description = (
                desc.desc.decode("utf-8", errors="replace") if desc.desc else ""
            )
            constraint = self._read_constraint(desc)
            result.append(
                (
                    name,
                    title,
                    description,
                    desc.type,
                    desc.unit,
                    desc.size,
                    desc.cap,
                    constraint,
                )
            )
            i += 1
        return result

    def set_option(self, name: str, value: Any) -> None:
        self._build_option_map()
        option_num = self._option_map.get(name)
        if option_num is None:
            raise ScanError(f"Unknown option: {name!r}")

        desc = self._get_descriptor(option_num)
        info = ctypes.c_int()

        if desc.type == _TYPE_STRING:
            encoded = str(value).encode("utf-8")
            buf = ctypes.create_string_buffer(encoded, desc.size)
            status = _lib.sane_control_option(
                self._handle,
                option_num,
                _ACTION_SET_VALUE,
                buf,
                ctypes.byref(info),
            )
        elif desc.type == _TYPE_INT:
            val = ctypes.c_int(int(value))
            status = _lib.sane_control_option(
                self._handle,
                option_num,
                _ACTION_SET_VALUE,
                ctypes.byref(val),
                ctypes.byref(info),
            )
        elif desc.type == _TYPE_FIXED:
            val = ctypes.c_int(_float_to_fixed(float(value)))
            status = _lib.sane_control_option(
                self._handle,
                option_num,
                _ACTION_SET_VALUE,
                ctypes.byref(val),
                ctypes.byref(info),
            )
        elif desc.type == _TYPE_BOOL:
            val = ctypes.c_int(1 if value else 0)
            status = _lib.sane_control_option(
                self._handle,
                option_num,
                _ACTION_SET_VALUE,
                ctypes.byref(val),
                ctypes.byref(info),
            )
        else:
            raise ScanError(f"Cannot set option {name!r} of type {desc.type}")

        _check_status(status, f"set_option({name!r})")

        if info.value & 0x04:  # SANE_INFO_RELOAD_OPTIONS
            self._option_map = None

    def get_option(self, name: str) -> Any:
        self._build_option_map()
        option_num = self._option_map.get(name)
        if option_num is None:
            raise ScanError(f"Unknown option: {name!r}")

        desc = self._get_descriptor(option_num)
        info = ctypes.c_int()

        if desc.type == _TYPE_STRING:
            buf = ctypes.create_string_buffer(desc.size)
            status = _lib.sane_control_option(
                self._handle,
                option_num,
                _ACTION_GET_VALUE,
                buf,
                ctypes.byref(info),
            )
            _check_status(status, f"get_option({name!r})")
            return buf.value.decode("utf-8", errors="replace")
        elif desc.type in (_TYPE_INT, _TYPE_BOOL):
            val = ctypes.c_int()
            status = _lib.sane_control_option(
                self._handle,
                option_num,
                _ACTION_GET_VALUE,
                ctypes.byref(val),
                ctypes.byref(info),
            )
            _check_status(status, f"get_option({name!r})")
            return val.value
        elif desc.type == _TYPE_FIXED:
            val = ctypes.c_int()
            status = _lib.sane_control_option(
                self._handle,
                option_num,
                _ACTION_GET_VALUE,
                ctypes.byref(val),
                ctypes.byref(info),
            )
            _check_status(status, f"get_option({name!r})")
            return _fixed_to_float(val.value)
        else:
            raise ScanError(f"Cannot get option {name!r} of type {desc.type}")

    def start(self) -> int:
        return _lib.sane_start(self._handle)

    def get_parameters(self) -> Parameters:
        params = _SANE_Parameters()
        status = _lib.sane_get_parameters(self._handle, ctypes.byref(params))
        _check_status(status, "sane_get_parameters")
        return Parameters(
            format=params.format,
            last_frame=bool(params.last_frame),
            bytes_per_line=params.bytes_per_line,
            pixels_per_line=params.pixels_per_line,
            lines=params.lines,
            depth=params.depth,
        )

    def read(self, max_len: int = 65536) -> tuple[bytes, int]:
        buf = (ctypes.c_ubyte * max_len)()
        length = ctypes.c_int()
        status = _lib.sane_read(self._handle, buf, max_len, ctypes.byref(length))
        if status == _STATUS_GOOD or status == _STATUS_EOF:
            return bytes(buf[: length.value]), status
        return b"", status


# ---------------------------------------------------------------------------
# Backend logic
# ---------------------------------------------------------------------------

_COLOR_MODE_MAP = {
    ColorMode.COLOR: "color",
    ColorMode.GRAY: "gray",
    ColorMode.BW: "lineart",
}

_SANE_MODE_TO_COLOR = {v: k for k, v in _COLOR_MODE_MAP.items()}

_SANE_SOURCE_MAP = {
    "flatbed": ScanSource.FLATBED,
    "automatic document feeder": ScanSource.FEEDER,
    "adf": ScanSource.FEEDER,
}

_SCAN_SOURCE_TO_SANE = {
    ScanSource.FLATBED: "Flatbed",
    ScanSource.FEEDER: "Automatic Document Feeder",
}


def _get_options(dev: _SaneDevice) -> list[tuple]:
    try:
        opts = dev.get_options()
    except Exception:
        return []
    return [opt for opt in opts if isinstance(opt, tuple) and len(opt) >= 8]


def _parse_sources(opts: list[tuple]) -> tuple[list[ScanSource], dict[ScanSource, str]]:
    """Extract supported sources from SANE option descriptors.

    Returns ``(sources, sane_names)`` where *sane_names* maps each
    :class:`ScanSource` to the original string reported by the backend.
    """
    for opt in opts:
        if opt[0] == "source":
            constraint = opt[7]
            if isinstance(constraint, (list, tuple)):
                sources: list[ScanSource] = []
                sane_names: dict[ScanSource, str] = {}
                for value in constraint:
                    sval = str(value)
                    key = sval.lower()
                    for pattern, source in _SANE_SOURCE_MAP.items():
                        if pattern in key and source not in sources:
                            sources.append(source)
                            sane_names[source] = sval
                return sources, sane_names
    return [], {}


def _parse_max_scan_area(opts: list[tuple]) -> ScanArea | None:
    max_x = max_y = None
    for opt in opts:
        name = opt[0]
        constraint = opt[7]
        if (
            name in ("br_x", "br-x")
            and isinstance(constraint, (list, tuple))
            and len(constraint) >= 2
        ):
            max_x = float(constraint[1])
        elif (
            name in ("br_y", "br-y")
            and isinstance(constraint, (list, tuple))
            and len(constraint) >= 2
        ):
            max_y = float(constraint[1])

    if max_x is not None and max_y is not None:
        return ScanArea(
            x=0, y=0, width=math.ceil(max_x * 10), height=math.ceil(max_y * 10)
        )
    return None


def _parse_resolutions(opts: list[tuple]) -> list[int]:
    """Extract supported resolutions from SANE option descriptors."""
    from .._types import normalize_resolutions

    for opt in opts:
        if opt[0] == "resolution":
            constraint = opt[7]
            if isinstance(constraint, (list, tuple)):
                if len(constraint) == 3 and isinstance(constraint[0], (int, float)):
                    lo, hi, step = (
                        int(constraint[0]),
                        int(constraint[1]),
                        int(constraint[2] or 1),
                    )
                    step = max(1, step)
                    return normalize_resolutions(list(range(lo, hi + 1, step)))
                return [int(v) for v in constraint if isinstance(v, (int, float))]
            break
    return []


def _parse_color_modes(
    opts: list[tuple],
) -> tuple[list[ColorMode], dict[ColorMode, str]]:
    """Extract supported color modes from SANE option descriptors.

    Returns ``(modes, sane_names)`` where *sane_names* maps each
    :class:`ColorMode` to the original string reported by the backend
    (preserving case).
    """
    for opt in opts:
        if opt[0] == "mode":
            constraint = opt[7]
            if isinstance(constraint, (list, tuple)):
                modes: list[ColorMode] = []
                sane_names: dict[ColorMode, str] = {}
                for val in constraint:
                    sval = str(val)
                    mapped = _SANE_MODE_TO_COLOR.get(sval.lower())
                    if mapped is not None and mapped not in modes:
                        modes.append(mapped)
                        sane_names[mapped] = sval
                return modes, sane_names
            break
    return [], {}


def _pick_default_dpi(resolutions: list[int]) -> int:
    """Pick the best default DPI from a list of supported resolutions."""
    if not resolutions:
        return 300
    if 300 in resolutions:
        return 300
    # Pick the closest to 300
    return min(resolutions, key=lambda r: abs(r - 300))


def _pick_default_color_mode(modes: list[ColorMode]) -> ColorMode:
    """Pick the best default color mode from supported modes."""
    for preferred in (ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW):
        if preferred in modes:
            return preferred
    return ColorMode.COLOR


def _read_defaults(sources: list[SourceInfo]) -> ScannerDefaults | None:
    """Synthesize sensible defaults from the device's supported options.

    SANE has no API for "recommended" defaults — some backends (e.g. eSCL)
    initialise at their minimum settings.  Instead we pick 300 dpi (or the
    closest available), Color mode if supported, and the first source.
    """
    try:
        if not sources:
            return ScannerDefaults(dpi=300, color_mode=ColorMode.COLOR, source=None)
        first = sources[0]
        return ScannerDefaults(
            dpi=_pick_default_dpi(first.resolutions),
            color_mode=_pick_default_color_mode(first.color_modes),
            source=first.type,
        )
    except Exception:
        return None


def _scan_one_page(dev: _SaneDevice, progress=None) -> ScannedPage:
    status = dev.start()
    if status != _STATUS_GOOD:
        name = _STATUS_NAMES.get(status, f"unknown ({status})")
        raise ScanError(f"sane_start: {name}")

    params = dev.get_parameters()
    width = params.pixels_per_line
    depth = params.depth

    expected = params.bytes_per_line * params.lines if params.lines > 0 else 0
    received = 0
    last_pct = 0

    chunks: list[bytes] = []
    while True:
        data, st = dev.read(65536)
        if data:
            chunks.append(data)
            if expected > 0:
                received += len(data)
                pct = min(received * 99 // expected, 99)
                if pct > last_pct:
                    check_progress(progress, pct)
                    last_pct = pct
        if st == _STATUS_EOF:
            break
        if st == _STATUS_IO_ERROR and chunks:
            # Some backends (e.g. hpaio) signal end-of-page with IO_ERROR
            # instead of EOF once all data has been delivered.
            break
        if st != _STATUS_GOOD:
            name = _STATUS_NAMES.get(st, f"unknown ({st})")
            raise ScanError(f"sane_read: {name}")

    raw = b"".join(chunks)

    if params.bytes_per_line > 0:
        height = len(raw) // params.bytes_per_line
    else:
        height = params.lines if params.lines > 0 else 0

    if height == 0 or width == 0:
        raise ScanError("Empty scan data")

    raw = raw[: height * params.bytes_per_line]

    is_rgb = params.format == _FRAME_RGB

    if is_rgb and depth == 8:
        pixel_data = trim_rows(raw, height, params.bytes_per_line, width * 3)
        mode = ColorMode.COLOR

    elif not is_rgb and depth == 8:
        pixel_data = trim_rows(raw, height, params.bytes_per_line, width)
        mode = ColorMode.GRAY

    elif not is_rgb and depth == 1:
        row_bytes = (width + 7) // 8
        pixel_data = trim_rows(raw, height, params.bytes_per_line, row_bytes)
        mode = ColorMode.BW

    else:
        raise ScanError(
            f"Unsupported SANE frame: format={params.format}, depth={depth}"
        )

    return ScannedPage(
        data=pixel_data,
        width=width,
        height=height,
        color_mode=mode,
    )


class SaneBackend:
    backend_name = "sane"
    """Linux scanning backend using SANE (via ctypes)."""

    def __init__(self) -> None:
        _init()
        self._handles: dict[str, _SaneDevice] = {}

    def list_scanners(
        self,
        timeout: float = DISCOVERY_TIMEOUT,
        cancel: threading.Event | None = None,
    ) -> list[Scanner]:
        from .._mdns import extract_ip_from_uri

        result: list | None = None
        error: BaseException | None = None
        done = threading.Event()

        def _discover():
            nonlocal result, error
            try:
                result = _get_devices()
            except BaseException as exc:
                error = exc
            finally:
                done.set()

        t = threading.Thread(target=_discover, daemon=True)
        t.start()
        if not wait_or_cancel(done, timeout, cancel):
            return []
        if error is not None:
            raise error
        # Only list local (USB) scanners — network scanners are handled
        # by the eSCL backend via the composite backend.  Skip v4l
        # devices and deduplicate by USB bus:dev identity.
        scanners: list[Scanner] = []
        seen: set[str] = set()
        for dev_info in result or []:
            if dev_info[0].startswith("v4l:"):
                continue
            # Skip network scanners (eSCL, airscan, or anything with an IP)
            if dev_info[0].startswith(("escl:", "airscan:")):
                continue
            if extract_ip_from_uri(dev_info[0]):
                continue
            dev_id = _extract_device_id(dev_info[0])
            if dev_id:
                if dev_id in seen:
                    continue
                seen.add(dev_id)
            scanners.append(
                Scanner(
                    name=dev_info[0],
                    vendor=dev_info[1] or None,
                    model=dev_info[2] or None,
                    backend=self.backend_name,
                    _backend_impl=self,
                )
            )
        return scanners

    def open_scanner(self, scanner: Scanner) -> None:
        try:
            dev = _open_device(scanner.name)
        except Exception as exc:
            raise ScanError(f"Failed to open scanner {scanner.name!r}: {exc}") from exc
        self._handles[scanner.name] = dev

        opts = _get_options(dev)
        source_types, dev._sane_source_names = _parse_sources(opts)

        # Read initial (pre-source-switch) values as fallback.
        initial_resolutions = _parse_resolutions(opts)
        initial_color_modes, dev._sane_mode_names = _parse_color_modes(opts)

        # Per-source capabilities — switching source can change constraints.
        source_infos: list[SourceInfo] = []
        for source in source_types:
            try:
                source_str = dev._sane_source_names.get(
                    source,
                    _SCAN_SOURCE_TO_SANE.get(source, source.value),
                )
                dev.set_option("source", source_str)
            except Exception:
                pass
            source_opts = _get_options(dev)

            area = _parse_max_scan_area(source_opts)

            resolutions = _parse_resolutions(source_opts)
            if not resolutions:
                resolutions = initial_resolutions

            color_modes, sane_names = _parse_color_modes(source_opts)
            if color_modes:
                dev._sane_mode_names.update(sane_names)
            else:
                color_modes = initial_color_modes

            source_infos.append(
                SourceInfo(
                    type=source,
                    resolutions=resolutions,
                    color_modes=color_modes,
                    max_scan_area=area,
                )
            )

        scanner._sources = source_infos
        scanner._defaults = _read_defaults(scanner._sources)

    def close_scanner(self, scanner: Scanner) -> None:
        dev = self._handles.pop(scanner.name, None)
        if dev is not None:
            dev.close()

    def abort_scan(self, scanner: Scanner) -> None:
        dev = self._handles.get(scanner.name)
        if dev is not None:
            dev.cancel()

    def scan_pages(
        self, scanner: Scanner, options: ScanOptions
    ) -> Iterator[ScannedPage]:
        dev = self._handles.get(scanner.name)
        if dev is None:
            raise ScanError("Scanner is not open")

        try:
            # Set source first — some SANE backends reset mode/resolution
            # when the source changes (e.g. feeder defaults to Lineart).
            if options.source is not None and dev.has_option("source"):
                sane_source_names = getattr(dev, "_sane_source_names", {})
                source_str = sane_source_names.get(
                    options.source,
                    _SCAN_SOURCE_TO_SANE.get(options.source, options.source.value),
                )
                dev.set_option("source", source_str)

            if dev.has_option("mode"):
                sane_mode_names = getattr(dev, "_sane_mode_names", {})
                mode_str = sane_mode_names.get(
                    options.color_mode,
                    _COLOR_MODE_MAP.get(options.color_mode, options.color_mode.value),
                )
                dev.set_option("mode", mode_str)
            if dev.has_option("resolution"):
                dev.set_option("resolution", options.dpi)

            if options.scan_area is not None:
                area = options.scan_area
                try:
                    if dev.has_option("tl-x"):
                        dev.set_option("tl-x", area.x / 10.0)
                    if dev.has_option("tl-y"):
                        dev.set_option("tl-y", area.y / 10.0)
                    if dev.has_option("br-x"):
                        dev.set_option("br-x", (area.x + area.width) / 10.0)
                    if dev.has_option("br-y"):
                        dev.set_option("br-y", (area.y + area.height) / 10.0)
                except ScanError:
                    pass  # scanner may use different units (e.g. pixels)

            is_feeder = options.source == ScanSource.FEEDER
            page_count = 0
            scan_started = False

            check_progress(options.progress, 0)

            while True:
                try:
                    scan_started = True
                    page = _scan_one_page(dev, progress=options.progress)
                except ScanError as exc:
                    msg = str(exc).lower()
                    if is_feeder and ("no docs" in msg or "eof" in msg):
                        break
                    if "cancel" in msg or "jammed" in msg:
                        raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                    raise

                yield page
                page_count += 1

                if not is_feeder:
                    break

            if page_count == 0:
                if is_feeder:
                    raise FeederEmptyError("No documents in feeder")
                raise ScanError("No pages were scanned")

            check_progress(options.progress, 100)
        except ScanAborted:
            raise
        except ScanError:
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
        finally:
            if scan_started:
                dev.cancel()
