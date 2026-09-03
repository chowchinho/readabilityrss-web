import hashlib
import bcrypt
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app
from app.routes.auth import _hash_password, _reset_login_throttling

client = TestClient(app)

TEST_USER = "admin"
TEST_PASS = "secret123"
TEST_HASH = hashlib.sha256(TEST_PASS.encode()).hexdigest()
TEST_BCRYPT = _hash_password(TEST_PASS)


@pytest.fixture(autouse=True)
def reset_throttling():
    _reset_login_throttling()
    yield
    _reset_login_throttling()


def _mock_no_user():
    """Return a mock db where no web_auth user exists."""
    mock = AsyncMock()
    mock.get_web_auth = AsyncMock(return_value=None)
    return mock


def _mock_with_user(token=None):
    """Return a mock db where a user exists."""
    mock = AsyncMock()
    mock.get_web_auth = AsyncMock(return_value={
        "id": 1, "username": TEST_USER, "password_hash": TEST_BCRYPT,
        "session_token": token, "created_at": "2026-03-21"
    })
    mock.validate_session_token = AsyncMock(side_effect=lambda t: t == token and t is not None)
    return mock


# --- Auth Status ---

@patch('app.routes.auth.db')
def test_status_setup_required(mock_db):
    mock_db.get_web_auth = AsyncMock(return_value=None)
    resp = client.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setup_required"] is True
    assert data["authenticated"] is False


@patch('app.routes.auth.db')
def test_status_not_authenticated(mock_db):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_BCRYPT, "session_token": "abc123"
    })
    mock_db.validate_session_token = AsyncMock(return_value=False)
    resp = client.get("/api/auth/status")
    data = resp.json()
    assert data["setup_required"] is False
    assert data["authenticated"] is False


@patch('app.routes.auth.db')
def test_status_authenticated(mock_db):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_BCRYPT, "session_token": "valid_token"
    })
    mock_db.validate_session_token = AsyncMock(return_value=True)
    resp = client.get("/api/auth/status", headers={"Authorization": "Bearer valid_token"})
    data = resp.json()
    assert data["setup_required"] is False
    assert data["authenticated"] is True
    assert data["username"] == TEST_USER


# --- Setup ---

@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_setup_success(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value=None)
    mock_db.set_web_auth = AsyncMock()
    mock_db.add_session_token = AsyncMock()

    resp = client.post("/api/auth/setup", json={
        "username": TEST_USER, "password": TEST_PASS
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "token" in data
    mock_db.set_web_auth.assert_called_once()
    args = mock_db.set_web_auth.call_args[0]
    assert args[0] == TEST_USER
    assert bcrypt.checkpw(TEST_PASS.encode(), args[1].encode())


@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_setup_already_exists(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value={"username": "existing"})

    resp = client.post("/api/auth/setup", json={
        "username": TEST_USER, "password": TEST_PASS
    })
    assert resp.status_code == 400


@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=False)
@patch('app.routes.auth.db')
def test_setup_recaptcha_failure(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value=None)

    resp = client.post("/api/auth/setup", json={
        "username": TEST_USER, "password": TEST_PASS, "recaptcha_token": "bad"
    })
    assert resp.status_code == 403


# --- Login ---

@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_login_success(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_BCRYPT
    })
    mock_db.add_session_token = AsyncMock()

    resp = client.post("/api/auth/login", json={
        "username": TEST_USER, "password": TEST_PASS
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "token" in data


@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_legacy_sha256_login_transparently_migrates_to_bcrypt(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_HASH
    })
    mock_db.add_session_token = AsyncMock()
    mock_db.update_web_auth = AsyncMock()

    resp = client.post("/api/auth/login", json={
        "username": TEST_USER, "password": TEST_PASS
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "token" in data
    # Transparently migrated to bcrypt and persisted
    mock_db.update_web_auth.assert_called_once()
    args = mock_db.update_web_auth.call_args[0]
    assert args[0] == TEST_USER
    assert bcrypt.checkpw(TEST_PASS.encode(), args[1].encode())


@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_login_adds_new_session_without_invalidating_existing_ones(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_BCRYPT
    })
    mock_db.add_session_token = AsyncMock()

    resp = client.post("/api/auth/login", json={
        "username": TEST_USER, "password": TEST_PASS
    })
    assert resp.status_code == 200
    data = resp.json()
    mock_db.add_session_token.assert_called_once_with(data["token"])


@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_login_wrong_password(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_BCRYPT
    })

    resp = client.post("/api/auth/login", json={
        "username": TEST_USER, "password": "wrong"
    })
    assert resp.status_code == 401


@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_login_wrong_username(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_BCRYPT
    })

    resp = client.post("/api/auth/login", json={
        "username": "wronguser", "password": TEST_PASS
    })
    assert resp.status_code == 401


@patch('app.routes.auth._verify_recaptcha', new_callable=AsyncMock, return_value=True)
@patch('app.routes.auth.db')
def test_login_throttling_locks_out_after_max_attempts(mock_db, mock_recaptcha):
    mock_db.get_web_auth = AsyncMock(return_value={
        "username": TEST_USER, "password_hash": TEST_BCRYPT
    })
    mock_db.add_session_token = AsyncMock()

    # 5 failed attempts
    for _ in range(5):
        resp = client.post("/api/auth/login", json={
            "username": TEST_USER, "password": "wrongpassword"
        })
        assert resp.status_code == 401

    # 6th attempt with CORRECT credentials must be locked out
    resp = client.post("/api/auth/login", json={
        "username": TEST_USER, "password": TEST_PASS
    })
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid username or password"}


# --- Logout ---

@patch('app.main.db')
@patch('app.routes.auth.db')
def test_logout(mock_route_db, mock_main_db):
    for m in (mock_route_db, mock_main_db):
        m.get_web_auth = AsyncMock(return_value={"username": TEST_USER, "password_hash": TEST_BCRYPT, "session_token": "valid_token"})
        m.validate_session_token = AsyncMock(return_value=True)
    mock_route_db.clear_session_token = AsyncMock()

    resp = client.post("/api/auth/logout", headers={"Authorization": "Bearer valid_token"})
    assert resp.status_code == 200
    mock_route_db.clear_session_token.assert_called_once_with("valid_token")


@patch('app.main.db')
@patch('app.routes.auth.db')
def test_logout_unauthenticated(mock_route_db, mock_main_db):
    for m in (mock_route_db, mock_main_db):
        m.get_web_auth = AsyncMock(return_value={"username": TEST_USER, "password_hash": TEST_BCRYPT, "session_token": None})
        m.validate_session_token = AsyncMock(return_value=False)

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 401


# --- Credential Update ---

@patch('app.main.db')
@patch('app.routes.auth.db')
def test_update_credentials(mock_route_db, mock_main_db):
    for m in (mock_route_db, mock_main_db):
        m.get_web_auth = AsyncMock(return_value={"username": TEST_USER, "password_hash": TEST_BCRYPT, "session_token": "valid_token"})
        m.validate_session_token = AsyncMock(return_value=True)
    mock_route_db.update_web_auth = AsyncMock()

    resp = client.put("/api/auth/credentials",
        headers={"Authorization": "Bearer valid_token"},
        json={
            "current_password": TEST_PASS,
            "new_username": "newadmin",
            "new_password": "newpass"
        }
    )
    assert resp.status_code == 200
    mock_route_db.update_web_auth.assert_called_once()
    args = mock_route_db.update_web_auth.call_args[0]
    assert args[0] == "newadmin"
    assert bcrypt.checkpw("newpass".encode(), args[1].encode())


@patch('app.main.db')
@patch('app.routes.auth.db')
def test_update_credentials_wrong_current_password(mock_route_db, mock_main_db):
    for m in (mock_route_db, mock_main_db):
        m.get_web_auth = AsyncMock(return_value={"username": TEST_USER, "password_hash": TEST_BCRYPT, "session_token": "valid_token"})
        m.validate_session_token = AsyncMock(return_value=True)

    resp = client.put("/api/auth/credentials",
        headers={"Authorization": "Bearer valid_token"},
        json={
            "current_password": "wrong",
            "new_username": "newadmin",
            "new_password": "newpass"
        }
    )
    assert resp.status_code == 401


# --- Auth Middleware Fail Closed ---

@patch('app.main.db')
def test_auth_middleware_db_error_fails_closed_503(mock_main_db):
    mock_main_db.get_web_auth = AsyncMock(side_effect=Exception("Database disk I/O error"))
    resp = client.get("/api/categories")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Authentication service unavailable"}


# --- reCAPTCHA Verification Unit Tests ---

@pytest.mark.asyncio
async def test_verify_recaptcha_unconfigured_passes(monkeypatch):
    from app.routes.auth import _verify_recaptcha
    import app.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "RECAPTCHA_SECRET", "")
    assert await _verify_recaptcha("") is True
    assert await _verify_recaptcha("any_token") is True


@pytest.mark.asyncio
async def test_verify_recaptcha_configured_missing_token_fails_closed(monkeypatch):
    from app.routes.auth import _verify_recaptcha
    import app.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "RECAPTCHA_SECRET", "secret_key_123")
    assert await _verify_recaptcha("") is False


@pytest.mark.asyncio
async def test_verify_recaptcha_configured_network_error_fails_closed(monkeypatch):
    from app.routes.auth import _verify_recaptcha
    import app.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "RECAPTCHA_SECRET", "secret_key_123")
    with patch("httpx.AsyncClient.post", side_effect=Exception("Connection refused")):
        assert await _verify_recaptcha("some_token") is False



