from unittest import mock

import pytest

import scanlib
from scanlib._types import BackendNotAvailableError, Scanner


class TestGetBackend:
    def test_unsupported_platform(self):
        # Reset cached backend
        scanlib._backend = None
        with mock.patch("scanlib.sys") as mock_sys:
            mock_sys.platform = "freebsd"

            with pytest.raises(BackendNotAvailableError, match="Unsupported platform"):
                scanlib._get_backend()
        # Reset again so other tests aren't affected
        scanlib._backend = None

    def test_caches_backend(self):
        scanlib._backend = None
        try:
            b1 = scanlib._get_backend()
        except (OSError, Exception):
            pytest.skip("platform backend unavailable")
        finally:
            scanlib._backend = None
        scanlib._backend = None
        b1 = scanlib._get_backend()
        b2 = scanlib._get_backend()
        assert b1 is b2
        scanlib._backend = None


class TestListScanners:
    def test_returns_scanners_from_backend(self):
        fake_scanners = [
            Scanner(
                name="Test Scanner",
                vendor="Acme",
                model="X100",
                backend="mock",
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
        scanlib._backend = None
        try:
            result = scanlib.list_scanners()
        except (OSError, Exception):
            # Backend may fail to initialise in CI (e.g. WIA unavailable)
            pytest.skip("platform backend unavailable")
        finally:
            scanlib._backend = None

        assert isinstance(result, list)
        for scanner in result:
            assert isinstance(scanner, Scanner)
            assert isinstance(scanner.name, str)
            assert isinstance(scanner.backend, str)
