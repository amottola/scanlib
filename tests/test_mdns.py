from scanlib._mdns import (
    LocationMap,
    _BrowseResult,
    _best_address,
    _build_query,
    _encode_name,
    _is_routable_address,
    _parse_responses,
    _parse_txt,
    _read_name,
    _resolvable_instances,
    _resolve_srv_addresses,
    _SrvInfo,
    extract_ip_from_uri,
)


class TestExtractIpFromUri:
    def test_escl_http(self):
        uri = "escl:http://192.168.1.5:443/eSCL"
        assert extract_ip_from_uri(uri) == "192.168.1.5"

    def test_escl_https(self):
        uri = "escl:https://10.0.0.42:8443/eSCL"
        assert extract_ip_from_uri(uri) == "10.0.0.42"

    def test_airscan(self):
        uri = "airscan:e0:Canon MF240 http://192.168.1.10:8080/eSCL"
        assert extract_ip_from_uri(uri) == "192.168.1.10"

    def test_ip_query_param(self):
        uri = "backend:/net/SomeModel?ip=192.168.1.20"
        assert extract_ip_from_uri(uri) == "192.168.1.20"

    def test_ip_query_param_with_extra(self):
        uri = "backend:/net/SomeModel?ip=192.168.1.20&port=1234"
        assert extract_ip_from_uri(uri) == "192.168.1.20"

    def test_usb_returns_none(self):
        uri = "pixma:04A9176D_3EF123"
        assert extract_ip_from_uri(uri) is None

    def test_libusb_returns_none(self):
        uri = "epson2:libusb:001:004"
        assert extract_ip_from_uri(uri) is None

    def test_empty_returns_none(self):
        assert extract_ip_from_uri("") is None

    def test_hostname(self):
        uri = "escl:http://myprinter.local:443/eSCL"
        assert extract_ip_from_uri(uri) == "myprinter.local"


class TestLocationMap:
    def test_empty_is_falsy(self):
        m = LocationMap()
        assert not m

    def test_with_ip_is_truthy(self):
        m = LocationMap(by_ip={"192.168.1.5": "Office"})
        assert m

    def test_with_name_is_truthy(self):
        m = LocationMap(by_name={"Canon MF240": "Office"})
        assert m


class TestEncodeName:
    def test_simple(self):
        encoded = _encode_name("_uscan._tcp.local.")
        # Each label: length byte + content, terminated by \x00
        assert encoded == (b"\x06_uscan\x04_tcp\x05local\x00")

    def test_trailing_dot(self):
        assert _encode_name("foo.bar.") == _encode_name("foo.bar")


class TestReadName:
    def test_simple(self):
        data = b"\x03foo\x03bar\x00"
        name, offset = _read_name(data, 0)
        assert name == "foo.bar."
        assert offset == len(data)

    def test_pointer(self):
        # Name at offset 0: "foo.bar."
        # Name at offset 9: pointer to offset 0
        data = b"\x03foo\x03bar\x00\xc0\x00"
        name, offset = _read_name(data, 9)
        assert name == "foo.bar."
        assert offset == 11


class TestParseTxt:
    def test_key_value_pairs(self):
        # TXT RDATA: each string is length-prefixed
        s1 = b"note=2nd Floor"
        s2 = b"ty=Canon MF240"
        rdata = bytes([len(s1)]) + s1 + bytes([len(s2)]) + s2
        result = _parse_txt(rdata, 0, len(rdata))
        assert result == {"note": "2nd Floor", "ty": "Canon MF240"}

    def test_empty_value(self):
        s = b"note="
        rdata = bytes([len(s)]) + s
        result = _parse_txt(rdata, 0, len(rdata))
        assert result == {"note": ""}

    def test_no_equals(self):
        # Entries without '=' are skipped
        s = b"flagonly"
        rdata = bytes([len(s)]) + s
        result = _parse_txt(rdata, 0, len(rdata))
        assert result == {}


class TestBuildQuery:
    def test_builds_valid_dns_packet(self):
        query = _build_query("_uscan._tcp.local.")
        # Header: 12 bytes, QDCOUNT=1
        assert len(query) > 12
        import struct

        _id, _flags, qdcount, ancount, nscount, arcount = struct.unpack(
            ">HHHHHH", query[:12]
        )
        assert qdcount == 1
        assert ancount == 0

    def test_multiple_questions(self):
        query = _build_query("_uscan._tcp.local.", "_uscans._tcp.local.")
        import struct

        qdcount = struct.unpack(">H", query[4:6])[0]
        assert qdcount == 2


class TestParseResponses:
    def _build_response(self, answers):
        """Build a minimal DNS response with the given answer records."""
        import socket
        import struct

        header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(answers), 0, 0)
        body = b""
        for name, rtype, rdata in answers:
            encoded_name = _encode_name(name)
            body += encoded_name
            body += struct.pack(">HHIH", rtype, 1, 300, len(rdata))
            body += rdata
        return header + body

    def test_ptr_record(self):
        target_name = "Canon._uscan._tcp.local."
        rdata = _encode_name(target_name)
        data = self._build_response([("_uscan._tcp.local.", 12, rdata)])
        ptrs, txts, addrs, srvs = _parse_responses(data)
        assert ("_uscan._tcp.local.", target_name) in ptrs

    def test_ptr_record_ignores_unrelated_service_types(self):
        # We receive unsolicited mDNS announcements for other services on
        # the LAN (e.g. screen sharing on _rfb._tcp from iPhones/Macs);
        # those PTRs must not be treated as scanners.
        scanner = _encode_name("Canon._uscan._tcp.local.")
        vnc = _encode_name("Mac Mini._rfb._tcp.local.")
        data = self._build_response(
            [
                ("_uscan._tcp.local.", 12, scanner),
                ("_rfb._tcp.local.", 12, vnc),
            ]
        )
        ptrs, txts, addrs, srvs = _parse_responses(data)
        types = {svc for svc, _inst in ptrs}
        assert types == {"_uscan._tcp.local."}

    def test_txt_record(self):
        s1 = b"note=Office"
        s2 = b"ty=Canon MF240"
        rdata = bytes([len(s1)]) + s1 + bytes([len(s2)]) + s2
        data = self._build_response([("Canon._uscan._tcp.local.", 16, rdata)])
        ptrs, txts, addrs, srvs = _parse_responses(data)
        assert "Canon._uscan._tcp.local." in txts
        assert txts["Canon._uscan._tcp.local."]["note"] == "Office"
        assert txts["Canon._uscan._tcp.local."]["ty"] == "Canon MF240"

    def test_a_record(self):
        import socket

        rdata = socket.inet_aton("192.168.1.5")
        data = self._build_response([("printer.local.", 1, rdata)])
        ptrs, txts, addrs, srvs = _parse_responses(data)
        assert "192.168.1.5" in addrs.get("printer.local.", [])

    def test_empty_packet(self):
        ptrs, txts, addrs, srvs = _parse_responses(b"")
        assert ptrs == []
        assert txts == {}
        assert addrs == {}

    def test_srv_record_port(self):
        import struct

        # SRV RDATA: priority(2) + weight(2) + port(2) + target name
        target_name = _encode_name("printer.local.")
        rdata = struct.pack(">HHH", 0, 0, 8443) + target_name
        data = self._build_response([("Canon._uscan._tcp.local.", 33, rdata)])
        ptrs, txts, addrs, srvs = _parse_responses(data)
        assert "Canon._uscan._tcp.local." in srvs
        assert srvs["Canon._uscan._tcp.local."].port == 8443
        assert srvs["Canon._uscan._tcp.local."].target == "printer.local."

    def test_truncated_packet(self):
        ptrs, txts, addrs, srvs = _parse_responses(b"\x00" * 6)
        assert ptrs == []


class TestSrvAddressResolution:
    def test_links_srv_target_addresses_to_instance(self):
        # A/AAAA records arrive under the host name; SRV maps the service
        # instance to that host.  Resolution should copy the addresses onto
        # the instance name (even across packets).
        inst = "Brother._uscan._tcp.local."
        host = "BRN123.local."
        result = _BrowseResult()
        result.ptrs.add(("_uscan._tcp.local.", inst))
        result.srvs[inst] = _SrvInfo(target=host, port=80)
        result.addrs[host] = ["192.168.1.23"]

        assert _resolvable_instances(result) == []  # not yet linked
        _resolve_srv_addresses(result)
        assert result.addrs[inst] == ["192.168.1.23"]
        assert _resolvable_instances(result) == [inst]

    def test_direct_instance_address_not_overwritten(self):
        inst = "Dev._uscan._tcp.local."
        result = _BrowseResult()
        result.srvs[inst] = _SrvInfo(target="host.local.", port=80)
        result.addrs[inst] = ["10.0.0.1"]
        result.addrs["host.local."] = ["10.0.0.2"]
        _resolve_srv_addresses(result)
        assert result.addrs[inst] == ["10.0.0.1"]

    def test_resolvable_instances_requires_ptr_and_address(self):
        result = _BrowseResult()
        result.ptrs.add(("_uscan._tcp.local.", "A._uscan._tcp.local."))
        # PTR present but no address yet → not resolvable.
        assert _resolvable_instances(result) == []
        result.addrs["A._uscan._tcp.local."] = ["1.2.3.4"]
        assert _resolvable_instances(result) == ["A._uscan._tcp.local."]


class TestRoutableAddress:
    def test_ipv4_routable(self):
        assert _is_routable_address("192.168.1.23") is True

    def test_ipv4_link_local(self):
        assert _is_routable_address("169.254.1.2") is False

    def test_ipv6_link_local(self):
        assert _is_routable_address("fe80::3:4e83:3a7b:1fd3") is False

    def test_ipv6_global(self):
        assert _is_routable_address("2001:db8::1") is True


class TestBestAddress:
    def test_prefers_ipv4_over_ipv6(self):
        assert _best_address(["fe80::1", "2001:db8::1", "192.168.1.5"]) == "192.168.1.5"

    def test_link_local_only_returns_none(self):
        assert _best_address(["fe80::3:4e83:3a7b:1fd3"]) is None

    def test_global_ipv6_when_no_ipv4(self):
        assert _best_address(["2001:db8::1"]) == "2001:db8::1"

    def test_empty(self):
        assert _best_address([]) is None


class TestDiscoverSkipsLinkLocal:
    def test_link_local_only_instance_skipped(self, monkeypatch):
        from scanlib import _mdns

        res = _BrowseResult()
        good = "Brother MFC-L3750CDW series._uscan._tcp.local."
        # A phantom service that resolves only to a link-local address.
        bad = "20AB4F1B-0E9A-4567-B812-AADD01DFB6B5._uscans._tcp.local."
        res.ptrs.add(("_uscan._tcp.local.", good))
        res.ptrs.add(("_uscans._tcp.local.", bad))
        res.addrs[good] = ["192.168.1.23", "fe80::b622:ff:fea1:72d1"]
        res.addrs[bad] = ["fe80::3:4e83:3a7b:1fd3"]
        res.srvs[good] = _SrvInfo(target="BRN.local.", port=80)
        res.srvs[bad] = _SrvInfo(target="phantom.local.", port=53168)
        res.txts[good] = {
            "ty": "Brother MFC-L3750CDW series",
            "UUID": "e3248000",
            "rs": "eSCL",
        }
        res.txts[bad] = {}

        monkeypatch.setattr(_mdns, "_browse_mdns", lambda timeout=4.0: res)
        svcs = _mdns.discover_escl_services(timeout=1)

        assert len(svcs) == 1
        assert svcs[0].ip == "192.168.1.23"  # IPv4 chosen; link-local-only skipped
        assert svcs[0].name == "Brother MFC-L3750CDW series"
