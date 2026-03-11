"""List all available scanners and their capabilities."""

import scanlib

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

print(f"Found {len(scanners)} scanner(s):\n")

for scanner in scanners:
    print(f"  {scanner.display_name}")
    print(f"    Name:   {scanner.name}")
    if scanner.vendor:
        print(f"    Vendor: {scanner.vendor}")
    if scanner.model:
        print(f"    Model:  {scanner.model}")

    with scanner:
        for si in scanner.sources:
            print(f"    Source: {si.type.value}")
            print(f"      Resolutions:  {si.resolutions} dpi")
            print(f"      Color modes:  {[m.value for m in si.color_modes]}")
            if si.max_scan_area:
                w_mm = si.max_scan_area.width / 10
                h_mm = si.max_scan_area.height / 10
                print(f"      Max area:     {w_mm:.0f} x {h_mm:.0f} mm")
        if scanner.defaults:
            d = scanner.defaults
            src = d.source.value if d.source else "none"
            print(f"    Defaults:     {d.dpi} dpi, {d.color_mode.value}, source={src}")

    print()
