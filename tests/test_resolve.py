from unittest import mock

import pytest

import scanlib
from scanlib._types import BackendNotAvailableError, ScannerInfo, ScanSource


class TestGetBackend:
    def test_unsupported_platform(self):
        with mock.patch("scanlib.sys") as mock_sys:
            mock_sys.platform = "freebsd"

            with pytest.raises(BackendNotAvailableError, match="Unsupported platform"):
                scanlib._get_backend()


class TestListScanners:
    def test_returns_scanners_from_backend(self):
        fake_scanners = [
            ScannerInfo(
                name="Test Scanner",
                vendor="Acme",
                model="X100",
                backend="mock",
                sources=[ScanSource.FLATBED, ScanSource.FEEDER],
            ),
        ]
        mock_backend = mock.MagicMock()
        mock_backend.list_scanners.return_value = fake_scanners

        with mock.patch("scanlib._get_backend", return_value=mock_backend):
            result = scanlib.list_scanners()

        assert result == fake_scanners
        mock_backend.list_scanners.assert_called_once()

    def test_returns_empty_list(self):
        mock_backend = mock.MagicMock()
        mock_backend.list_scanners.return_value = []

        with mock.patch("scanlib._get_backend", return_value=mock_backend):
            result = scanlib.list_scanners()

        assert result == []

    @pytest.mark.timeout(15)
    def test_with_real_backend(self):
        """Call list_scanners with the real platform backend, no mocking."""
        result = scanlib.list_scanners()

        assert isinstance(result, list)
        for scanner in result:
            assert isinstance(scanner, ScannerInfo)
            assert isinstance(scanner.name, str)
            assert isinstance(scanner.backend, str)
            assert isinstance(scanner.sources, list)
            for source in scanner.sources:
                assert isinstance(source, ScanSource)
