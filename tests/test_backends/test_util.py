"""Tests for shared backend utilities."""

from _scanlib_accel import trim_rows

from scanlib._types import ScanAborted, check_progress

import pytest


class TestCheckProgress:
    def test_none_callback(self):
        # Should not raise
        check_progress(None, 50)

    def test_true_return(self):
        check_progress(lambda p: True, 50)

    def test_false_return_aborts(self):
        with pytest.raises(ScanAborted):
            check_progress(lambda p: False, 50)

    def test_none_return_does_not_abort(self):
        # Returning None (not False) should not abort
        check_progress(lambda p: None, 50)


class TestTrimRows:
    def test_no_padding(self):
        data = bytes([1, 2, 3, 4, 5, 6])
        result = trim_rows(data, 2, 3, 3)
        assert result == data

    def test_with_padding(self):
        # stride=4, row_width=3 -> strip last byte of each row
        data = bytes([1, 2, 3, 0, 4, 5, 6, 0])
        result = trim_rows(data, 2, 4, 3)
        assert result == bytes([1, 2, 3, 4, 5, 6])
