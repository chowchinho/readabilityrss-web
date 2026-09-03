"""Web authentication routes for ReadabilityRSS."""
import hashlib
import hmac
import logging
import os
import secrets
import time
import bcrypt
import httpx
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from ..database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Note: If reCAPTCHA is enabled, both frontend client script (in public/index.html)
# and backend RECAPTCHA_SECRET_KEY must be configured together.
RECAPTCHA_SECRET = os.environ.get("RECAPTCHA_SECRET_KEY", "")

# In-process login attempt throttling (per-IP and per-username)
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900.0  # 15 minutes


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def _verify_password(stored: str, plaintext: str, username: str = "") -> bool:
    if not stored or not plaintext:
        return False

    # Check for legacy 64-character hex SHA-256 hash
    if len(stored) == 64 and all(c in "0123456789abcdefABCDEF" for c in stored):
        sha = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        if hmac.compare_digest(stored.lower(), sha.lower()):
            # Transparently upgrade legacy SHA-256 to bcrypt and persist
            new_hash = _hash_password(plaintext)
            if username:
                await db.update_web_auth(username, new_hash)
            else:
                auth = await db.get_web_auth()
                if auth:
                    await db.update_web_auth(auth["username"], new_hash)
            return True
        return False

    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), stored.encode("utf-8"))
    except Exception:
        return False


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


def _is_rate_limited(ip: str, username: str) -> bool:
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    for key in (f"ip:{ip}", f"user:{username.lower().strip()}"):
        attempts = [t for t in _LOGIN_ATTEMPTS.get(key, []) if t > cutoff]
        _LOGIN_ATTEMPTS[key] = attempts
        if len(attempts) >= MAX_FAILED_ATTEMPTS:
            return True
    return False


def _record_login_failure(ip: str, username: str):
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    for key in (f"ip:{ip}", f"user:{username.lower().strip()}"):
        attempts = [t for t in _LOGIN_ATTEMPTS.get(key, []) if t > cutoff]
        attempts.append(now)
        _LOGIN_ATTEMPTS[key] = attempts


def _record_login_success(ip: str, username: str):
    _LOGIN_ATTEMPTS.pop(f"ip:{ip}", None)
    _LOGIN_ATTEMPTS.pop(f"user:{username.lower().strip()}", None)


def _reset_login_throttling():
    """Helper for testing."""
    _LOGIN_ATTEMPTS.clear()


async def _verify_recaptcha(token: str) -> bool:
    """Verify reCAPTCHA v3 token. Returns True if valid or if reCAPTCHA is not configured."""
    if not RECAPTCHA_SECRET:
        return True  # Skip verification if not configured
    if not token:
        logger.warning(
            "RECAPTCHA_SECRET_KEY is configured on backend, but client did not provide a recaptcha_token. "
            "Both backend RECAPTCHA_SECRET_KEY and frontend reCAPTCHA client script must be configured together."
        )
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={"secret": RECAPTCHA_SECRET, "response": token},
            )
            result = resp.json()
            return result.get("success", False) and result.get("score", 0) >= 0.5
    except Exception as e:
        logger.error(f"reCAPTCHA verification request failed: {e}")
        return False  # Fail closed when configured


def _get_token_from_request(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return ""


# --- Endpoints ---

class SetupRequest(BaseModel):
    username: str
    password: str
    recaptcha_token: str = ""

class LoginRequest(BaseModel):
    username: str
    password: str
    recaptcha_token: str = ""

class CredentialsRequest(BaseModel):
    current_password: str
    new_username: str
    new_password: str


@router.get("/status")
async def auth_status(request: Request):
    auth = await db.get_web_auth()
    setup_required = auth is None
    authenticated = False
    username = ""
    if not setup_required:
        token = _get_token_from_request(request)
        authenticated = await db.validate_session_token(token)
        username = auth["username"] if authenticated else ""
    return {"setup_required": setup_required, "authenticated": authenticated, "username": username}


@router.post("/setup")
async def auth_setup(req: SetupRequest):
    existing = await db.get_web_auth()
    if existing:
        raise HTTPException(status_code=400, detail="Account already exists. Use login instead.")

    if not await _verify_recaptcha(req.recaptcha_token):
        raise HTTPException(status_code=403, detail="reCAPTCHA verification failed")

    if not req.username.strip() or not req.password.strip():
        raise HTTPException(status_code=400, detail="Username and password are required")

    password_hash = _hash_password(req.password)
    await db.set_web_auth(req.username.strip(), password_hash)
    token = secrets.token_urlsafe(32)
    await db.add_session_token(token)
    return {"success": True, "token": token}


@router.post("/login")
async def auth_login(request: Request, req: LoginRequest):
    client_ip = _get_client_ip(request)
    if _is_rate_limited(client_ip, req.username):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not await _verify_recaptcha(req.recaptcha_token):
        _record_login_failure(client_ip, req.username)
        raise HTTPException(status_code=403, detail="reCAPTCHA verification failed")

    auth = await db.get_web_auth()
    if not auth:
        raise HTTPException(status_code=400, detail="No account configured. Setup required.")

    valid_username = hmac.compare_digest(auth["username"], req.username)
    valid_password = await _verify_password(auth["password_hash"], req.password, auth["username"]) if valid_username else False

    if not (valid_username and valid_password):
        _record_login_failure(client_ip, req.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _record_login_success(client_ip, req.username)
    token = secrets.token_urlsafe(32)
    await db.add_session_token(token)
    return {"success": True, "token": token}


@router.post("/logout")
async def auth_logout(request: Request):
    token = _get_token_from_request(request)
    if not await db.validate_session_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    await db.clear_session_token(token)
    return {"success": True}


@router.put("/credentials")
async def update_credentials(request: Request, req: CredentialsRequest):
    token = _get_token_from_request(request)
    if not await db.validate_session_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth = await db.get_web_auth()
    if not auth or not await _verify_password(auth["password_hash"], req.current_password, auth["username"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    if not req.new_username.strip() or not req.new_password.strip():
        raise HTTPException(status_code=400, detail="New username and password are required")

    await db.update_web_auth(req.new_username.strip(), _hash_password(req.new_password))
    return {"success": True}

