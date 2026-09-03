"""FEVER API key validation shared across API routes."""
import hmac

from ..database import db


async def validate_api_key(api_key: str | None) -> bool:
    if not api_key:
        return False
    auth = await db.get_fever_auth()
    if not auth or not auth.get("api_key"):
        return False
    return hmac.compare_digest(api_key, auth["api_key"])
