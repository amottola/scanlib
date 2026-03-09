"""Tests that run against real scanner hardware.

All tests in this module are skipped automatically when no physical
scanner is detected.  Run with ``pytest -m hardware`` to target them.
"""

from __future__ import annotations

import pytest

import scanlib
from scanlib._types import ColorMode, PageSize, Scanner, ScanSource, ScannedDocument  # noqa: F401


def _get_scanners() -> list[Scanner]:
    try:
        return scanlib.list_scanners()
    except Exception:
        return []


_scanners = _get_scanners()

requires_scanner = pytest.mark.skipif(
    not _scanners, reason="No physical scanner available"
)

pytestmark = pytest.mark.hardware


@requires_scanner
class TestListScannersHardware:
    @pytest.mark.timeout(30)
    def test_returns_at_least_one(self):
        assert len(_scanners) >= 1

    def test_scanner_fields(self):
        for s in _scanners:
            assert isinstance(s, Scanner)
            assert isinstance(s.name, str) and s.name
            assert isinstance(s.backend, str) and s.backend

    @pytest.mark.timeout(30)
    def test_consistent_results(self):
        """Calling list_scanners twice returns the same scanner names."""
        names_cached = {s.name for s in _scanners}
        names_fresh = {s.name for s in scanlib.list_scanners()}
        assert names_cached == names_fresh


@requires_scanner
class TestOpenScanner:
    @pytest.mark.timeout(30)
    def test_open_populates_sources(self):
        with _scanners[0] as scanner:
            assert isinstance(scanner.sources, list)
            for source in scanner.sources:
                assert isinstance(source, ScanSource)

    @pytest.mark.timeout(30)
    def test_open_populates_max_page_sizes(self):
        with _scanners[0] as scanner:
            sizes = scanner.max_page_sizes
            assert isinstance(sizes, dict)
            for source, ps in sizes.items():
                assert isinstance(source, ScanSource)
                assert isinstance(ps, PageSize)
                assert ps.width > 0
                assert ps.height > 0


@requires_scanner
class TestScanHardware:
    @pytest.mark.timeout(120)
    def test_scan_default(self):
        with _scanners[0] as scanner:
            doc = scanner.scan()
            assert isinstance(doc, ScannedDocument)
            assert doc.data[:8] == b"%PDF-1.4"
            assert doc.page_count >= 1
            assert doc.width > 0
            assert doc.height > 0
            assert doc.dpi == 300
            assert doc.color_mode == ColorMode.COLOR
            assert isinstance(doc.scanner, Scanner)

    @pytest.mark.timeout(120)
    def test_scan_grayscale(self):
        with _scanners[0] as scanner:
            if ColorMode.GRAY not in scanner.color_modes:
                pytest.skip("scanner does not support grayscale")
            try:
                doc = scanner.scan(color_mode=ColorMode.GRAY)
            except scanlib.ScanError as exc:
                if "invalid argument" in str(exc).lower():
                    pytest.skip("scanner driver does not support grayscale scanning")
                raise
            assert doc.data[:8] == b"%PDF-1.4"
            assert doc.color_mode == ColorMode.GRAY

    @pytest.mark.timeout(120)
    def test_scan_custom_dpi(self):
        with _scanners[0] as scanner:
            doc = scanner.scan(dpi=150)
            assert doc.dpi == 150

    @pytest.mark.timeout(120)
    def test_scan_with_page_size(self):
        with _scanners[0] as scanner:
            doc = scanner.scan(page_size=PageSize(2100, 2970))
            assert doc.data[:8] == b"%PDF-1.4"

    @pytest.mark.timeout(120)
    def test_scan_progress_reports(self):
        percentages = []
        with _scanners[0] as scanner:
            doc = scanner.scan(progress=lambda pct: (percentages.append(pct) or True))
            assert isinstance(doc, ScannedDocument)
        assert 0 in percentages
        assert 100 in percentages

    @pytest.mark.timeout(120)
    def test_scan_progress_abort(self):
        with _scanners[0] as scanner:
            with pytest.raises(scanlib.ScanAborted):
                scanner.scan(progress=lambda pct: False)
