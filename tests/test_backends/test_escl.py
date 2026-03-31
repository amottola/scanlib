"""Tests for the eSCL backend (XML parsing, unit conversion, settings generation)."""

import xml.etree.ElementTree as ET

import pytest

from scanlib._types import ColorMode, ScanArea, ScanOptions, ScanSource, SourceInfo
from scanlib.backends._escl import (
    _build_scan_settings,
    _decode_scan_response,
    _escl_to_tenths_mm,
    _parse_capabilities,
    _tenths_mm_to_escl,
)

# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------


class TestUnitConversion:
    def test_tenths_mm_to_escl_a4_width(self):
        # A4 width = 2100 tenths of mm → ~2480 in 1/300 inch
        result = _tenths_mm_to_escl(2100)
        assert result == 2480

    def test_tenths_mm_to_escl_a4_height(self):
        result = _tenths_mm_to_escl(2970)
        assert result == 3508

    def test_escl_to_tenths_mm_roundtrip(self):
        original = 2100
        escl = _tenths_mm_to_escl(original)
        back = _escl_to_tenths_mm(escl)
        assert abs(back - original) <= 1

    def test_zero(self):
        assert _tenths_mm_to_escl(0) == 0
        assert _escl_to_tenths_mm(0) == 0


# ---------------------------------------------------------------------------
# Capabilities XML parsing
# ---------------------------------------------------------------------------

_CAPABILITIES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities
    xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <scan:Platen>
    <scan:DiscreteResolutions>
      <scan:DiscreteResolution>
        <scan:XResolution>75</scan:XResolution>
        <scan:YResolution>75</scan:YResolution>
      </scan:DiscreteResolution>
      <scan:DiscreteResolution>
        <scan:XResolution>300</scan:XResolution>
        <scan:YResolution>300</scan:YResolution>
      </scan:DiscreteResolution>
      <scan:DiscreteResolution>
        <scan:XResolution>600</scan:XResolution>
        <scan:YResolution>600</scan:YResolution>
      </scan:DiscreteResolution>
    </scan:DiscreteResolutions>
    <scan:ColorModes>
      <scan:ColorMode>BlackAndWhite1</scan:ColorMode>
      <scan:ColorMode>Grayscale8</scan:ColorMode>
      <scan:ColorMode>RGB24</scan:ColorMode>
    </scan:ColorModes>
    <scan:MaxWidth>2480</scan:MaxWidth>
    <scan:MaxHeight>3508</scan:MaxHeight>
  </scan:Platen>
  <scan:Adf>
    <scan:DiscreteResolutions>
      <scan:DiscreteResolution>
        <scan:XResolution>300</scan:XResolution>
        <scan:YResolution>300</scan:YResolution>
      </scan:DiscreteResolution>
    </scan:DiscreteResolutions>
    <scan:ColorModes>
      <scan:ColorMode>Grayscale8</scan:ColorMode>
      <scan:ColorMode>RGB24</scan:ColorMode>
    </scan:ColorModes>
    <scan:MaxWidth>2480</scan:MaxWidth>
    <scan:MaxHeight>3508</scan:MaxHeight>
  </scan:Adf>
</scan:ScannerCapabilities>
"""


class TestParseCapabilities:
    def test_parses_sources(self):
        root = ET.fromstring(_CAPABILITIES_XML)
        sources, defaults, source_names = _parse_capabilities(root)
        types = [s.type for s in sources]
        assert ScanSource.FLATBED in types
        assert ScanSource.FEEDER in types

    def test_flatbed_resolutions(self):
        root = ET.fromstring(_CAPABILITIES_XML)
        sources, _, _ = _parse_capabilities(root)
        flatbed = next(s for s in sources if s.type == ScanSource.FLATBED)
        assert flatbed.resolutions == [75, 300, 600]

    def test_flatbed_color_modes(self):
        root = ET.fromstring(_CAPABILITIES_XML)
        sources, _, _ = _parse_capabilities(root)
        flatbed = next(s for s in sources if s.type == ScanSource.FLATBED)
        assert ColorMode.BW in flatbed.color_modes
        assert ColorMode.GRAY in flatbed.color_modes
        assert ColorMode.COLOR in flatbed.color_modes

    def test_max_scan_area_converted(self):
        root = ET.fromstring(_CAPABILITIES_XML)
        sources, _, _ = _parse_capabilities(root)
        flatbed = next(s for s in sources if s.type == ScanSource.FLATBED)
        area = flatbed.max_scan_area
        assert area is not None
        # 2480 escl units → ~2100 tenths mm
        assert abs(area.width - 2100) <= 1
        assert abs(area.height - 2970) <= 1

    def test_defaults(self):
        root = ET.fromstring(_CAPABILITIES_XML)
        _, defaults, _ = _parse_capabilities(root)
        assert defaults is not None
        assert defaults.dpi == 300
        assert defaults.color_mode == ColorMode.COLOR
        assert defaults.source == ScanSource.FLATBED

    def test_source_names(self):
        root = ET.fromstring(_CAPABILITIES_XML)
        _, _, source_names = _parse_capabilities(root)
        assert source_names[ScanSource.FLATBED] == "Platen"
        assert source_names[ScanSource.FEEDER] == "Adf"


class TestParseCapabilitiesMinimal:
    """Test with a minimal capabilities XML (no recognized sources)."""

    _MINIMAL = """\
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities
    xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
</scan:ScannerCapabilities>
"""

    def test_fallback_flatbed(self):
        root = ET.fromstring(self._MINIMAL)
        sources, defaults, source_names = _parse_capabilities(root)
        assert len(sources) == 1
        assert sources[0].type == ScanSource.FLATBED
        assert sources[0].resolutions == [300]


class TestParseCapabilitiesResolutionRange:
    """Test resolution range parsing and normalization."""

    _RANGE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities
    xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
  <scan:Platen>
    <scan:XResolutionRange>
      <scan:Min>75</scan:Min>
      <scan:Max>1200</scan:Max>
      <scan:Step>1</scan:Step>
    </scan:XResolutionRange>
    <scan:ColorModes>
      <scan:ColorMode>RGB24</scan:ColorMode>
    </scan:ColorModes>
    <scan:MaxWidth>2480</scan:MaxWidth>
    <scan:MaxHeight>3508</scan:MaxHeight>
  </scan:Platen>
</scan:ScannerCapabilities>
"""

    def test_range_normalized(self):
        root = ET.fromstring(self._RANGE_XML)
        sources, _, _ = _parse_capabilities(root)
        flatbed = sources[0]
        # Should be normalized to standard DPIs, not 75..1200 step 1
        assert len(flatbed.resolutions) < 50
        assert 300 in flatbed.resolutions
        assert 600 in flatbed.resolutions


# ---------------------------------------------------------------------------
# Scan settings XML
# ---------------------------------------------------------------------------


class TestBuildScanSettings:
    def test_basic_settings(self):
        options = ScanOptions(dpi=300, color_mode=ColorMode.COLOR)
        xml = _build_scan_settings(options, "Platen", ScanArea(0, 0, 2100, 2970))
        root = ET.fromstring(xml)
        ns = "http://schemas.hp.com/imaging/escl/2011/05/03"
        assert root.find(f"{{{ns}}}XResolution").text == "300"
        assert root.find(f"{{{ns}}}ColorMode").text == "RGB24"
        assert root.find(f"{{{ns}}}InputSource").text == "Platen"

    def test_scan_area_conversion(self):
        area = ScanArea(0, 0, 2100, 2970)
        options = ScanOptions(dpi=300, color_mode=ColorMode.COLOR, scan_area=area)
        xml = _build_scan_settings(options, "Platen", None)
        root = ET.fromstring(xml)
        ns = "http://schemas.hp.com/imaging/escl/2011/05/03"
        input_el = root.find(f"{{{ns}}}InputSize")
        assert input_el is not None
        w = int(input_el.find(f"{{{ns}}}Width").text)
        h = int(input_el.find(f"{{{ns}}}Height").text)
        assert w == _tenths_mm_to_escl(2100)
        assert h == _tenths_mm_to_escl(2970)

    def test_scan_area_with_offset(self):
        area = ScanArea(100, 200, 1000, 1500)
        options = ScanOptions(dpi=300, color_mode=ColorMode.GRAY, scan_area=area)
        xml = _build_scan_settings(options, "Platen", None)
        root = ET.fromstring(xml)
        ns = "http://schemas.hp.com/imaging/escl/2011/05/03"
        xoff = root.find(f"{{{ns}}}XOffset")
        yoff = root.find(f"{{{ns}}}YOffset")
        assert xoff is not None
        assert int(xoff.text) == _tenths_mm_to_escl(100)
        assert int(yoff.text) == _tenths_mm_to_escl(200)

    def test_bw_mode(self):
        options = ScanOptions(dpi=300, color_mode=ColorMode.BW)
        xml = _build_scan_settings(options, "Platen", None)
        root = ET.fromstring(xml)
        ns = "http://schemas.hp.com/imaging/escl/2011/05/03"
        assert root.find(f"{{{ns}}}ColorMode").text == "BlackAndWhite1"

    def test_document_format_is_jpeg(self):
        options = ScanOptions(dpi=300, color_mode=ColorMode.COLOR)
        xml = _build_scan_settings(options, "Platen", None)
        root = ET.fromstring(xml)
        ns = "http://schemas.hp.com/imaging/escl/2011/05/03"
        fmt = root.find(f"{{{ns}}}DocumentFormatExt")
        assert fmt is not None
        assert fmt.text == "image/jpeg"
