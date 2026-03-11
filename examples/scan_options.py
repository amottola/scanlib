"""Demonstrate various scan options: scan area, image format, quality, and progress."""

import scanlib
from scanlib import ColorMode, ImageFormat, ScanArea

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

scanner = scanners[0]

with scanner:
    print(f"Using: {scanner.display_name}")
    print(f"Sources: {[s.value for s in scanner.sources]}")
    print(f"Max scan areas: {scanner.max_scan_area}")
    if scanner.defaults:
        d = scanner.defaults
        print(f"Defaults: {d.dpi} dpi, {d.color_mode.value}, source={d.source}")

    # -- A4 grayscale with a progress callback --
    def on_progress(percent: int) -> bool:
        print(f"\r  Progress: {percent}%", end="", flush=True)
        return True  # return False to abort

    # Pick a DPI the scanner actually supports (prefer 150, fall back to default)
    dpi = 150
    if scanner.resolutions and dpi not in scanner.resolutions:
        dpi = scanner.defaults.dpi if scanner.defaults else scanner.resolutions[0]

    print(f"\nScanning A4 grayscale at {dpi} dpi...")
    doc = scanner.scan(
        dpi=dpi,
        color_mode=ColorMode.GRAY,
        scan_area=ScanArea(0, 0, 2100, 2970),  # full A4 in 1/10 mm
        progress=on_progress,
    )
    print(f"\n  {doc.width}x{doc.height} px, {len(doc.data):,} bytes")

    with open("scan_a4_gray.pdf", "wb") as f:
        f.write(doc.data)
    print("  Saved to scan_a4_gray.pdf")

    # -- Lossless PNG output --
    print("\nScanning with PNG (lossless) encoding...")
    doc = scanner.scan(image_format=ImageFormat.PNG)

    with open("scan_png.pdf", "wb") as f:
        f.write(doc.data)
    print(f"  {len(doc.data):,} bytes — saved to scan_png.pdf")

    # -- Low-quality JPEG for small file size --
    print("\nScanning with low JPEG quality (30)...")
    doc = scanner.scan(jpeg_quality=30)

    with open("scan_lowq.pdf", "wb") as f:
        f.write(doc.data)
    print(f"  {len(doc.data):,} bytes — saved to scan_lowq.pdf")
