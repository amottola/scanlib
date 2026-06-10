"""Tests for the _CompositeBackend that merges a platform backend with eSCL."""

from __future__ import annotations

import threading

from scanlib import _CompositeBackend, _await_thread
from scanlib._types import Scanner


class _FakePlatform:
    backend_name = "platform"

    def __init__(self, scanners):
        self._scanners = scanners

    def list_scanners(self, timeout=15.0, cancel=None):
        return list(self._scanners)


class _FakeEscl:
    backend_name = "escl"

    def __init__(self, scanners, uuids=None, ips=None):
        self._scanners = scanners
        self._uuids = uuids or {}
        self._ips = ips or {}

    def list_scanners(self, timeout=15.0, cancel=None):
        return list(self._scanners)

    def get_scanner_uuids(self):
        return dict(self._uuids)

    def get_scanner_ips(self):
        return dict(self._ips)


def _make_composite(platform, escl):
    comp = _CompositeBackend.__new__(_CompositeBackend)
    comp._platform = platform
    comp._escl = escl
    return comp


def _scanner(name, backend, scanner_id):
    return Scanner(
        name=name, vendor=None, model=None, backend=backend, scanner_id=scanner_id
    )


class TestCompositeDedup:
    def test_uuid_match_prefers_escl(self):
        # ImageCaptureCore reports the device under a (upper-case) UUID id;
        # eSCL reports the same device by UUID.  The platform entry should
        # be dropped in favour of the eSCL one.
        uuid = "E3248000-80CE-11DB-8000-B42200A172D1"
        platform = _FakePlatform([_scanner("Brother", "imagecapture", uuid)])
        escl = _FakeEscl(
            [_scanner("Brother", "escl", "escl:192.168.1.23:80")],
            uuids={"escl:192.168.1.23:80": uuid.lower()},
            ips={"escl:192.168.1.23:80": "192.168.1.23"},
        )
        comp = _make_composite(platform, escl)
        result = comp.list_scanners(timeout=1)
        assert len(result) == 1
        assert result[0].backend == "escl"

    def test_distinct_devices_both_kept(self):
        # A USB scanner only the platform sees plus a network scanner only
        # eSCL sees → both returned.
        platform = _FakePlatform([_scanner("USB Scanner", "imagecapture", "USB-UUID")])
        escl = _FakeEscl(
            [_scanner("Net Scanner", "escl", "escl:192.168.1.5:80")],
            uuids={"escl:192.168.1.5:80": "net-uuid"},
            ips={"escl:192.168.1.5:80": "192.168.1.5"},
        )
        comp = _make_composite(platform, escl)
        result = comp.list_scanners(timeout=1)
        assert {s.backend for s in result} == {"imagecapture", "escl"}
        assert len(result) == 2

    def test_ip_match_prefers_platform(self):
        # When the platform backend itself reports a network scanner whose
        # IP matches an eSCL one (no UUID overlap), the eSCL duplicate is
        # dropped and the platform entry kept.
        platform = _FakePlatform(
            [_scanner("escl:http://192.168.1.9:80/eSCL", "sane", "sane-id")]
        )
        escl = _FakeEscl(
            [_scanner("Net", "escl", "escl:192.168.1.9:80")],
            uuids={},
            ips={"escl:192.168.1.9:80": "192.168.1.9"},
        )
        comp = _make_composite(platform, escl)
        result = comp.list_scanners(timeout=1)
        assert len(result) == 1
        assert result[0].backend == "sane"

    def test_no_escl_returns_platform(self):
        platform = _FakePlatform([_scanner("USB", "sane", "sane-id")])
        escl = _FakeEscl([])
        comp = _make_composite(platform, escl)
        result = comp.list_scanners(timeout=1)
        assert len(result) == 1
        assert result[0].backend == "sane"


class TestAwaitThread:
    def test_returns_after_thread_finishes(self):
        ran = threading.Event()
        t = threading.Thread(target=ran.set)
        t.start()
        _await_thread(t, timeout=2.0)
        assert ran.is_set()
        assert not t.is_alive()

    def test_returns_when_already_finished(self):
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        # Should return promptly without raising.
        _await_thread(t, timeout=2.0)
        assert not t.is_alive()
