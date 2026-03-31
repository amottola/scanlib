"""Command-line interface for scanlib.

Usage::

    scanlib list                     # list available scanners
    scanlib info -s 0                # show scanner capabilities
    scanlib scan -o out.pdf --dpi 300 --color-mode gray
"""

from __future__ import annotations

import argparse
import sys
import threading

from . import __version__, list_scanners, open_scanner
from ._types import (
    ColorMode,
    ImageFormat,
    ScanAborted,
    ScanArea,
    ScanLibError,
    ScanSource,
    Scanner,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_by_selector(selector: str, timeout: float = 15.0) -> Scanner:
    """Open a scanner by index, ID, or substring.

    If *selector* looks like a scanner ID (contains ``:``) and is not a
    bare numeric index, tries :func:`open_scanner` first to skip
    discovery.  Falls back to :func:`list_scanners` otherwise.

    Returns an **opened** scanner.
    """
    # Try direct open by ID when the selector looks like one
    is_index = selector.isdigit()
    if not is_index and ":" in selector:
        try:
            return open_scanner(selector)
        except Exception:
            pass  # fall back to discovery

    # Discovery path
    scanners = list_scanners(timeout=timeout)
    if not scanners:
        _err("No scanners found.")
        sys.exit(1)

    # Try numeric index
    if is_index:
        idx = int(selector)
        if 0 <= idx < len(scanners):
            scanner = scanners[idx]
            scanner.open()
            return scanner

    # Exact ID match
    for s in scanners:
        if s.id == selector:
            s.open()
            return s

    # Substring match on name or str()
    sel_lower = selector.lower()
    for s in scanners:
        if sel_lower in s.name.lower() or sel_lower in str(s).lower():
            s.open()
            return s

    _err(f"Scanner not found: {selector!r}")
    _err(f"Available scanners ({len(scanners)}):")
    for i, s in enumerate(scanners):
        _err(f"  {i}: {s} [{s.backend}]")
    sys.exit(1)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


_BAR_WIDTH = 30
_PULSE_WIDTH = 6  # width of the sliding highlight


class _Progress:
    """Progress reporter with animated indeterminate and determinate bars.

    During the indeterminate phase (percent < 0) a highlight slides
    back and forth across the bar in a background thread.  Once a real
    percentage arrives the bar switches to a standard fill.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def __call__(self, percent: int) -> bool:
        if percent < 0:
            self._start_pulse()
        elif percent >= 100:
            self._stop_pulse()
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        else:
            self._stop_pulse()
            filled = int(_BAR_WIDTH * percent / 100)
            empty = _BAR_WIDTH - filled
            bar = "\u2588" * filled + "\u2591" * empty
            sys.stderr.write(f"\r  Scanning [{bar}] {percent:3d}%\033[K")
            sys.stderr.flush()
        return True

    def _start_pulse(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._pulse_loop, daemon=True)
            self._thread.start()

    def _stop_pulse(self) -> None:
        with self._lock:
            if self._thread is None:
                return
            self._stop.set()
            t = self._thread
            self._thread = None
        t.join(timeout=1)
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def _pulse_loop(self) -> None:
        import time

        pos = 0
        direction = 1
        while not self._stop.is_set():
            bar = ["\u2591"] * _BAR_WIDTH
            for i in range(pos, min(pos + _PULSE_WIDTH, _BAR_WIDTH)):
                bar[i] = "\u2588"
            line = "".join(bar)
            sys.stderr.write(f"\r  Scanning [{line}]   - \033[K")
            sys.stderr.flush()
            pos += direction
            if pos + _PULSE_WIDTH >= _BAR_WIDTH:
                direction = -1
            elif pos <= 0:
                direction = 1
            time.sleep(0.08)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> None:
    scanners = list_scanners(timeout=args.timeout)
    if not scanners:
        _err("No scanners found.")
        sys.exit(1)

    # Compute column widths
    rows = []
    for i, s in enumerate(scanners):
        rows.append(
            (
                str(i),
                str(s),
                s.id,
                s.location or "-",
                s.backend,
            )
        )

    headers = ("#", "Name", "ID", "Location", "Backend")
    widths = [len(h) for h in headers]
    for row in rows:
        for j, cell in enumerate(row):
            widths[j] = max(widths[j], len(cell))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def cmd_info(args: argparse.Namespace) -> None:
    scanner = _open_by_selector(args.scanner)

    with scanner:
        print(f"Scanner:  {scanner}")
        print(f"ID:       {scanner.id}")
        print(f"Backend:  {scanner.backend}")
        print(f"Vendor:   {scanner.vendor or '-'}")
        print(f"Model:    {scanner.model or '-'}")
        print(f"Location: {scanner.location or '-'}")
        print()

        defaults = scanner.defaults
        if defaults:
            print("Defaults:")
            print(f"  DPI:        {defaults.dpi}")
            print(f"  Color mode: {defaults.color_mode.value}")
            if defaults.source:
                print(f"  Source:     {defaults.source.value}")
            print()

        print("Sources:")
        for si in scanner.sources:
            print(f"  {si.type.value.title()}:")
            res_str = ", ".join(str(r) for r in si.resolutions)
            print(f"    Resolutions: {res_str}")
            modes_str = ", ".join(m.value for m in si.color_modes)
            print(f"    Color modes: {modes_str}")
            if si.max_scan_area:
                a = si.max_scan_area
                mm_w = a.width / 10
                mm_h = a.height / 10
                print(
                    f"    Max scan area: {a.width} x {a.height} "
                    f"({mm_w:.1f} x {mm_h:.1f} mm)"
                )
            print()


def cmd_reset(args: argparse.Namespace) -> None:
    scanner_id = args.scanner
    if not scanner_id.startswith("escl:"):
        _err("Reset is only supported for eSCL scanners (ID starts with escl:).")
        sys.exit(1)

    from .backends._escl import _parse_escl_id

    conn = _parse_escl_id(scanner_id)
    if conn is None:
        _err(f"Invalid eSCL scanner ID: {scanner_id}")
        sys.exit(1)

    status = conn.get_status()
    _err(f"Scanner status: {status}")
    cancelled = conn.cancel_active_jobs()
    if cancelled:
        _err(f"Cancelled {cancelled} active job(s).")
        status = conn.get_status()
        _err(f"Scanner status: {status}")
    else:
        _err("No active jobs found.")
    conn.close()


def cmd_scan(args: argparse.Namespace) -> None:
    scanner = _open_by_selector(args.scanner)

    with scanner:
        # Resolve defaults
        defaults = scanner.defaults
        dpi = args.dpi
        if dpi is None and defaults:
            dpi = defaults.dpi
        if dpi is None:
            dpi = 300

        color_mode = None
        if args.color_mode:
            color_mode = ColorMode(args.color_mode)
        elif defaults:
            color_mode = defaults.color_mode
        if color_mode is None:
            color_mode = ColorMode.COLOR

        source = None
        if args.source:
            source = ScanSource(args.source)
        elif defaults and defaults.source:
            source = defaults.source

        scan_area = None
        if args.scan_area:
            parts = args.scan_area.split(",")
            if len(parts) != 4:
                _err(
                    "--scan-area must be x,y,width,height "
                    "(4 comma-separated integers)"
                )
                sys.exit(1)
            try:
                scan_area = ScanArea(*(int(p.strip()) for p in parts))
            except ValueError:
                _err("--scan-area values must be integers (in 1/10 mm)")
                sys.exit(1)

        image_format = None
        if args.format:
            image_format = ImageFormat(args.format)

        # Build next_page callback
        next_page = None
        if args.pages and source != ScanSource.FEEDER:
            if args.pages.lower() == "ask":

                def next_page(count: int) -> bool:
                    try:
                        sys.stderr.write(
                            f"Scanned {count} page(s). " f"Scan another page? [y/N] "
                        )
                        sys.stderr.flush()
                        answer = input().strip().lower()
                        return answer in ("y", "yes")
                    except (EOFError, KeyboardInterrupt):
                        return False

            else:
                try:
                    max_pages = int(args.pages)
                except ValueError:
                    _err("--pages must be a number or 'ask'")
                    sys.exit(1)

                def next_page(count: int, _max=max_pages) -> bool:
                    return count < _max

        _err(f"Scanning with {scanner} @ {dpi} DPI, {color_mode.value}...")

        try:
            result = scanner.scan(
                dpi=dpi,
                color_mode=color_mode,
                source=source,
                scan_area=scan_area,
                progress=_Progress(),
                next_page=next_page,
                image_format=image_format,
                jpeg_quality=args.jpeg_quality,
                bw_threshold=args.bw_threshold,
            )
        except ScanAborted:
            _err("Scan aborted.")
            sys.exit(1)

        output = args.output
        try:
            with open(output, "wb") as f:
                f.write(result.data)
        except (OSError, PermissionError) as exc:
            _err(f"Failed to write {output}: {exc}")
            sys.exit(1)

        _err(
            f"Saved {result.page_count} page(s) to {output} "
            f"({len(result.data)} bytes, {result.width}x{result.height} px)"
        )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scanlib",
        description="Scan documents from the command line.",
    )
    parser.add_argument("--version", action="version", version=f"scanlib {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    # --- list ---
    p_list = subparsers.add_parser("list", help="List available scanners")
    p_list.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Discovery timeout in seconds (default: 15)",
    )

    # --- info ---
    p_info = subparsers.add_parser("info", help="Show scanner capabilities")
    p_info.add_argument(
        "-s",
        "--scanner",
        default="0",
        help="Scanner index or ID (default: 0)",
    )

    # --- scan ---
    p_scan = subparsers.add_parser("scan", help="Scan a document to PDF")
    p_scan.add_argument(
        "-s",
        "--scanner",
        default="0",
        help="Scanner index or ID (default: 0)",
    )
    p_scan.add_argument(
        "-o",
        "--output",
        default="scan.pdf",
        help="Output PDF file path (default: scan.pdf)",
    )
    p_scan.add_argument(
        "--dpi",
        type=int,
        default=None,
        help="Scan resolution in DPI (default: scanner default)",
    )
    p_scan.add_argument(
        "--color-mode",
        choices=["color", "gray", "bw"],
        default=None,
        help="Color mode (default: scanner default)",
    )
    p_scan.add_argument(
        "--source",
        choices=["flatbed", "feeder"],
        default=None,
        help="Scan source (default: scanner default)",
    )
    p_scan.add_argument(
        "--scan-area",
        default=None,
        help="Scan area as x,y,width,height in 1/10 mm",
    )
    p_scan.add_argument(
        "--format",
        choices=["jpeg", "png"],
        default=None,
        help="Image format inside PDF (default: auto)",
    )
    p_scan.add_argument(
        "--jpeg-quality",
        type=int,
        default=85,
        help="JPEG quality 1-100 (default: 85)",
    )
    p_scan.add_argument(
        "--pages",
        default=None,
        help="Number of pages or 'ask' for interactive prompting",
    )
    p_scan.add_argument(
        "--bw-threshold",
        type=int,
        default=128,
        help="BW mode threshold 0-255: pixels >= value become white (default: 128)",
    )

    # --- reset ---
    p_reset = subparsers.add_parser(
        "reset", help="Cancel active jobs on an eSCL scanner"
    )
    p_reset.add_argument(
        "-s",
        "--scanner",
        required=True,
        help="eSCL scanner ID (e.g. escl:192.168.1.5:443)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "list":
            cmd_list(args)
        elif args.command == "info":
            cmd_info(args)
        elif args.command == "scan":
            cmd_scan(args)
        elif args.command == "reset":
            cmd_reset(args)
    except KeyboardInterrupt:
        _err("\nAborted.")
        sys.exit(130)
    except ScanLibError as exc:
        _err(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
