"""Multi-page scanning — from a document feeder or flatbed with prompts."""

import scanlib
from scanlib import FeederEmptyError, ScanSource

scanners = scanlib.list_scanners()
if not scanners:
    print("No scanners found.")
    raise SystemExit(1)

scanner = scanners[0]

with scanner:
    print(f"Using: {scanner.name}")
    print(f"Sources: {[s.value for s in scanner.sources]}")

    # -- Automatic document feeder (ADF) --
    if ScanSource.FEEDER in scanner.sources:
        print("\nScanning all pages from the document feeder...")
        try:
            doc = scanner.scan(source=ScanSource.FEEDER)
        except FeederEmptyError:
            print("  No documents in feeder — skipping.")
        else:
            with open("scan_feeder.pdf", "wb") as f:
                f.write(doc.data)
            print(f"  {doc.page_count} page(s) — saved to scan_feeder.pdf")

    # -- Flatbed multi-page with user prompts --
    if ScanSource.FLATBED in scanner.sources or not scanner.sources:
        print("\nFlatbed multi-page scan (press Enter for next page, 'q' to stop):")

        def next_page(pages_so_far: int) -> bool:
            reply = input(f"  {pages_so_far} page(s) scanned. Another? [Y/q] ").strip()
            return reply.lower() != "q"

        doc = scanner.scan(
            source=ScanSource.FLATBED if ScanSource.FLATBED in scanner.sources else None,
            next_page=next_page,
        )
        with open("scan_multi.pdf", "wb") as f:
            f.write(doc.data)
        print(f"  {doc.page_count} page(s) — saved to scan_multi.pdf")
