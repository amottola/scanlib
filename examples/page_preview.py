"""Page-level scanning — preview pages and build a PDF after reordering."""

import scanlib

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

with scanners[0] as scanner:
    print(f"Using: {scanner.name}")

    # Scan pages individually
    pages = list(scanner.scan_pages())
    print(f"Scanned {len(pages)} page(s)")

    # Save a JPEG preview of each page
    for i, page in enumerate(pages):
        preview_path = f"preview_{i}.jpg"
        with open(preview_path, "wb") as f:
            f.write(page.to_jpeg(quality=70))
        print(f"  Page {i}: {page.width}x{page.height}, {page.color_mode.value} — {preview_path}")

# Pages can be reordered, filtered, duplicated, etc.
# pages.reverse()
# pages = [pages[1], pages[0]]
# pages = [p for p in pages if p.width > 100]

# Build the final PDF
doc = scanlib.build_pdf(pages, dpi=300)
with open("scan_preview.pdf", "wb") as f:
    f.write(doc.data)
print(f"Saved {doc.page_count} page(s) to scan_preview.pdf")
