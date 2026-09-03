"""Clock helpers."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC now — the semantics `datetime.utcnow()` had before it was deprecated.

    Timestamps here are stored as naive UTC strings and read back with `strptime`, so
    returning an aware datetime would raise on every subtraction against a parsed one.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
