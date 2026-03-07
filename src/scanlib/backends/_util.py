"""Shared utilities for scanner backends."""

from __future__ import annotations

from collections.abc import Callable

from .._types import ScanAborted

MM_PER_INCH = 25.4


def check_progress(progress: Callable[[int], bool] | None, percent: int) -> None:
    """Call the progress callback; raise ScanAborted if it returns False."""
    if progress is not None and progress(percent) is False:
        raise ScanAborted("Scan aborted")
