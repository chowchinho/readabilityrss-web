"""Session validation shared across middleware and routes."""
import logging

from starlette.requests import Request

from ..database import Database, db

logger = logging.getLogger(__name__)


async def session_is_valid(request: Request, db_instance: Database | None = None) -> bool | None:
    """Validate web session Bearer token from request headers.

    Returns:
        True: valid session token, or web auth is not configured yet (first-time use).
        False: web auth is configured, but session token is missing or invalid.
        None: database error occurred during auth check or session validation.
    """
    target_db = db_instance if db_instance is not None else db
    try:
        auth = await target_db.get_web_auth()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Auth check database error: {e}")
        return None

    if auth is None:
        return True

    token = request.headers.get("authorization", "")
    if token.startswith("Bearer "):
        token = token[7:]
    else:
        token = ""

    try:
        return await target_db.validate_session_token(token)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Session validation database error: {e}")
        return None
