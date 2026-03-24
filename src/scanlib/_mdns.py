"""mDNS scanner location lookup.

Browses ``_uscan._tcp`` and ``_uscans._tcp`` services via multicast DNS
to collect the ``note`` TXT record (a free-form location string) for
network scanners.  Uses only the standard library (``socket``, ``struct``).
"""

from __future__ import annotations

import dataclasses
import select
import socket
import struct
import threading
import time
from urllib.parse import urlparse

# mDNS constants
_MDNS_ADDR = "224.0.0.251"
_MDNS_PORT = 5353

# DNS record types
_TYPE_A = 1
_TYPE_PTR = 12
_TYPE_TXT = 16
_TYPE_AAAA = 28
_TYPE_SRV = 33

# Service types to query
_SERVICE_TYPES = (
    "_uscan._tcp.local.",
    "_uscans._tcp.local.",
)


@dataclasses.dataclass
class LocationMap:
    """mDNS location lookup results, keyed by IP and by device name."""

    by_ip: dict[str, str] = dataclasses.field(default_factory=dict)
    by_name: dict[str, str] = dataclasses.field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.by_ip) or bool(self.by_name)


# ---------------------------------------------------------------------------
# Minimal DNS wire-format helpers
# ---------------------------------------------------------------------------


def _encode_name(name: str) -> bytes:
    """Encode a DNS name into wire format (sequence of labels)."""
    parts = []
    for label in name.rstrip(".").split("."):
        encoded = label.encode("utf-8")
        parts.append(bytes([len(encoded)]) + encoded)
    parts.append(b"\x00")
    return b"".join(parts)


def _build_query(*names: str) -> bytes:
    """Build a DNS query packet for PTR records."""
    # Header: ID=0, flags=0 (standard query), QDCOUNT=len(names)
    header = struct.pack(">HHHHHH", 0, 0, len(names), 0, 0, 0)
    questions = b""
    for name in names:
        questions += _encode_name(name) + struct.pack(">HH", _TYPE_PTR, 1)
    return header + questions


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Read a DNS name from *data* starting at *offset*.

    Handles label compression (pointer bytes).  Returns ``(name, new_offset)``.
    """
    labels: list[str] = []
    jumped = False
    end_offset = offset
    seen: set[int] = set()
    while True:
        if offset >= len(data):
            break
        length = data[offset]
        if (length & 0xC0) == 0xC0:
            # Pointer
            if not jumped:
                end_offset = offset + 2
                jumped = True
            ptr = ((length & 0x3F) << 8) | data[offset + 1]
            if ptr in seen:
                break  # avoid infinite loops
            seen.add(ptr)
            offset = ptr
        elif length == 0:
            if not jumped:
                end_offset = offset + 1
            break
        else:
            offset += 1
            labels.append(data[offset : offset + length].decode("utf-8", "replace"))
            offset += length
    return ".".join(labels) + ".", end_offset


def _parse_txt(data: bytes, rdstart: int, rdlength: int) -> dict[str, str]:
    """Parse a DNS TXT RDATA section into a key→value dict."""
    result: dict[str, str] = {}
    pos = rdstart
    end = rdstart + rdlength
    while pos < end:
        slen = data[pos]
        pos += 1
        s = data[pos : pos + slen]
        pos += slen
        if b"=" in s:
            key, _, val = s.partition(b"=")
            result[key.decode("utf-8", "replace")] = val.decode("utf-8", "replace")
    return result


def _parse_responses(
    data: bytes,
) -> tuple[list[str], dict[str, dict[str, str]], dict[str, list[str]]]:
    """Parse a DNS response packet.

    Returns ``(ptr_targets, txt_records, a_records)`` where:
    - *ptr_targets* is a list of PTR target names
    - *txt_records* maps owner name → TXT key/value dict
    - *a_records* maps owner name → list of IP address strings
    """
    if len(data) < 12:
        return [], {}, {}

    _id, _flags, qdcount, ancount, nscount, arcount = struct.unpack(
        ">HHHHHH", data[:12]
    )
    offset = 12

    # Skip questions
    for _ in range(qdcount):
        _, offset = _read_name(data, offset)
        offset += 4  # QTYPE + QCLASS

    ptrs: list[str] = []
    txts: dict[str, dict[str, str]] = {}
    addrs: dict[str, list[str]] = {}
    srv_targets: dict[str, str] = {}  # service name → SRV target hostname

    total = ancount + nscount + arcount
    for _ in range(total):
        if offset >= len(data):
            break
        name, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _, _ttl, rdlength = struct.unpack(
            ">HHIH", data[offset : offset + 10]
        )
        offset += 10
        rdstart = offset
        offset += rdlength

        if rtype == _TYPE_PTR:
            target, _ = _read_name(data, rdstart)
            ptrs.append(target)
        elif rtype == _TYPE_TXT:
            txts[name] = _parse_txt(data, rdstart, rdlength)
        elif rtype == _TYPE_A and rdlength == 4:
            ip = socket.inet_ntoa(data[rdstart : rdstart + 4])
            addrs.setdefault(name, []).append(ip)
        elif rtype == _TYPE_AAAA and rdlength == 16:
            ip = socket.inet_ntop(
                socket.AF_INET6, data[rdstart : rdstart + 16]
            )
            addrs.setdefault(name, []).append(ip)
        elif rtype == _TYPE_SRV and rdlength > 6:
            srv_target, _ = _read_name(data, rdstart + 6)
            srv_targets[name] = srv_target

    # Resolve SRV target addresses: if a service name has no direct A
    # records but its SRV target does, copy them over.
    # (mDNS often puts A records under the hostname, not the service name.)
    for sname, srv_target in srv_targets.items():
        if sname not in addrs and srv_target in addrs:
            addrs[sname] = addrs[srv_target]

    return ptrs, txts, addrs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_location_map(timeout: float = 4.0) -> LocationMap:
    """Browse mDNS for scanner services and read ``note`` TXT records.

    Returns a :class:`LocationMap` with IP → note and device-name → note
    mappings.  Returns an empty ``LocationMap`` on any network error.

    *timeout* controls how long (in seconds) to listen for mDNS responses.
    """
    result = LocationMap()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    except OSError:
        return result

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", _MDNS_PORT))

        # Join multicast group
        mreq = struct.pack(
            "4s4s",
            socket.inet_aton(_MDNS_ADDR),
            socket.inet_aton("0.0.0.0"),
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)

        # Send PTR query
        query = _build_query(*_SERVICE_TYPES)
        sock.sendto(query, (_MDNS_ADDR, _MDNS_PORT))

        # Collect all service instance names, TXT records, and addresses
        all_ptrs: set[str] = set()
        all_txts: dict[str, dict[str, str]] = {}
        all_addrs: dict[str, list[str]] = {}

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([sock], [], [], remaining)
            if not readable:
                continue
            try:
                data, _addr = sock.recvfrom(4096)
            except OSError:
                continue
            ptrs, txts, addrs = _parse_responses(data)
            all_ptrs.update(ptrs)
            all_txts.update(txts)
            for k, v in addrs.items():
                all_addrs.setdefault(k, []).extend(v)

        # Match PTR targets to their TXT and A records
        for instance_name in all_ptrs:
            txt = all_txts.get(instance_name, {})
            note = txt.get("note", "").strip()
            if not note:
                continue

            # Collect IPs for this service instance
            ips = all_addrs.get(instance_name, [])
            for ip in ips:
                result.by_ip[ip] = note

            # Use the ``ty`` TXT record as the device-name key
            ty = txt.get("ty", "").strip()
            if ty:
                result.by_name[ty] = note

    except OSError:
        pass
    finally:
        sock.close()

    return result


_CACHE_TTL = 60.0  # seconds
_cache_lock = threading.Lock()
_cached_result: LocationMap | None = None
_cached_at: float = 0.0


def browse_in_thread(timeout: float) -> tuple[threading.Thread, list[LocationMap]]:
    """Start an mDNS browse in a daemon thread, with caching.

    Returns ``(thread, box)`` where *box* is a single-element list that
    will contain the :class:`LocationMap` once the thread completes.
    The caller should ``thread.join(timeout=...)`` then read ``box[0]``.

    Results are cached for 60 seconds.  Within the TTL the thread
    completes immediately and *box* contains the cached result.
    """
    global _cached_result, _cached_at

    with _cache_lock:
        if _cached_result is not None and (time.monotonic() - _cached_at) < _CACHE_TTL:
            # Return a no-op thread that's already done
            box: list[LocationMap] = [_cached_result]
            t = threading.Thread(target=lambda: None, daemon=True)
            t.start()
            return t, box

    box = [LocationMap()]

    def _browse() -> None:
        global _cached_result, _cached_at
        try:
            result = get_location_map(timeout=min(timeout, 4.0))
        except Exception:
            return
        box[0] = result
        with _cache_lock:
            _cached_result = result
            _cached_at = time.monotonic()

    t = threading.Thread(target=_browse, daemon=True)
    t.start()
    return t, box


def extract_ip_from_uri(uri: str) -> str | None:
    """Extract an IP address or hostname from a SANE device URI.

    Handles patterns like:
    - ``escl:http://192.168.1.5:443/eSCL``
    - ``airscan:e0:Scanner Name http://192.168.1.5:8080/eSCL``
    - ``hpaio:/net/MODEL?ip=192.168.1.5``

    Returns ``None`` for USB or otherwise non-network URIs.
    """
    # hpaio:/net/MODEL?ip=ADDR
    if uri.startswith("hpaio:") and "ip=" in uri:
        idx = uri.index("ip=") + 3
        addr = uri[idx:].split("&")[0].split("/")[0]
        return addr or None

    # escl:http://... or airscan:... http://...
    # Find the http(s):// portion
    for prefix in ("http://", "https://"):
        idx = uri.find(prefix)
        if idx != -1:
            try:
                parsed = urlparse(uri[idx:])
                host = parsed.hostname
                if host:
                    return host
            except Exception:
                pass

    return None
