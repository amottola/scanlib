import pytest

from scanlib._types import (
    ColorMode,
    ImageFormat,
    ScanArea,
    ScanAborted,
    ScanError,
    ScanLibError,
    Scanner,
    ScannerDefaults,
    ScannerNotOpenError,
    ScanOptions,
    ScanSource,
    ScannedDocument,
    ScannedPage,
    SourceInfo,
    build_pdf,
    normalize_resolutions,
)


class TestColorMode:
    def test_values(self):
        assert ColorMode.COLOR.value == "color"
        assert ColorMode.GRAY.value == "gray"
        assert ColorMode.BW.value == "bw"


class TestScanSource:
    def test_values(self):
        assert ScanSource.FLATBED.value == "flatbed"
        assert ScanSource.FEEDER.value == "feeder"


class TestScanArea:
    def test_creation(self):
        area = ScanArea(x=0, y=0, width=2100, height=2970)
        assert area.x == 0
        assert area.y == 0
        assert area.width == 2100
        assert area.height == 2970

    def test_with_offsets(self):
        area = ScanArea(x=100, y=200, width=1000, height=1500)
        assert area.x == 100
        assert area.y == 200
        assert area.width == 1000
        assert area.height == 1500


class TestScanner:
    def test_creation(self):
        s = Scanner(name="test", vendor="Acme", model="X100", backend="sane")
        assert s.name == "test"
        assert s.vendor == "Acme"
        assert s.model == "X100"
        assert s.backend == "sane"
        assert s.is_open is False

    def test_str_with_location(self):
        s = Scanner(
            name="epson:usb:001",
            vendor="Epson",
            model="GT-S50",
            backend="sane",
            location="2nd Floor",
        )
        assert str(s) == "2nd Floor"

    def test_str_vendor_and_model(self):
        s = Scanner(
            name="epson:usb:001", vendor="Epson", model="GT-S50", backend="sane"
        )
        assert str(s) == "Epson GT-S50"

    def test_str_vendor_only(self):
        s = Scanner(
            name="Canon ImageRUNNER", vendor="Canon", model=None, backend="imagecapture"
        )
        assert str(s) == "Canon"

    def test_str_model_only(self):
        s = Scanner(name="test:dev", vendor=None, model="GT-S50", backend="sane")
        assert str(s) == "GT-S50"

    def test_str_fallback_to_name(self):
        s = Scanner(name="HP Officejet", vendor=None, model=None, backend="wia")
        assert str(s) == "HP Officejet"

    def test_optional_fields(self):
        s = Scanner(name="test", vendor=None, model=None, backend="wia")
        assert s.vendor is None
        assert s.model is None

    def test_id_defaults_to_name(self):
        s = Scanner(
            name="escl:http://192.168.1.5/eSCL", vendor=None, model=None, backend="sane"
        )
        assert s.id == "escl:http://192.168.1.5/eSCL"

    def test_id_explicit(self):
        s = Scanner(
            name="HP Officejet",
            vendor=None,
            model=None,
            backend="wia",
            scanner_id="{6BDD1FC6-810F-11D0-BEC7-08002BE2092F}\\0001",
        )
        assert s.id == "{6BDD1FC6-810F-11D0-BEC7-08002BE2092F}\\0001"
        assert s.name == "HP Officejet"

    def test_location_default_none(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        assert s.location is None

    def test_location_set(self):
        s = Scanner(
            name="test", vendor=None, model=None, backend="sane", location="Office"
        )
        assert s.location == "Office"

    def test_sources_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            _ = s.sources

    def test_scan_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            s.scan()

    def test_defaults_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            _ = s.defaults

    def test_defaults_none_by_default(self):
        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            assert s.defaults is None

    def test_defaults_populated(self):
        defaults = ScannerDefaults(
            dpi=300,
            color_mode=ColorMode.COLOR,
            source=ScanSource.FLATBED,
        )

        def open_scanner(self, s):
            s._defaults = defaults

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": open_scanner,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            assert s.defaults is defaults
            assert s.defaults.dpi == 300

    def test_sources_default_empty(self):
        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            assert s.sources == []

    def test_sources_populated(self):
        def open_scanner(self, s):
            s._sources = [
                SourceInfo(
                    type=ScanSource.FLATBED,
                    resolutions=[150, 300, 600],
                    color_modes=[ColorMode.COLOR, ColorMode.GRAY],
                    max_scan_area=ScanArea(0, 0, 2100, 2970),
                )
            ]

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": open_scanner,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            assert len(s.sources) == 1
            si = s.sources[0]
            assert si.type == ScanSource.FLATBED
            assert si.resolutions == [150, 300, 600]
            assert si.color_modes == [ColorMode.COLOR, ColorMode.GRAY]
            assert si.max_scan_area == ScanArea(0, 0, 2100, 2970)

    def test_context_manager(self):
        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s as opened:
            assert opened is s
            assert s.is_open is True
        assert s.is_open is False

    def test_repr(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        assert "closed" in repr(s)


class TestNormalizeResolutions:
    def test_short_list_unchanged(self):
        """Lists with ≤30 entries are returned as-is."""
        dpis = [75, 150, 300, 600, 1200]
        assert normalize_resolutions(dpis) == dpis

    def test_range_expansion_normalized(self):
        """A range like 75–1200 step 1 is snapped to standard DPIs."""
        huge = list(range(75, 1201))
        result = normalize_resolutions(huge)
        assert 75 in result
        assert 300 in result
        assert 600 in result
        assert 1200 in result
        assert len(result) < 30

    def test_no_intermediate_values(self):
        """Non-standard DPIs like 76, 301, etc. are excluded."""
        huge = list(range(75, 1201))
        result = normalize_resolutions(huge)
        assert 76 not in result
        assert 301 not in result

    def test_narrow_range(self):
        """A narrow range still returns the standard DPIs within it."""
        dpis = list(range(200, 401))
        result = normalize_resolutions(dpis)
        assert result == [200, 240, 300, 400]

    def test_empty_list(self):
        assert normalize_resolutions([]) == []

    def test_single_value(self):
        assert normalize_resolutions([300]) == [300]

    def test_thirty_entries_unchanged(self):
        """Exactly 30 entries — treated as discrete, not normalized."""
        dpis = list(range(100, 130))
        assert normalize_resolutions(dpis) == dpis


class TestScanOptions:
    def test_defaults(self):
        opts = ScanOptions()
        assert opts.dpi == 300
        assert opts.color_mode == ColorMode.COLOR
        assert opts.scan_area is None
        assert opts.source is None
        assert opts.progress is None

    def test_custom(self):
        opts = ScanOptions(
            dpi=600,
            color_mode=ColorMode.GRAY,
            scan_area=ScanArea(0, 0, 2100, 2970),
            source=ScanSource.FEEDER,
        )
        assert opts.dpi == 600
        assert opts.color_mode == ColorMode.GRAY
        assert opts.scan_area == ScanArea(0, 0, 2100, 2970)
        assert opts.source == ScanSource.FEEDER


class TestScannedDocument:
    def test_creation(self):
        doc = ScannedDocument(
            data=b"%PDF-1.4",
            page_count=1,
            width=100,
            height=200,
            dpi=300,
            color_mode=ColorMode.COLOR,
        )
        assert doc.data == b"%PDF-1.4"
        assert doc.page_count == 1
        assert doc.width == 100
        assert doc.height == 200
        assert doc.dpi == 300
        assert doc.color_mode == ColorMode.COLOR


class TestScanAborted:
    def test_is_scanlib_error(self):
        assert issubclass(ScanAborted, ScanLibError)


class TestScannerNotOpenError:
    def test_is_scanlib_error(self):
        assert issubclass(ScannerNotOpenError, ScanLibError)


def _make_page(width=16, height=16, color_mode=ColorMode.COLOR):
    """Create a ScannedPage with deterministic pixel data."""
    if color_mode == ColorMode.BW:
        # 1-bit packed data (MSB first)
        row_bytes = (width + 7) // 8
        buf = bytearray(row_bytes * height)
        for y in range(height):
            for x in range(width):
                # Checkerboard pattern
                if (x + y) % 2 == 0:
                    buf[y * row_bytes + x // 8] |= 0x80 >> (x & 7)
        data = bytes(buf)
    elif color_mode == ColorMode.COLOR:
        channels = 3
        data = bytes(
            [
                (y * 37 + x * 13) & 0xFF
                for y in range(height)
                for x in range(width * channels)
            ]
        )
    else:
        channels = 1
        data = bytes(
            [
                (y * 37 + x * 13) & 0xFF
                for y in range(height)
                for x in range(width * channels)
            ]
        )
    return ScannedPage(
        data=data,
        width=width,
        height=height,
        color_mode=color_mode,
    )


class TestScannedPage:
    def test_color_mode_rgb(self):
        page = _make_page(color_mode=ColorMode.COLOR)
        assert page.color_mode == ColorMode.COLOR

    def test_color_mode_gray(self):
        page = _make_page(color_mode=ColorMode.GRAY)
        assert page.color_mode == ColorMode.GRAY

    def test_to_jpeg_rgb(self):
        page = _make_page(color_mode=ColorMode.COLOR)
        jpg = page.to_jpeg()
        assert jpg[:2] == b"\xff\xd8"  # SOI marker
        assert jpg[-2:] == b"\xff\xd9"  # EOI marker

    def test_to_jpeg_gray(self):
        page = _make_page(color_mode=ColorMode.GRAY)
        jpg = page.to_jpeg(quality=50)
        assert jpg[:2] == b"\xff\xd8"

    def test_to_png_rgb(self):
        page = _make_page(color_mode=ColorMode.COLOR)
        png = page.to_png()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_to_png_gray(self):
        page = _make_page(color_mode=ColorMode.GRAY)
        png = page.to_png()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_to_png_roundtrip(self):
        """PNG output can be decoded (validate structure)."""
        import struct, zlib

        page = _make_page(width=4, height=4, color_mode=ColorMode.COLOR)
        png = page.to_png()
        # Parse IHDR
        ihdr_len = struct.unpack(">I", png[8:12])[0]
        assert png[12:16] == b"IHDR"
        w, h, bd, ct = struct.unpack(">IIBBBBB", png[16 : 16 + ihdr_len])[:4]
        assert w == 4
        assert h == 4
        assert bd == 8
        assert ct == 2


class TestRotate:
    def _rgb_page(self, width, height):
        """Create an RGB page with unique pixel values per position."""
        data = bytearray()
        for y in range(height):
            for x in range(width):
                data.extend([x & 0xFF, y & 0xFF, (x + y) & 0xFF])
        return ScannedPage(
            data=bytes(data),
            width=width,
            height=height,
            color_mode=ColorMode.COLOR,
        )

    def _gray_page(self, width, height):
        """Create a grayscale page with unique pixel values per position."""
        data = bytes(
            (y * width + x) & 0xFF for y in range(height) for x in range(width)
        )
        return ScannedPage(
            data=data,
            width=width,
            height=height,
            color_mode=ColorMode.GRAY,
        )

    def _get_rgb_pixel(self, page, x, y):
        off = (y * page.width + x) * 3
        return (page.data[off], page.data[off + 1], page.data[off + 2])

    def _get_gray_pixel(self, page, x, y):
        return page.data[y * page.width + x]

    def test_rotate_90_rgb(self):
        # 3x2 image: pixel (x,y) -> (h-1-y, x) in new (2x3) image
        page = self._rgb_page(3, 2)
        r = page.rotate(90)
        assert r.width == 2
        assert r.height == 3
        # Original (0,0) -> rotated (1, 0)
        assert self._get_rgb_pixel(r, 1, 0) == self._get_rgb_pixel(page, 0, 0)
        # Original (2,0) -> rotated (1, 2)
        assert self._get_rgb_pixel(r, 1, 2) == self._get_rgb_pixel(page, 2, 0)
        # Original (0,1) -> rotated (0, 0)
        assert self._get_rgb_pixel(r, 0, 0) == self._get_rgb_pixel(page, 0, 1)

    def test_rotate_180_rgb(self):
        page = self._rgb_page(3, 2)
        r = page.rotate(180)
        assert r.width == 3
        assert r.height == 2
        # (0,0) -> (2, 1)
        assert self._get_rgb_pixel(r, 2, 1) == self._get_rgb_pixel(page, 0, 0)
        # (2,1) -> (0, 0)
        assert self._get_rgb_pixel(r, 0, 0) == self._get_rgb_pixel(page, 2, 1)

    def test_rotate_270_rgb(self):
        page = self._rgb_page(3, 2)
        r = page.rotate(270)
        assert r.width == 2
        assert r.height == 3
        # Original (0,0) -> rotated (0, 2)
        assert self._get_rgb_pixel(r, 0, 2) == self._get_rgb_pixel(page, 0, 0)
        # Original (2,1) -> rotated (1, 0)
        assert self._get_rgb_pixel(r, 1, 0) == self._get_rgb_pixel(page, 2, 1)

    def test_rotate_90_gray(self):
        page = self._gray_page(4, 3)
        r = page.rotate(90)
        assert r.width == 3
        assert r.height == 4
        # (0,0) -> (2, 0)
        assert self._get_gray_pixel(r, 2, 0) == self._get_gray_pixel(page, 0, 0)
        # (3,2) -> (0, 3)
        assert self._get_gray_pixel(r, 0, 3) == self._get_gray_pixel(page, 3, 2)

    def test_rotate_180_gray(self):
        page = self._gray_page(4, 3)
        r = page.rotate(180)
        assert r.width == 4
        assert r.height == 3
        assert self._get_gray_pixel(r, 3, 2) == self._get_gray_pixel(page, 0, 0)
        assert self._get_gray_pixel(r, 0, 0) == self._get_gray_pixel(page, 3, 2)

    def test_rotate_invalid_degrees(self):
        page = self._gray_page(4, 4)
        with pytest.raises(ValueError):
            page.rotate(45)
        with pytest.raises(ValueError):
            page.rotate(0)
        with pytest.raises(ValueError):
            page.rotate(360)

    def test_rotate_dimensions_swap(self):
        page = self._gray_page(5, 3)
        assert page.rotate(90).width == 3
        assert page.rotate(90).height == 5
        assert page.rotate(180).width == 5
        assert page.rotate(180).height == 3
        assert page.rotate(270).width == 3
        assert page.rotate(270).height == 5

    def test_rotate_preserves_color_mode(self):
        rgb = self._rgb_page(4, 4).rotate(90)
        assert rgb.color_mode == ColorMode.COLOR
        gray = self._gray_page(4, 4).rotate(90)
        assert gray.color_mode == ColorMode.GRAY

    def test_rotate_roundtrip(self):
        page = self._rgb_page(5, 3)
        r = page.rotate(90).rotate(90).rotate(90).rotate(90)
        assert r.width == page.width
        assert r.height == page.height
        assert r.data == page.data


class TestScanPages:
    def test_scan_pages_raises_when_not_open(self):
        s = Scanner(name="test", vendor=None, model=None, backend="sane")
        with pytest.raises(ScannerNotOpenError):
            s.scan_pages()

    def _open_scanner(self, **caps):
        """Create a mock-opened Scanner with given capabilities."""
        source_types = caps.get("sources", [ScanSource.FLATBED])
        raw_res = caps.get("resolutions", [100, 200, 300])
        raw_modes = caps.get("color_modes", [ColorMode.COLOR, ColorMode.GRAY])
        max_scan_areas = caps.pop("max_scan_areas", None)

        def open_scanner(be, s):
            source_infos = []
            for src in source_types:
                area = max_scan_areas.get(src) if max_scan_areas is not None else None
                source_infos.append(
                    SourceInfo(
                        type=src,
                        resolutions=raw_res,
                        color_modes=raw_modes,
                        max_scan_area=area,
                    )
                )
            s._sources = source_infos

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": open_scanner,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
                "scan_pages": lambda self, s, o: iter([_make_page()]),
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        s.open()
        return s

    def test_scan_pages_yields_pages(self):
        pages = [_make_page(), _make_page()]

        def scan_pages(self, scanner, options):
            return iter(pages)

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
                "scan_pages": scan_pages,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            result = list(s.scan_pages())
        assert len(result) == 2
        assert all(isinstance(p, ScannedPage) for p in result)

    def test_scan_rejects_unsupported_dpi(self):
        s = self._open_scanner(resolutions=[100, 200, 300])
        with pytest.raises(ValueError, match="Unsupported DPI"):
            s.scan_pages(dpi=150)

    def test_scan_rejects_unsupported_color_mode(self):
        s = self._open_scanner(color_modes=[ColorMode.COLOR])
        with pytest.raises(ValueError, match="Unsupported color mode"):
            s.scan_pages(color_mode=ColorMode.BW)

    def test_scan_rejects_unsupported_source(self):
        s = self._open_scanner(sources=[ScanSource.FLATBED])
        with pytest.raises(ValueError, match="Unsupported source"):
            s.scan_pages(source=ScanSource.FEEDER)

    def test_scan_rejects_scan_area_beyond_max(self):
        s = self._open_scanner(
            resolutions=[300],
            color_modes=[ColorMode.COLOR],
            max_scan_areas={ScanSource.FLATBED: ScanArea(0, 0, 2100, 2970)},
        )
        with pytest.raises(ValueError, match="scan_area extends beyond"):
            s.scan_pages(scan_area=ScanArea(0, 0, 9999, 9999))

    def test_scan_area_within_max_passes(self):
        s = self._open_scanner(
            resolutions=[300],
            color_modes=[ColorMode.COLOR],
            max_scan_areas={ScanSource.FLATBED: ScanArea(0, 0, 2100, 2970)},
        )
        pages = list(s.scan_pages(scan_area=ScanArea(100, 200, 1000, 1500)))
        assert len(pages) == 1

    def test_backend_exception_propagates_to_caller(self):
        """Exceptions from the backend reach the caller's thread."""

        def scan_pages(self, scanner, options):
            raise ScanError("device error")

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
                "scan_pages": scan_pages,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            with pytest.raises(ScanError, match="device error"):
                list(s.scan_pages())

    def test_backend_exception_after_yielding_propagates(self):
        """Exception after yielding one page still reaches the caller."""

        def scan_pages(self, scanner, options):
            yield _make_page()
            raise ScanError("mid-scan error")

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
                "scan_pages": scan_pages,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            gen = s.scan_pages()
            page = next(gen)
            assert isinstance(page, ScannedPage)
            with pytest.raises(ScanError, match="mid-scan error"):
                next(gen)

    def test_exception_during_next_page_round_propagates(self):
        """Exception on a next_page continuation reaches the caller."""
        call_count = [0]

        def scan_pages(self, scanner, options):
            call_count[0] += 1
            if call_count[0] > 1:
                raise ScanError("second round failed")
            yield _make_page()

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
                "scan_pages": scan_pages,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            with pytest.raises(ScanError, match="second round failed"):
                list(s.scan_pages(next_page=lambda n: True))

    def test_abort_stops_scan(self):
        """scanner.abort() causes scan_pages to raise ScanAborted."""
        import threading

        def scan_pages(self, scanner, options):
            # Simulate a slow scan that blocks until abort
            scanner._abort_event.wait(timeout=5.0)
            yield _make_page()

        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
                "scan_pages": scan_pages,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            # Abort from another thread after a short delay
            threading.Timer(0.1, s.abort).start()
            with pytest.raises(ScanAborted):
                list(s.scan_pages())

    def test_abort_safe_when_not_scanning(self):
        """abort() does not raise when no scan is in progress."""
        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            s.abort()  # should not raise

    def test_abort_does_not_affect_next_scan(self):
        """A stale abort() does not interfere with the next scan."""
        mock_backend = type(
            "B",
            (),
            {
                "open_scanner": lambda self, s: None,
                "close_scanner": lambda self, s: None,
                "abort_scan": lambda self, s: None,
                "scan_pages": lambda self, s, o: iter([_make_page()]),
            },
        )()
        s = Scanner(
            name="test",
            vendor=None,
            model=None,
            backend="sane",
            _backend_impl=mock_backend,
        )
        with s:
            s.abort()
            # Next scan should work fine
            pages = list(s.scan_pages())
            assert len(pages) == 1


class TestBuildPdf:
    def test_basic(self):
        pages = [_make_page(), _make_page()]
        doc = build_pdf(pages, dpi=300)
        assert doc.data[:8] == b"%PDF-1.4"
        assert doc.page_count == 2

    def test_reordered_pages(self):
        page_small = _make_page(width=8, height=8)
        page_large = _make_page(width=32, height=32)
        doc = build_pdf([page_large, page_small], dpi=300)
        assert doc.page_count == 2
        assert doc.width == 32  # first page dimensions
        assert doc.height == 32

    def test_png_format(self):
        pages = [_make_page()]
        doc = build_pdf(pages, image_format=ImageFormat.PNG)
        assert b"/FlateDecode" in doc.data

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No pages"):
            build_pdf([])

    def test_bw_page_jpeg_format(self):
        """BW page encoded as JPEG in PDF (must unpack to 8-bit)."""
        page = _make_page(width=16, height=16, color_mode=ColorMode.BW)
        doc = build_pdf([page], color_mode=ColorMode.BW, image_format=ImageFormat.JPEG)
        assert doc.data[:8] == b"%PDF-1.4"
        assert b"/DCTDecode" in doc.data

    def test_bw_page_png_format(self):
        """BW page encoded as PNG in PDF (stays 1-bit)."""
        page = _make_page(width=16, height=16, color_mode=ColorMode.BW)
        doc = build_pdf([page], color_mode=ColorMode.BW, image_format=ImageFormat.PNG)
        assert doc.data[:8] == b"%PDF-1.4"
        assert b"/FlateDecode" in doc.data
        assert b"/BitsPerComponent 1" in doc.data

    def test_bw_page_as_gray(self):
        """BW page converted to grayscale in PDF (must unpack to 8-bit)."""
        page = _make_page(width=16, height=16, color_mode=ColorMode.BW)
        doc = build_pdf([page], color_mode=ColorMode.GRAY)
        assert doc.data[:8] == b"%PDF-1.4"
        assert b"/BitsPerComponent 8" in doc.data


class TestBwToGray:
    def test_roundtrip(self):
        """gray_to_bw then bw_to_gray preserves values (0 or 255)."""
        from _scanlib_accel import bw_to_gray, gray_to_bw

        width, height = 10, 5
        # Start with gray pixels: alternating 0 and 255
        gray = bytes(
            [
                255 if (x + y) % 2 == 0 else 0
                for y in range(height)
                for x in range(width)
            ]
        )
        packed = gray_to_bw(gray, width, height)
        unpacked = bw_to_gray(packed, width, height)
        assert unpacked == gray

    def test_output_size(self):
        from _scanlib_accel import bw_to_gray

        width, height = 13, 7  # non-multiple-of-8 width
        row_bytes = (width + 7) // 8
        packed = bytes(row_bytes * height)
        gray = bw_to_gray(packed, width, height)
        assert len(gray) == width * height

    def test_bit_values(self):
        """Each set bit becomes 255, each unset bit becomes 0."""
        from _scanlib_accel import bw_to_gray

        # 1 byte = 8 pixels: bits 10110001
        packed = bytes([0b10110001])
        gray = bw_to_gray(packed, 8, 1)
        assert gray == bytes([255, 0, 255, 255, 0, 0, 0, 255])

    def test_to_jpeg_bw_page(self):
        """to_jpeg() works on 1-bit BW pages."""
        page = _make_page(width=16, height=16, color_mode=ColorMode.BW)
        jpg = page.to_jpeg()
        assert jpg[:2] == b"\xff\xd8"
        assert jpg[-2:] == b"\xff\xd9"

    def test_to_png_bw_page(self):
        """to_png() works on 1-bit BW pages."""
        import struct

        page = _make_page(width=16, height=16, color_mode=ColorMode.BW)
        png = page.to_png()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        # Parse IHDR to verify bit_depth=1
        ihdr_len = struct.unpack(">I", png[8:12])[0]
        w, h, bd, ct = struct.unpack(">IIBBBBB", png[16 : 16 + ihdr_len])[:4]
        assert bd == 1
        assert ct == 0
