"""Scan the same page at multiple resolutions and compare file sizes."""

import scanlib

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

scanner = scanners[0]

with scanner:
    print(f"Using: {scanner}")

    # Use the first source's resolution list for compatibility checks.
    first = scanner.sources[0] if scanner.sources else None
    supported_dpi = first.resolutions if first else []
    print(f"Supported resolutions: {supported_dpi}")

    for dpi in (75, 150, 300, 600):
        if supported_dpi and dpi not in supported_dpi:
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
