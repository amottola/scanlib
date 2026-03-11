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

    # Use the first source's color mode list for compatibility checks.
    first = scanner.sources[0] if scanner.sources else None
    supported_modes = first.color_modes if first else []
    print(f"Supported color modes: {[m.value for m in supported_modes]}")

    for mode in (ColorMode.COLOR, ColorMode.GRAY, ColorMode.BW):
        if supported_modes and mode not in supported_modes:
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
