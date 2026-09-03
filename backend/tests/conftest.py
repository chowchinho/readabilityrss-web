"""Shared test fixtures."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Mock app.main.db so the auth middleware passes all requests through.

    The middleware calls db.get_web_auth() — returning None means
    'no user configured yet', which skips auth checks.
    """
    mock_db = AsyncMock()
    mock_db.get_web_auth = AsyncMock(return_value=None)
    with patch('app.main.db', mock_db):
        yield mock_db


@pytest.fixture(autouse=True, scope="session")
def close_db_on_session_finish():
    """Without this the pytest process passes every test and then hangs forever,
    joining aiosqlite's non-daemon worker thread. See Database.close_sync."""
    yield
    from app.database import close_all_sync
    close_all_sync()

