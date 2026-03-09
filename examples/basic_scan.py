"""Basic scanning example — discover a scanner and scan a single page."""

import scanlib

# Discover available scanners
scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

scanner = scanners[0]
print(f"Using: {scanner.name}")

# Open a session and scan with default settings (300 dpi, color, JPEG)
with scanner:
    doc = scanner.scan()

print(f"Scanned {doc.page_count} page, {doc.width}x{doc.height} px, {len(doc.data)} bytes")

with open("scan.pdf", "wb") as f:
    f.write(doc.data)

print("Saved to scan.pdf")
