"""Basic scanning example — discover a scanner and scan a single page."""

import scanlib

# Discover available scanners
scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

scanner = scanners[0]
print(f"Using: {scanner}")

# Open a session and scan with default settings (300 dpi, color, JPEG).
# Most scanners allow only one scan session at a time, so handle the case
# where another application (or host) is already using the device.
try:
    with scanner:
        doc = scanner.scan()
except scanlib.ScannerBusyError:
    print("Scanner is busy — close any other scanning app and try again.")
    raise SystemExit(1)

print(
    f"Scanned {doc.page_count} page, {doc.width}x{doc.height} px, {len(doc.data)} bytes"
)

with open("scan.pdf", "wb") as f:
    f.write(doc.data)

print("Saved to scan.pdf")
