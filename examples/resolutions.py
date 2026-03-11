"""Scan the same page at multiple resolutions and compare file sizes."""

import scanlib

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

scanner = scanners[0]

with scanner:
    print(f"Using: {scanner.display_name}")
    print(f"Supported resolutions: {scanner.resolutions}")

    for dpi in (75, 150, 300, 600):
        if scanner.resolutions and dpi not in scanner.resolutions:
            print(f"\n{dpi} dpi — not supported, skipping")
            continue

        doc = scanner.scan(dpi=dpi)
        filename = f"scan_{dpi}dpi.pdf"
        with open(filename, "wb") as f:
            f.write(doc.data)

        print(f"\n{dpi} dpi:")
        print(f"  {doc.width}x{doc.height} px")
        print(f"  {len(doc.data):,} bytes")
        print(f"  Saved to {filename}")
