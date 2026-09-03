"""Shared HTTP fetch with FlareSolverr fallback for Cloudflare-protected sites."""
import asyncio
import codecs
import logging
import os
import re
import time
import requests
from .url_safety import validate_url_ssrf

logger = logging.getLogger(__name__)

FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191/v1")
FLARESOLVERR_HEALTH_TTL_SECONDS = 60

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Encoding": "gzip, deflate",
}

_flaresolverr_health = {
    "checked_at": 0.0,
    "available": False,
}


def fetch_html(url: str, timeout: int = 15) -> str:
    """Fetch HTML from a URL (sync). Falls back to FlareSolverr on 403."""
    if not validate_url_ssrf(url):
        raise ValueError(f"SSRF validation failed: URL must use http/https and target a safe public host: {url}")
    response = requests.get(url, timeout=timeout, headers=HEADERS)

    if response.status_code == 403 and is_flaresolverr_available():
        return _fetch_via_flaresolverr(url)

    response.raise_for_status()
    # When the HTTP header omits charset, requests defaults to ISO-8859-1 for text/*.
    # This breaks Japanese (and other non-Latin) sites that rely on HTML meta charset.
    # Prefer the HTML <meta charset> (authoritative for these pages) over chardet,
    # which mis-detects CJK UTF-8 as a Latin variant (e.g. Windows-1254) and yields
    # mojibake; fall back to chardet only when no usable meta charset is present.
    if response.encoding and response.encoding.upper() in ('ISO-8859-1', 'LATIN-1'):
        response.encoding = _detect_html_charset(response.content) or response.apparent_encoding
    return response.text


def _detect_html_charset(content: bytes) -> str | None:
    """Return the charset declared in the HTML head, if it names a valid codec."""
    head = content[:4096]
    match = re.search(rb'<meta[^>]+charset=["\']?\s*([\w-]+)', head, re.I)
    if not match:
        match = re.search(rb'charset=["\']?\s*([\w-]+)', head, re.I)
    if not match:
        return None
    try:
        charset = match.group(1).decode('ascii').strip()
        codecs.lookup(charset)
        return charset
    except (LookupError, UnicodeDecodeError):
        return None


async def fetch_html_async(url: str, timeout: int = 15) -> str:
    """Fetch HTML without blocking the event loop."""
    return await asyncio.to_thread(fetch_html, url, timeout)


async def fetch_html_rendered_async(url: str) -> str:
    """Fetch HTML via FlareSolverr without blocking the event loop."""
    return await asyncio.to_thread(_fetch_via_flaresolverr, url)


def _get_flaresolverr_info() -> tuple[bool, str]:
    """Return (enabled, url) from system_settings or env fallback."""
    try:
        from ..database import db
        s = db.get_system_settings_sync()
        enabled = s.get("flaresolverr_enabled", False)
        url = s.get("flaresolverr_url") or FLARESOLVERR_URL
        return bool(enabled), url
    except Exception:
        return bool(os.environ.get("FLARESOLVERR_URL")), FLARESOLVERR_URL


def is_flaresolverr_available(force_refresh: bool = False) -> bool:
    """Return whether FlareSolverr is reachable. Uses a short-lived cache."""
    enabled, fs_url = _get_flaresolverr_info()
    if not enabled:
        return False

    now = time.time()
    if (
        not force_refresh
        and _flaresolverr_health["checked_at"]
        and (now - _flaresolverr_health["checked_at"] < FLARESOLVERR_HEALTH_TTL_SECONDS)
    ):
        return _flaresolverr_health["available"]

    available = False
    try:
        r = requests.post(
            fs_url,
            json={"cmd": "sessions.list"},
            timeout=3,
        )
        available = r.ok
    except requests.RequestException:
        available = False

    _flaresolverr_health["checked_at"] = now
    _flaresolverr_health["available"] = available
    return available


def get_flaresolverr_solution(url: str, max_timeout_ms: int = 30000) -> dict | None:
    """
    Try solving URL via FlareSolverr.
    Returns the `solution` dict when successful, else None.
    """
    if not is_flaresolverr_available():
        return None

    _, fs_url = _get_flaresolverr_info()
    try:
        logger.info(f"FlareSolverr solve: {url}")
        r = requests.post(
            fs_url,
            json={
                "cmd": "request.get",
                "url": url,
                "maxTimeout": max_timeout_ms,
            },
            timeout=max(10, int(max_timeout_ms / 1000) + 5),
        )
        data = r.json()
        if data.get("status") == "ok" and data.get("solution"):
            return data["solution"]
    except requests.RequestException:
        logger.info("FlareSolverr unavailable while solving URL")
    except ValueError:
        logger.warning("FlareSolverr returned non-JSON response")
    except Exception as exc:
        logger.warning(f"FlareSolverr solve failed: {exc}")

    return None


def _fetch_via_flaresolverr(url: str) -> str:
    """Solve Cloudflare challenge via FlareSolverr."""
    _, fs_url = _get_flaresolverr_info()
    try:
        logger.info(f"FlareSolverr: fetching {url}")
        r = requests.post(fs_url, json={
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 30000,
        }, timeout=60)
        data = r.json()
        if data.get("status") == "ok" and data.get("solution", {}).get("response"):
            return data["solution"]["response"]
        raise Exception(f"FlareSolverr failed: {data.get('message', 'unknown error')}")
    except requests.ConnectionError:
        raise Exception("Cloudflare blocked this URL and FlareSolverr is not available")
    except (ValueError, AttributeError) as exc:
        raise Exception(f"FlareSolverr returned malformed response: {exc}")
