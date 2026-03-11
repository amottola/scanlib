"""Scan the same page in every supported color mode."""

import scanlib
from scanlib import ColorMode

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

scanner = scanners[0]

with scanner:
    print(f"Using: {scanner.display_name}")
    print(f"Supported color modes: {[m.value for m in scanner.color_modes]}")

    for mode in (ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW):
        if mode not in scanner.color_modes:
            print(f"\n{mode.value} — not supported, skipping")
            continue

        doc = scanner.scan(color_mode=mode)
        filename = f"scan_{mode.value}.pdf"
        with open(filename, "wb") as f:
            f.write(doc.data)

        print(f"\n{mode.value}:")
        print(f"  {doc.width}x{doc.height} px")
        print(f"  {len(doc.data):,} bytes")
        print(f"  Saved to {filename}")
