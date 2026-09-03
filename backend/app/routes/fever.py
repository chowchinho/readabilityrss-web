"""Fever API endpoint for RSS reader app compatibility."""
import hashlib
import hmac
import os
import urllib.parse
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ..database import db
from ..services.article_image_cache import is_local_cached_image_url

router = APIRouter(tags=["fever"])

# --- Auth management endpoints (for frontend Options page) ---

class FeverAuthRequest(BaseModel):
    username: str
    password: str

@router.get("/api/fever-auth")
async def get_fever_auth():
    auth = await db.get_fever_auth()
    if auth:
        return {"enabled": True, "username": auth["username"]}
    return {"enabled": False, "username": ""}

@router.post("/api/fever-auth")
async def set_fever_auth(req: FeverAuthRequest):
    api_key = hashlib.md5(f"{req.username}:{req.password}".encode()).hexdigest()
    await db.set_fever_auth(req.username, api_key)
    return {"success": True}

@router.delete("/api/fever-auth")
async def delete_fever_auth():
    await db.delete_fever_auth()
    return {"success": True}

@router.get("/api/fever-endpoint")
async def get_fever_endpoint():
    """Return the Fever API endpoint URL for display in the frontend."""
    public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if public_url:
        return {"url": f"{public_url}/fever/"}
    return {"url": "http://localhost:8001/fever/"}

# --- Fever protocol endpoint ---

def _to_unix_ts(date_str):
    """Convert ISO date string or SQLite timestamp to unix epoch."""
    if not date_str:
        return 0
    try:
        if isinstance(date_str, (int, float)):
            return int(date_str)
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(date_str, fmt).timestamp())
            except ValueError:
                continue
        return 0
    except Exception:
        return 0

def _build_feeds_groups(sources):
    """Build the feeds_groups array from feed sources."""
    groups = {}
    for s in sources:
        gid = s.get("category_id") or 0
        groups.setdefault(gid, []).append(str(s["id"]))
    return [{"group_id": gid, "feed_ids": ",".join(fids)} for gid, fids in groups.items()]


async def _authenticate(form_data) -> bool:
    api_key = form_data.get("api_key", "")
    if not api_key:
        return False
    auth = await db.get_fever_auth()
    if not auth or not auth.get("api_key"):
        return False
    return hmac.compare_digest(api_key, auth["api_key"])


def _to_absolute_url(base_url: str, path_or_url: str | None) -> str | None:
    if not path_or_url:
        return None
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return f"{base_url}{path_or_url}" if path_or_url.startswith("/") else f"{base_url}/{path_or_url}"


def _build_main_image_proxy(base_url: str, image_url: str | None, width: int = 800) -> str | None:
    if not image_url:
        return None
    if is_local_cached_image_url(image_url):
        return _to_absolute_url(base_url, image_url)
    encoded_url = urllib.parse.quote(image_url)
    return f"{base_url}/api/reader/image-proxy?url={encoded_url}&w={width}"


@router.post("/fever")
@router.post("/fever/")
async def fever_api(request: Request):
    form_data = await request.form()
    params = request.query_params

    base_response = {"api_version": 3, "auth": 0}

    if not await _authenticate(form_data):
        return JSONResponse(base_response)

    last_refreshed = await db.get_last_refreshed_on_time()
    response = {
        "api_version": 3,
        "auth": 1,
        "last_refreshed_on_time": last_refreshed,
    }

    # Handle write operations first
    mark = form_data.get("mark")
    if mark:
        mark_as = form_data.get("as", "")
        try:
            mark_id = int(form_data.get("id", 0) or 0)
        except (ValueError, TypeError):
            mark_id = 0
        try:
            before = int(form_data.get("before", 0) or 0)
        except (ValueError, TypeError):
            before = 0

        if mark == "item":
            if mark_as == "read":
                await db.mark_item_read(mark_id)
            elif mark_as == "saved":
                await db.mark_item_saved(mark_id)
            elif mark_as == "unsaved":
                await db.mark_item_unsaved(mark_id)
        elif mark == "feed" and mark_as == "read":
            await db.mark_feed_read(mark_id, before)
        elif mark == "group" and mark_as == "read":
            await db.mark_group_read(mark_id, before)

    # Handle read queries
    if "groups" in params:
        categories = await db.get_categories()
        sources = [s for s in await db.get_feed_sources() if s.get('enabled', 1)]
        groups = [{"id": c["id"], "title": c["name"]} for c in categories]
        # Add uncategorized group if any feeds lack a category
        has_uncategorized = any(s.get("category_id") is None for s in sources)
        if has_uncategorized:
            groups.append({"id": 0, "title": "Uncategorized"})
        response["groups"] = groups
        response["feeds_groups"] = _build_feeds_groups(sources)

    if "feeds" in params:
        sources = [s for s in await db.get_feed_sources() if s.get('enabled', 1)]
        public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
        base_url = public_url if public_url else str(request.base_url).rstrip("/")
        feeds = []
        for s in sources:
            favicon_proxy = f"{base_url}/api/reader/favicon/{s['id']}"
            feeds.append({
                "id": s["id"],
                "favicon_id": 0,
                "title": s["name"],
                "url": f"{base_url}/feed/{s['id']}/rss",
                "site_url": s["url"],
                "favicon": favicon_proxy,
                "favicon_proxy": favicon_proxy,
                "is_spark": 0,
                "last_updated_on_time": _to_unix_ts(s.get("last_fetch_at") or s.get("updated_at")),
            })
        response["feeds"] = feeds
        response["feeds_groups"] = _build_feeds_groups(sources)

    if "items" in params:
        since_id = params.get("since_id")
        max_id = params.get("max_id")
        with_ids = params.get("with_ids")

        kwargs = {}
        if with_ids:
            parsed_ids = []
            for x in with_ids.split(","):
                try:
                    if x.strip():
                        parsed_ids.append(int(x.strip()))
                except (ValueError, TypeError):
                    pass
            kwargs["with_ids"] = parsed_ids
        elif since_id is not None:
            try:
                kwargs["since_id"] = int(since_id)
            except (ValueError, TypeError):
                pass
        elif max_id is not None:
            try:
                kwargs["max_id"] = int(max_id)
            except (ValueError, TypeError):
                pass

        articles = await db.get_fever_items(**kwargs)
        public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
        base_url = public_url if public_url else str(request.base_url).rstrip("/")
        items = []
        for a in articles:
            main_image = a.get("main_image") or ""
            items.append({
                "id": a["id"],
                "feed_id": a["source_id"],
                "title": a.get("title") or "",
                "author": "",
                "html": a.get("content") or "",
                "url": a["url"],
                "main_image": _to_absolute_url(base_url, main_image) if main_image else "",
                "main_image_proxy": _build_main_image_proxy(base_url, main_image) or "",
                "is_saved": a.get("is_saved", 0),
                "is_read": a.get("is_read", 0),
                "created_on_time": _to_unix_ts(a.get("pub_date") or a.get("created_at")),
            })
        response["items"] = items
        response["total_items"] = await db.get_total_item_count()

    if "favicons" in params:
        # Return a single default 1x1 transparent GIF favicon
        response["favicons"] = [{
            "id": 0,
            "data": "image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        }]

    if "unread_item_ids" in params:
        ids = await db.get_unread_item_ids()
        response["unread_item_ids"] = ",".join(str(i) for i in ids)

    if "saved_item_ids" in params:
        ids = await db.get_saved_item_ids()
        response["saved_item_ids"] = ",".join(str(i) for i in ids)

    if "links" in params:
        response["links"] = []

    return JSONResponse(response)
