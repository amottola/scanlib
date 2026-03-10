"""List all available scanners and their capabilities."""

import scanlib

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

print(f"Found {len(scanners)} scanner(s):\n")

for scanner in scanners:
    print(f"  {scanner.name}")
    if scanner.vendor:
        print(f"    Vendor: {scanner.vendor}")
    if scanner.model:
        print(f"    Model:  {scanner.model}")

    with scanner:
        if scanner.sources:
            print(f"    Sources:      {[s.value for s in scanner.sources]}")
        if scanner.resolutions:
            print(f"    Resolutions:  {scanner.resolutions} dpi")
        if scanner.color_modes:
            print(f"    Color modes:  {[m.value for m in scanner.color_modes]}")
        if scanner.max_scan_area:
            for src, area in scanner.max_scan_area.items():
                w_mm, h_mm = area.width / 10, area.height / 10
                print(f"    Max area ({src.value}): {w_mm:.0f} x {h_mm:.0f} mm")
        if scanner.defaults:
            d = scanner.defaults
            src = d.source.value if d.source else "none"
            print(f"    Defaults:     {d.dpi} dpi, {d.color_mode.value}, source={src}")

    print()
