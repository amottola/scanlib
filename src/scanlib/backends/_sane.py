"""Linux scanning backend using SANE (via ctypes, no external dependencies)."""

from __future__ import annotations

import ctypes
import ctypes.util
import math
from collections import namedtuple
from collections.abc import Iterator
from typing import Any

from _scanlib_accel import trim_rows

from .._types import (
    ColorMode,
    PageSize,
    ScanAborted,
    ScanError,
    ScannedPage,
    Scanner,
    ScannerDefaults,
    ScanOptions,
    ScanSource,
    check_progress,
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
        _SANE_Handle, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
    ]
    _lib.sane_control_option.restype = ctypes.c_int

    _lib.sane_start.argtypes = [_SANE_Handle]
    _lib.sane_start.restype = ctypes.c_int

    _lib.sane_get_parameters.argtypes = [_SANE_Handle, ctypes.POINTER(_SANE_Parameters)]
    _lib.sane_get_parameters.restype = ctypes.c_int

    _lib.sane_read.argtypes = [
        _SANE_Handle, ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int, ctypes.POINTER(ctypes.c_int),
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
        result.append((
            dev.name.decode("utf-8", errors="replace") if dev.name else "",
            dev.vendor.decode("utf-8", errors="replace") if dev.vendor else "",
            dev.model.decode("utf-8", errors="replace") if dev.model else "",
            dev.type.decode("utf-8", errors="replace") if dev.type else "",
        ))
        i += 1
    return result


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
            description = desc.desc.decode("utf-8", errors="replace") if desc.desc else ""
            constraint = self._read_constraint(desc)
            result.append((
                name, title, description,
                desc.type, desc.unit, desc.size, desc.cap,
                constraint,
            ))
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
                self._handle, option_num, _ACTION_SET_VALUE,
                buf, ctypes.byref(info),
            )
        elif desc.type == _TYPE_INT:
            val = ctypes.c_int(int(value))
            status = _lib.sane_control_option(
                self._handle, option_num, _ACTION_SET_VALUE,
                ctypes.byref(val), ctypes.byref(info),
            )
        elif desc.type == _TYPE_FIXED:
            val = ctypes.c_int(_float_to_fixed(float(value)))
            status = _lib.sane_control_option(
                self._handle, option_num, _ACTION_SET_VALUE,
                ctypes.byref(val), ctypes.byref(info),
            )
        elif desc.type == _TYPE_BOOL:
            val = ctypes.c_int(1 if value else 0)
            status = _lib.sane_control_option(
                self._handle, option_num, _ACTION_SET_VALUE,
                ctypes.byref(val), ctypes.byref(info),
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
                self._handle, option_num, _ACTION_GET_VALUE,
                buf, ctypes.byref(info),
            )
            _check_status(status, f"get_option({name!r})")
            return buf.value.decode("utf-8", errors="replace")
        elif desc.type in (_TYPE_INT, _TYPE_BOOL):
            val = ctypes.c_int()
            status = _lib.sane_control_option(
                self._handle, option_num, _ACTION_GET_VALUE,
                ctypes.byref(val), ctypes.byref(info),
            )
            _check_status(status, f"get_option({name!r})")
            return val.value
        elif desc.type == _TYPE_FIXED:
            val = ctypes.c_int()
            status = _lib.sane_control_option(
                self._handle, option_num, _ACTION_GET_VALUE,
                ctypes.byref(val), ctypes.byref(info),
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


def _parse_sources(opts: list[tuple]) -> list[ScanSource]:
    for opt in opts:
        if opt[0] == "source":
            constraint = opt[7]
            if isinstance(constraint, (list, tuple)):
                sources: list[ScanSource] = []
                for value in constraint:
                    key = str(value).lower()
                    for pattern, source in _SANE_SOURCE_MAP.items():
                        if pattern in key and source not in sources:
                            sources.append(source)
                return sources
    return []


def _parse_max_page_size(opts: list[tuple]) -> PageSize | None:
    max_x = max_y = None
    for opt in opts:
        name = opt[0]
        constraint = opt[7]
        if name in ("br_x", "br-x") and isinstance(constraint, (list, tuple)) and len(constraint) >= 2:
            max_x = float(constraint[1])
        elif name in ("br_y", "br-y") and isinstance(constraint, (list, tuple)) and len(constraint) >= 2:
            max_y = float(constraint[1])

    if max_x is not None and max_y is not None:
        return PageSize(width=math.ceil(max_x * 10), height=math.ceil(max_y * 10))
    return None


def _parse_resolutions(opts: list[tuple]) -> list[int]:
    """Extract supported resolutions from SANE option descriptors."""
    for opt in opts:
        if opt[0] == "resolution":
            constraint = opt[7]
            if isinstance(constraint, (list, tuple)):
                if len(constraint) == 3 and isinstance(constraint[0], (int, float)):
                    lo, hi, step = int(constraint[0]), int(constraint[1]), int(constraint[2] or 1)
                    step = max(1, step)
                    if (hi - lo) // step <= 1000:
                        return list(range(lo, hi + 1, step))
                    return list(range(lo, hi + 1, (hi - lo) // 20))
                return [int(v) for v in constraint if isinstance(v, (int, float))]
            break
    return []


def _parse_color_modes(opts: list[tuple]) -> list[ColorMode]:
    """Extract supported color modes from SANE option descriptors."""
    for opt in opts:
        if opt[0] == "mode":
            constraint = opt[7]
            if isinstance(constraint, (list, tuple)):
                modes: list[ColorMode] = []
                for val in constraint:
                    mapped = _SANE_MODE_TO_COLOR.get(str(val).lower())
                    if mapped is not None and mapped not in modes:
                        modes.append(mapped)
                return modes
            break
    return []


def _read_defaults(dev: _SaneDevice, opts: list[tuple], sources: list[ScanSource]) -> ScannerDefaults | None:
    """Read default settings from SANE device options."""
    try:
        try:
            dpi = int(dev.get_option("resolution"))
        except Exception:
            dpi = 300

        try:
            mode_str = str(dev.get_option("mode")).lower()
            color_mode = _SANE_MODE_TO_COLOR.get(mode_str, ColorMode.COLOR)
        except Exception:
            color_mode = ColorMode.COLOR

        source: ScanSource | None = None
        try:
            source_str = str(dev.get_option("source")).lower()
            for pattern, src in _SANE_SOURCE_MAP.items():
                if pattern in source_str:
                    source = src
                    break
        except Exception:
            source = sources[0] if sources else None

        return ScannerDefaults(
            dpi=dpi,
            color_mode=color_mode,
            source=source,
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
        color_type = 2
        bit_depth = 8

    elif not is_rgb and depth == 8:
        pixel_data = trim_rows(raw, height, params.bytes_per_line, width)
        color_type = 0
        bit_depth = 8

    elif not is_rgb and depth == 1:
        row_bytes = (width + 7) // 8
        pixel_data = trim_rows(raw, height, params.bytes_per_line, row_bytes)
        color_type = 0
        bit_depth = 1

    else:
        raise ScanError(
            f"Unsupported SANE frame: format={params.format}, depth={depth}"
        )

    return ScannedPage(
        data=pixel_data, width=width, height=height,
        color_type=color_type, bit_depth=bit_depth,
    )


class SaneBackend:
    """Linux scanning backend using SANE (via ctypes)."""

    def __init__(self) -> None:
        _init()
        self._handles: dict[str, _SaneDevice] = {}

    def list_scanners(self) -> list[Scanner]:
        devices = _get_devices()
        return [
            Scanner(
                name=dev_info[0],
                vendor=dev_info[1] or None,
                model=dev_info[2] or None,
                backend="sane",
                _backend_impl=self,
            )
            for dev_info in devices
        ]

    def open_scanner(self, scanner: Scanner) -> None:
        try:
            dev = _open_device(scanner.name)
        except Exception as exc:
            raise ScanError(
                f"Failed to open scanner {scanner.name!r}: {exc}"
            ) from exc
        self._handles[scanner.name] = dev

        opts = _get_options(dev)
        scanner._sources = _parse_sources(opts)

        for source in scanner._sources:
            try:
                dev.set_option("source", _SCAN_SOURCE_TO_SANE.get(source, source.value))
            except Exception:
                pass
            source_opts = _get_options(dev)
            ps = _parse_max_page_size(source_opts)
            if ps is not None:
                scanner._max_page_sizes[source] = ps

        scanner._resolutions = _parse_resolutions(opts)
        scanner._color_modes = _parse_color_modes(opts)
        scanner._defaults = _read_defaults(dev, opts, scanner._sources)

    def close_scanner(self, scanner: Scanner) -> None:
        dev = self._handles.pop(scanner.name, None)
        if dev is not None:
            dev.close()

    def scan_pages(self, scanner: Scanner, options: ScanOptions) -> Iterator[ScannedPage]:
        dev = self._handles.get(scanner.name)
        if dev is None:
            raise ScanError("Scanner is not open")

        try:
            dev.set_option("mode", _COLOR_MODE_MAP.get(options.color_mode, options.color_mode.value))
            dev.set_option("resolution", options.dpi)

            if options.source is not None:
                dev.set_option(
                    "source",
                    _SCAN_SOURCE_TO_SANE.get(options.source, options.source.value),
                )

            if options.page_size is not None:
                dev.set_option("br-x", options.page_size.width / 10.0)
                dev.set_option("br-y", options.page_size.height / 10.0)

            check_progress(options.progress, 0)

            is_feeder = options.source == ScanSource.FEEDER
            page_count = 0

            while True:
                try:
                    page = _scan_one_page(dev, progress=options.progress)
                except ScanError as exc:
                    msg = str(exc).lower()
                    if is_feeder and page_count > 0 and (
                        "no docs" in msg or "eof" in msg
                    ):
                        break
                    if "cancel" in msg or "jammed" in msg:
                        raise ScanAborted(f"Scan cancelled by device: {exc}") from exc
                    raise

                yield page
                page_count += 1

                if not is_feeder:
                    if options.next_page is not None and options.next_page(page_count):
                        continue
                    break

            if page_count == 0:
                raise ScanError("No pages were scanned")

            check_progress(options.progress, 100)
        except ScanAborted:
            dev.cancel()
            raise
        except ScanError:
            raise
        except Exception as exc:
            raise ScanError(f"Scan failed: {exc}") from exc
