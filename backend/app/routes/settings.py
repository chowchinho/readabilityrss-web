"""System settings endpoints for ReadabilityRSS."""
import asyncio
import os
import time
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from ..database import db
from ..services.article_image_cache import ARTICLE_IMAGE_CACHE_DIR, IMAGE_CACHE_DIR, DATA_DIR

router = APIRouter(prefix="/api/settings", tags=["settings"])

VALID_LANGUAGES = {"zh-TW", "zh-CN", "en", "es", "fr", "de", "ja", "ko", "pt", "ru", "ar", "hi"}
VALID_IMAGE_DIMENSIONS = (800, 1000, 1200)
VALID_JPEG_QUALITIES = (50, 60, 70, 80, 85)

# Walking the image cache means stat()ing tens of thousands of files, so the
# result is reused briefly — the numbers only move when a refresh runs.
_STATS_TTL_SECONDS = 60
_stats_cache = {"at": 0.0, "value": None}


class SystemSettingsUpdate(BaseModel):
    max_articles_per_feed: int
    feed_refresh_interval_hours: float
    target_language: Optional[str] = "zh-TW"
    deepseek_enabled: Optional[bool] = False
    deepseek_api_key: Optional[str] = ""
    deepl_enabled: Optional[bool] = False
    deepl_api_key: Optional[str] = ""
    flaresolverr_enabled: Optional[bool] = False
    flaresolverr_url: Optional[str] = "http://localhost:8191/v1"
    image_max_dimension: Optional[int] = 1200
    image_jpeg_quality: Optional[int] = 70
    # None means "leave as is" — a dashboard bundle cached from before this field
    # existed would otherwise switch AI back on every time it saved anything else.
    ai_enabled: Optional[bool] = None


@router.get("")
async def get_settings():
    return await db.get_system_settings()


@router.put("")
async def update_settings(req: SystemSettingsUpdate, request: Request):
    if req.max_articles_per_feed not in (10, 20, 30, 50, 100, 200):
        raise HTTPException(status_code=400, detail="Invalid max_articles_per_feed value")
    if req.feed_refresh_interval_hours not in (0.5, 1.0, 2.0, 4.0, 6.0, 12.0, 24.0):
        raise HTTPException(status_code=400, detail="Invalid feed_refresh_interval_hours value")
    if req.target_language and req.target_language not in VALID_LANGUAGES:
        raise HTTPException(status_code=400, detail="Invalid target_language value")
    if req.image_max_dimension not in VALID_IMAGE_DIMENSIONS:
        raise HTTPException(status_code=400, detail="Invalid image_max_dimension value")
    if req.image_jpeg_quality not in VALID_JPEG_QUALITIES:
        raise HTTPException(status_code=400, detail="Invalid image_jpeg_quality value")

    current = await db.get_system_settings()
    ai_enabled = current.get("ai_enabled", True) if req.ai_enabled is None else bool(req.ai_enabled)

    new_settings = {
        "max_articles_per_feed": req.max_articles_per_feed,
        "feed_refresh_interval_hours": req.feed_refresh_interval_hours,
        "target_language": req.target_language or "zh-TW",
        "deepseek_enabled": bool(req.deepseek_enabled),
        "deepseek_api_key": (req.deepseek_api_key or "").strip(),
        "deepl_enabled": bool(req.deepl_enabled),
        "deepl_api_key": (req.deepl_api_key or "").strip(),
        "flaresolverr_enabled": bool(req.flaresolverr_enabled),
        "flaresolverr_url": (req.flaresolverr_url or "http://localhost:8191/v1").strip(),
        "image_max_dimension": req.image_max_dimension,
        "image_jpeg_quality": req.image_jpeg_quality,
        "ai_enabled": ai_enabled,
    }
    await db.update_system_settings(new_settings)
    return {"success": True, "settings": new_settings}


def _scan_image_cache() -> dict:
    """Measure the article image cache and derived image cache, split by whether re-encoding applies.

    Animated GIFs are stored as-is by _prepare_cached_image, so the dimension
    and quality settings do not affect them. They are a small share of files
    but a large share of bytes, so the projection must not scale them.
    """
    stats = {"files": 0, "bytes": 0, "gif_files": 0, "gif_bytes": 0}
    for cache_dir in (ARTICLE_IMAGE_CACHE_DIR, IMAGE_CACHE_DIR):
        if not os.path.exists(cache_dir):
            continue
        for root, _, names in os.walk(cache_dir):
            for name in names:
                try:
                    size = os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
                stats["files"] += 1
                stats["bytes"] += size
                if name.lower().endswith(".gif"):
                    stats["gif_files"] += 1
                    stats["gif_bytes"] += size
    return stats


@router.get("/storage-stats")
async def get_storage_stats():
    """Measured storage figures, used to project the cost of the article cap."""
    now = time.time()
    if _stats_cache["value"] and now - _stats_cache["at"] < _STATS_TTL_SECONDS:
        return _stats_cache["value"]

    conn = await db._get_db()
    cursor = await conn.execute(
        'SELECT COUNT(*) AS total, '
        'SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled FROM feed_sources'
    )
    source_row = await cursor.fetchone()
    cursor = await conn.execute('SELECT COUNT(*) AS n FROM feed_articles')
    article_row = await cursor.fetchone()

    cache = await asyncio.to_thread(_scan_image_cache)

    db_bytes = 0
    try:
        db_bytes = os.path.getsize(os.path.join(DATA_DIR, "feeds.db"))
    except OSError:
        pass

    total_articles = article_row["n"] or 0
    encoded_files = cache["files"] - cache["gif_files"]
    encoded_bytes = cache["bytes"] - cache["gif_bytes"]
    stats = {
        "total_sources": source_row["total"] or 0,
        "enabled_sources": source_row["enabled"] or 0,
        "total_articles": total_articles,
        "image_cache_files": cache["files"],
        "image_cache_bytes": cache["bytes"],
        "gif_files": cache["gif_files"],
        "gif_bytes": cache["gif_bytes"],
        "database_bytes": db_bytes,
        # Per-article figures drive the projection in the dashboard. They are
        # measured rather than assumed, so they track the user's actual feeds.
        "images_per_article": (cache["files"] / total_articles) if total_articles else 0,
        "encoded_per_article": (encoded_files / total_articles) if total_articles else 0,
        "gifs_per_article": (cache["gif_files"] / total_articles) if total_articles else 0,
        "avg_encoded_bytes": (encoded_bytes / encoded_files) if encoded_files else 0,
        "avg_gif_bytes": (cache["gif_bytes"] / cache["gif_files"]) if cache["gif_files"] else 0,
    }
    _stats_cache["at"] = now
    _stats_cache["value"] = stats
    return stats
