import logging
import ipaddress
import socket
import os
import io
import asyncio
import hashlib
import random
import urllib.parse
from typing import Optional, List
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import json
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from bs4 import BeautifulSoup
import httpx
from PIL import Image

from ..database import db
from ..services.ranking import score_article, score_article_breakdown
from ..services.rerank import calibrated_rerank, promote_exploration_slots
from ..services.vote_weights import get_effective_weights, invalidate_weights_cache
from ..services.article_events import record_article_vote
from ..services.article_image_cache import (
    ARTICLE_IMAGE_CACHE_DIR,
    absolutize_image_url,
    cached_image_hash_from_url,
    image_source_candidates,
    is_local_cached_image_url,
    remove_non_content_images,
)
from ..services.snippets import build_snippets
from ..utils.fetch import HEADERS, get_flaresolverr_solution
from ..utils.fever_key import validate_api_key
from ..utils.timeutil import utcnow

router = APIRouter(tags=["reader"])
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_PROXY_WIDTHS = (160, 320, 480, 800, 1200)
MAX_IMAGE_PROXY_BYTES = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 20_000_000


from ..utils.url_safety import _is_safe_ip, _validate_url_ssrf, is_safe_ip, validate_url_ssrf



def _ai_enabled() -> bool:
    return bool(db.get_system_settings_sync().get("ai_enabled", True))


@router.get("/api/reader/config")
async def get_reader_config():
    return {"ai_enabled": _ai_enabled()}


_FEED_HOST_PREFIXES = ("feeds", "feed", "rss", "feedproxy")


def resolve_site_url(stored_site_url: Optional[str], feed_url: str) -> str:
    """Best site URL for favicon lookup.

    Prefers a stored site_url captured from the feed's channel <link>. Falls
    back to the feed URL with a common feed-host prefix (feeds./rss./...)
    stripped, so favicons resolve to the real site rather than the feed host.
    """
    if stored_site_url:
        return stored_site_url

    parsed = urlparse(feed_url)
    host = parsed.netloc
    labels = host.split(".")
    if len(labels) >= 3 and labels[0].lower() in _FEED_HOST_PREFIXES:
        host = ".".join(labels[1:])
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{parsed.netloc}"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
IMAGE_CACHE_DIR = os.path.join(DATA_DIR, "image_cache")
FAVICON_CACHE_DIR = os.path.join(DATA_DIR, "favicon_cache")

os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
os.makedirs(FAVICON_CACHE_DIR, exist_ok=True)


def _build_reader_image_url(url: Optional[str], width: int = 800) -> Optional[str]:
    if not url:
        return None
    if is_local_cached_image_url(url):
        return url
    encoded_url = urllib.parse.quote(url)
    return f"/api/reader/image-proxy?url={encoded_url}&w={width}"


def _rewrite_content_image_sources(content: str, width: int = 800, base_url: str = "") -> str:
    """Point every image at a cached/proxied URL the browser will actually use.

    Read-time counterpart to cache_article_images: articles stored before
    responsive-source stripping existed still carry remote srcset/<source>, which
    the browser prefers over src, so the rewrite here must remove them too.
    """
    if not content:
        return content

    soup = BeautifulSoup(content, "html.parser")
    remove_non_content_images(soup)

    for img in soup.find_all("img"):
        if is_local_cached_image_url(img.get("src")):
            continue
        resolved = None
        for candidate in image_source_candidates(img):
            resolved = absolutize_image_url(candidate, base_url)
            if resolved:
                break
        if resolved and resolved.startswith("http"):
            img["src"] = _build_reader_image_url(resolved, width=width)

    _strip_proxied_overriding_sources(soup)
    return str(soup)


def _strip_proxied_overriding_sources(soup):
    """Drop srcset/<source> once the <img src> is cached or proxied."""
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if not (is_local_cached_image_url(src) or src.startswith("/api/reader/image-proxy")):
            continue
        for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
            if img.get(attr):
                del img[attr]
        parent = img.find_parent("picture")
        if parent:
            for source in parent.find_all("source"):
                source.decompose()

class BulkMarkReadRequest(BaseModel):
    article_ids: Optional[List[int]] = None
    source_id: Optional[int] = None
    category_id: Optional[int] = None
    before: Optional[str] = None
    mark_all: Optional[bool] = False

@router.get("/api/reader/feeds")
async def get_reader_feeds(since: Optional[str] = Query(None)):
    """Returns all enabled feed sources grouped by category, with unread counts and favicon URLs."""
    conn = await db._get_db()
    
    cursor = await conn.execute("SELECT * FROM categories ORDER BY name")
    categories = [dict(r) for r in await cursor.fetchall()]

    since_clause = "AND COALESCE(NULLIF(a.pub_date, ''), a.created_at) >= ?" if since else ""
    params = (since,) if since else ()
    
    cursor = await conn.execute(f'''
        SELECT f.id, f.name, f.url, f.site_url, f.category_id, f.desktop_view_mode, f.mobile_view_mode,
            (SELECT COUNT(*) FROM feed_articles a WHERE a.source_id = f.id AND a.parse_status = 'success' AND a.is_read = 0 {since_clause}) as unread_count,
            (SELECT COUNT(*) FROM feed_articles a WHERE a.source_id = f.id AND a.parse_status = 'success') as total_count,
            (SELECT MAX(created_at) FROM feed_articles a WHERE a.source_id = f.id AND a.parse_status = 'success') as last_article_at
        FROM feed_sources f
        WHERE f.enabled = 1
    ''', params)
    sources = [dict(r) for r in await cursor.fetchall()]
    
    cats_dict = {c["id"]: {"id": c["id"], "name": c["name"], "feeds": []} for c in categories}
    cats_dict[None] = {"id": None, "name": "Uncategorized", "feeds": []}
    
    total_unread = 0
    for s in sources:
        cat_id = s["category_id"]
        feed_data = {
            "id": s["id"],
            "name": s["name"],
            "url": s["url"],
            "site_url": resolve_site_url(s.get("site_url"), s["url"]),
            "desktop_view_mode": s["desktop_view_mode"],
            "mobile_view_mode": s["mobile_view_mode"],
            "favicon_url": f"/api/reader/favicon/{s['id']}",
            "unread_count": s["unread_count"],
            "total_count": s["total_count"],
            "last_article_at": s["last_article_at"] + "Z" if s["last_article_at"] and "Z" not in str(s["last_article_at"]) else s["last_article_at"]
        }
        total_unread += s["unread_count"]
        if cat_id in cats_dict:
            cats_dict[cat_id]["feeds"].append(feed_data)
        else:
            cats_dict[None]["feeds"].append(feed_data)
            
    result_cats = [c for c in cats_dict.values() if c["feeds"]]
    
    return {
        "categories": result_cats,
        "total_unread": total_unread
    }

@router.get("/api/reader/articles")
async def get_reader_articles(
    since: Optional[str] = None,
    source_id: Optional[int] = None,
    category_id: Optional[str] = None,
    sort: str = "smart",
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Returns article metadata for the feed list (no content — use /articles/{id} for full content)."""
    conn = await db._get_db()

    # Enforced here rather than in the reader: a tab cached from before the switch
    # was flipped still sends sort=smart.
    ai_on = _ai_enabled()
    if not ai_on and sort == "smart":
        sort = "latest"

    query = """
        SELECT a.id, a.source_id, f.name as source_name, a.url, a.title,
               a.snippet, a.featured_snippet,
               a.pub_date, a.main_image, a.is_read, a.is_saved, a.created_at, a.updated_at,
               a.primary_topic, a.secondary_topics, a.region, a.article_type, a.ai_summary,
               av.vote AS vote
        FROM feed_articles a
        JOIN feed_sources f ON a.source_id = f.id
        LEFT JOIN article_votes av ON av.article_id = a.id
        WHERE a.parse_status = 'success'
    """
    params = []

    if since:
        query += " AND a.updated_at >= ?"
        params.append(since)
    else:
        # Default view: show all unread articles OR read articles from the last 3 days
        three_days_ago = (utcnow() - timedelta(days=3)).isoformat()
        query += " AND (a.is_read = 0 OR a.created_at >= ?)"
        params.append(three_days_ago)

    if source_id:
        query += " AND a.source_id = ?"
        params.append(source_id)

    # 'uncat' is the reader's id for the synthetic Uncategorized bucket, which is
    # stored as a NULL category_id — it cannot be expressed as an equality match.
    if category_id == "uncat":
        query += " AND f.category_id IS NULL"
    elif category_id:
        try:
            params.append(int(category_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="category_id must be an integer or 'uncat'")
        query += " AND f.category_id = ?"

    query += " ORDER BY COALESCE(NULLIF(a.pub_date, ''), a.created_at) DESC, a.created_at DESC LIMIT ? OFFSET ?"
    params.append(limit)
    params.append(offset)

    cursor = await conn.execute(query, params)
    rows = await cursor.fetchall()

    # Rows parsed before the snippet columns existed still hold NULL. Fetch content for
    # only those, so the common case never pulls article HTML out of SQLite at all.
    missing = [r["id"] for r in rows if r["snippet"] is None or r["featured_snippet"] is None]
    fallback = {}
    if missing:
        placeholders = ",".join("?" * len(missing))
        cursor = await conn.execute(
            f"SELECT id, content FROM feed_articles WHERE id IN ({placeholders})", missing
        )
        for row in await cursor.fetchall():
            fallback[row["id"]] = build_snippets(row["content"])

    # One bulk lookup for focal points
    focal_map = await db.get_focal_points(
        [cached_image_hash_from_url(r["main_image"]) for r in rows]
    )

    # Bulk feed age percentiles and exposure for scoring
    source_ids = list({r["source_id"] for r in rows})
    perc_map = await db.get_feed_age_percentiles(source_ids) if (ai_on and source_ids) else {}
    exposure_map = await db.get_article_exposure([r["id"] for r in rows]) if ai_on else {}
    label_weights = await get_effective_weights(db_instance=db) if ai_on else None
    vote_settings = db.get_system_settings_sync()

    articles = []
    for r in rows:
        main_image_proxy = _build_reader_image_url(r["main_image"], width=800)
        focal_x, focal_y = focal_map.get(
            cached_image_hash_from_url(r["main_image"]) or "", (50, 50)
        )

        if r["snippet"] is None or r["featured_snippet"] is None:
            snippet, featured_snippet = fallback.get(r["id"], ("", ""))
        else:
            snippet = r["snippet"]
            featured_snippet = r["featured_snippet"]

        # Tags & scoring
        sec_raw = r["secondary_topics"]
        if isinstance(sec_raw, str):
            try:
                sec_list = json.loads(sec_raw)
            except Exception:
                sec_list = [sec_raw] if sec_raw else []
        elif isinstance(sec_raw, list):
            sec_list = sec_raw
        else:
            sec_list = []

        tags_dict = {
            "primary": r["primary_topic"],
            "secondary": sec_list,
            "region": r["region"],
            "type": r["article_type"]
        }

        if ai_on:
            art_perc = perc_map.get(r["source_id"], {}).get(r["id"], 0.5)
            score_val, reason = score_article(
                tags_dict, art_perc,
                weights=label_weights, vote=r["vote"], settings=vote_settings,
                exposure=exposure_map.get(r["id"]), is_saved=bool(r["is_saved"]))
        else:
            score_val, reason = None, ""

        articles.append({
            "id": r["id"],
            "source_id": r["source_id"],
            "source_name": r["source_name"],
            "source_favicon": f"/api/reader/favicon/{r['source_id']}",
            "url": r["url"],
            "title": r["title"] or "Untitled",
            "snippet": snippet,
            "featured_snippet": featured_snippet,
            "pub_date": r["pub_date"],
            "main_image": r["main_image"],
            "main_image_proxy": main_image_proxy,
            "focal_x": focal_x,
            "focal_y": focal_y,
            "vote": r["vote"],
            "is_read": r["is_read"],
            "is_saved": r["is_saved"],
            "created_at": r["created_at"] + "Z" if r["created_at"] and "Z" not in str(r["created_at"]) else r["created_at"],
            "updated_at": r["updated_at"] + "Z" if r["updated_at"] and "Z" not in str(r["updated_at"]) else r["updated_at"],
            "topics": tags_dict,
            "ai_summary": r["ai_summary"] or "",
            "recommendation_reason": reason,
            "score": score_val
        })

    # The SQL already returns newest-first, so "latest" needs no work here.
    if sort == "smart":
        articles = calibrated_rerank(articles, lambda_=0.3, label_weights=label_weights,
                                     floor=float(vote_settings.get("rerank_target_floor", 0.02)))
        # With the master switch off there are no scores, so every tail article looks
        # equally unseen and the slot would promote arbitrarily.
        if ai_on:
            articles = promote_exploration_slots(articles, exposure=exposure_map,
                                                 label_weights=label_weights)
    elif sort == "random":
        # Seeded per request so paging through offsets does not reshuffle underneath
        # the reader and show the same article twice.
        random.Random(f"{offset}:{limit}:{source_id}:{category_id}").shuffle(articles)

    return {
        "articles": articles,
        "sync_timestamp": utcnow().isoformat() + "Z"
    }

@router.get("/api/reader/articles/{id}")
async def get_reader_article(id: int):
    """Returns full article content for the reader pane."""
    conn = await db._get_db()
    cursor = await conn.execute("""
        SELECT a.id, a.source_id, f.name as source_name, a.url, a.title, a.content, a.pub_date, a.main_image, a.is_read, a.is_saved, a.created_at, a.updated_at,
               a.primary_topic, a.secondary_topics, a.region, a.article_type, a.ai_summary,
               av.vote AS vote
        FROM feed_articles a
        JOIN feed_sources f ON a.source_id = f.id
        LEFT JOIN article_votes av ON av.article_id = a.id
        WHERE a.id = ?
    """, (id,))
    r = await cursor.fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="Article not found")

    content = _rewrite_content_image_sources(r["content"] or "", width=800, base_url=r["url"] or "")
    main_image_proxy = _build_reader_image_url(
        absolutize_image_url(r["main_image"], r["url"] or "") or r["main_image"], width=800
    )

    # The list endpoint returns these; this one did not, so opening an article showed
    # "unknown" in the info panel even when the article was tagged hours earlier.
    try:
        sec_list = json.loads(r["secondary_topics"]) if r["secondary_topics"] else []
        if not isinstance(sec_list, list):
            sec_list = []
    except (TypeError, ValueError):
        sec_list = []
    tags_dict = {
        "primary": r["primary_topic"],
        "secondary": sec_list,
        "region": r["region"],
        "type": r["article_type"],
    }
    if _ai_enabled():
        perc = await db.get_feed_age_percentiles([r["source_id"]])
        exposure_map = await db.get_article_exposure([r["id"]])
        score_val, reason = score_article(
            tags_dict, perc.get(r["source_id"], {}).get(r["id"], 0.5),
            weights=await get_effective_weights(db_instance=db),
            vote=r["vote"],
            settings=db.get_system_settings_sync(),
            exposure=exposure_map.get(r["id"]),
            is_saved=bool(r["is_saved"]),
        )
    else:
        score_val, reason = None, ""

    return {
        "id": r["id"],
        "source_id": r["source_id"],
        "source_name": r["source_name"],
        "source_favicon": f"/api/reader/favicon/{r['source_id']}",
        "url": r["url"],
        "title": r["title"] or "Untitled",
        "content": content,
        "pub_date": r["pub_date"],
        "main_image": r["main_image"],
        "main_image_proxy": main_image_proxy,
        "vote": r["vote"],
        "is_read": r["is_read"],
        "is_saved": r["is_saved"],
        "created_at": r["created_at"] + "Z" if r["created_at"] and "Z" not in str(r["created_at"]) else r["created_at"],
        "updated_at": r["updated_at"] + "Z" if r["updated_at"] and "Z" not in str(r["updated_at"]) else r["updated_at"],
        "topics": tags_dict,
        "ai_summary": r["ai_summary"] or "",
        "recommendation_reason": reason,
        "score": score_val
    }

@router.get("/api/reader/article-images/{id}")
async def get_article_images(id: int):
    """Returns a list of all <img> URLs found in the article's content HTML."""
    conn = await db._get_db()
    cursor = await conn.execute("SELECT content, url FROM feed_articles WHERE id = ?", (id,))
    row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Article not found")

    # Must mirror what the reader renders, or offline pre-caching fetches a
    # different set of URLs than the article ends up referencing.
    content = _rewrite_content_image_sources(row["content"] or "", width=800, base_url=row["url"] or "")
    soup = BeautifulSoup(content, "html.parser")

    images = []
    image_hashes = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and (src.startswith("http") or src.startswith("/api/reader/")):
            proxy_url = src if src.startswith("/api/reader/") else _build_reader_image_url(src, width=800)
            hash_val = cached_image_hash_from_url(src) or cached_image_hash_from_url(proxy_url)
            image_hashes.append(hash_val)
            images.append({
                "original": src,
                "proxy": proxy_url,
            })

    focal_map = await db.get_focal_points([h for h in image_hashes if h])
    for img_obj, h in zip(images, image_hashes):
        fx, fy = focal_map.get(h or "", (50, 50))
        img_obj["focal_x"] = fx
        img_obj["focal_y"] = fy

    return {"images": images}

@router.post("/api/reader/articles/{id}/read")
async def mark_article_read(id: int):
    await db.mark_item_read(id)
    return {"success": True}

@router.post("/api/reader/articles/{id}/unread")
async def mark_article_unread(id: int):
    conn = await db._get_db()
    await conn.execute('UPDATE feed_articles SET is_read = 0 WHERE id = ?', (id,))
    await conn.commit()
    return {"success": True}


class VoteRequest(BaseModel):
    vote: Optional[str] = None
    mark_read: bool = True


VALID_VOTES = ("show_more", "show_less")


@router.post("/api/reader/articles/{id}/vote")
async def set_article_vote(id: int, req: VoteRequest):
    conn = await db._get_db()
    ok, error_reason, _ = await record_article_vote(
        id, req.vote, mark_read=req.mark_read, conn=conn, db_instance=db
    )
    if not ok:
        if error_reason == "Invalid vote value":
            raise HTTPException(status_code=400, detail="vote must be show_more, show_less or null")
        elif error_reason == "Article not found":
            raise HTTPException(status_code=404, detail="Article not found")
        else:
            raise HTTPException(status_code=400, detail=error_reason or "Vote failed")

    # Clearing a vote does not un-read the article: the read happened, and reporting
    # is_read 0 here would make the client put a finished card back on the page.
    cursor = await conn.execute("SELECT is_read FROM feed_articles WHERE id = ?", (id,))
    read_row = await cursor.fetchone()
    return {"success": True, "vote": req.vote,
            "is_read": int(read_row["is_read"] or 0) if read_row else 0}


@router.get("/api/reader/label-weights")
async def get_label_weights(days: int = 7):
    """The dashboard table: one row per label, plus the exploration-floor coverage check.

    Shape matters here - the dashboard reads `axes[axis]` as a list, so returning the
    raw nested dict from get_effective_weights renders an empty table.

    `impressions` is the spec 3.7 check: a label with a non-zero target floor and no
    impressions over the window means the floor is not doing its job, and the label
    cannot be voted back up because it is never shown. Only the topic axis has
    impression data - user_article_events records primary_topic, not type or region.
    """
    weights = await get_effective_weights(db_instance=db)
    window_days = max(1, min(365, days))
    since = (utcnow() - timedelta(days=window_days)).strftime("%Y-%m-%d %H:%M:%S")
    impressions = await db.get_tag_impression_counts(since=since)

    # Secondary weights are keyed by canonical label, which is lowercased and sometimes
    # ungrammatical, so the table shows the commonest real spelling instead.
    display = await db.get_secondary_display_names()

    axes = {}
    for axis, labels in (weights or {}).items():
        rows = []
        for label, entry in labels.items():
            row = {"label": label, **entry}
            if axis == "secondary":
                row["label"] = display.get(label, label)
                row["canonical"] = label
            if axis == "topic":
                seen = int(impressions.get(label, 0))
                row["impressions"] = seen
                # Only meaningful once impressions are flowing at all; on a cold
                # database every label would look starved.
                row["unseen"] = seen == 0 and bool(impressions)
            rows.append(row)
        # Biggest mover first - the question the table answers is "what did my voting do".
        rows.sort(key=lambda r: (-abs(r["explicit"] + r["behavioural"]), r["label"]))
        axes[axis] = rows

    return {"axes": axes, "impressions": impressions,
            "impression_window_days": window_days}


@router.get("/api/reader/articles/{id}/score-breakdown")
async def get_article_score_breakdown(id: int):
    conn = await db._get_db()
    cursor = await conn.execute("""
        SELECT a.id, a.source_id, a.primary_topic, a.secondary_topics,
               a.region, a.article_type, a.is_saved, av.vote AS vote
        FROM feed_articles a
        LEFT JOIN article_votes av ON av.article_id = a.id
        WHERE a.id = ?
    """, (id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Article not found")

    try:
        sec_list = json.loads(row["secondary_topics"]) if row["secondary_topics"] else []
        if not isinstance(sec_list, list):
            sec_list = []
    except (TypeError, ValueError):
        sec_list = []

    tags_dict = {
        "primary": row["primary_topic"],
        "secondary": sec_list,
        "region": row["region"],
        "type": row["article_type"],
    }

    perc = await db.get_feed_age_percentiles([row["source_id"]])
    art_perc = perc.get(row["source_id"], {}).get(row["id"], 0.5)
    exposure_map = await db.get_article_exposure([row["id"]]) if _ai_enabled() else {}

    # Same gate as the list and detail endpoints: with the master switch off the
    # breakdown must explain the declared table, not weights votes have moved.
    res = score_article_breakdown(
        tags_dict, art_perc,
        weights=await get_effective_weights(db_instance=db) if _ai_enabled() else None,
        vote=row["vote"],
        settings=db.get_system_settings_sync(),
        exposure=exposure_map.get(row["id"]),
        is_saved=bool(row["is_saved"]),
    )
    res["article_id"] = id
    res["score"] = res["total"]
    for t in res["terms"]:
        if t.get("kind") == "axis":
            t["term"] = f"{t['axis']}:{t['label']}"
        elif t.get("kind") == "freshness":
            t["term"] = "freshness"
        else:
            t["term"] = t.get("label", "")
    res["rows"] = res["terms"]
    return res

@router.post("/api/reader/mark-read")
async def bulk_mark_read(req: BulkMarkReadRequest):
    conn = await db._get_db()
    
    if req.article_ids:
        placeholders = ','.join(['?'] * len(req.article_ids))
        await conn.execute(f"UPDATE feed_articles SET is_read = 1 WHERE id IN ({placeholders})", req.article_ids)
        await conn.commit()
        return {"success": True, "count": len(req.article_ids)}
        
    elif req.source_id:
        cursor = await conn.execute("UPDATE feed_articles SET is_read = 1 WHERE source_id = ? AND is_read = 0", (req.source_id,))
        await conn.commit()
        return {"success": True, "count": cursor.rowcount}
        
    elif req.category_id:
        cursor = await conn.execute("""
            UPDATE feed_articles SET is_read = 1 
            WHERE source_id IN (SELECT id FROM feed_sources WHERE category_id = ?) AND is_read = 0
        """, (req.category_id,))
        await conn.commit()
        return {"success": True, "count": cursor.rowcount}
        
    elif req.before:
        cursor = await conn.execute("UPDATE feed_articles SET is_read = 1 WHERE created_at <= ? AND is_read = 0", (req.before,))
        await conn.commit()
        return {"success": True, "count": cursor.rowcount}
        
    elif req.mark_all:
        cursor = await conn.execute("UPDATE feed_articles SET is_read = 1 WHERE is_read = 0")
        await conn.commit()
        return {"success": True, "count": cursor.rowcount}
        
    return {"success": False, "message": "No valid parameters provided"}

@router.get("/api/reader/image-proxy")
async def image_proxy(url: str, w: int = 800):
    """Fetches an image, resizes to max width, compresses to JPEG 60, and caches it."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")

    # Bound the key space to allowed widths
    w = min(ALLOWED_IMAGE_PROXY_WIDTHS, key=lambda allowed: abs(allowed - w))

    url_hash = hashlib.md5(f"{url}_{w}".encode()).hexdigest()
    cache_path = os.path.join(IMAGE_CACHE_DIR, f"{url_hash}.jpg")

    if os.path.exists(cache_path):
        return FileResponse(
            cache_path,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=604800",
                "Access-Control-Allow-Origin": "*",
                "Vary": "Origin",
            },
        )

    try:
        current_url = url
        proxy_headers = HEADERS.copy()
        resp_content = None
        max_redirects = 5

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            for _ in range(max_redirects + 1):
                if not _validate_url_ssrf(current_url):
                    raise ValueError(f"Blocked unsafe URL or IP: {current_url}")

                parsed_url = urlparse(current_url)
                proxy_headers["Referer"] = f"{parsed_url.scheme}://{parsed_url.netloc}/"
                resp_status = None

                async with client.stream("GET", current_url, headers=proxy_headers) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            resp.raise_for_status()
                        current_url = urllib.parse.urljoin(current_url, location)
                        continue

                    if resp.status_code in (403, 503):
                        resp_status = resp.status_code
                    else:
                        resp.raise_for_status()
                        cl = resp.headers.get("content-length")
                        if cl and int(cl) > MAX_IMAGE_PROXY_BYTES:
                            raise ValueError("Image Content-Length exceeds limit")
                        chunks = []
                        total_bytes = 0
                        async for chunk in resp.aiter_bytes():
                            total_bytes += len(chunk)
                            if total_bytes > MAX_IMAGE_PROXY_BYTES:
                                raise ValueError("Image response size exceeds limit")
                            chunks.append(chunk)
                        resp_content = b"".join(chunks)
                        break

                if resp_status in (403, 503):
                    solution = await asyncio.to_thread(get_flaresolverr_solution, current_url, 45000)
                    if solution:
                        retry_headers = proxy_headers.copy()
                        user_agent = solution.get("userAgent")
                        if user_agent:
                            retry_headers["User-Agent"] = user_agent
                        cookie_jar = {
                            c.get("name"): c.get("value")
                            for c in (solution.get("cookies") or [])
                            if c.get("name") and c.get("value") is not None
                        }
                        async with client.stream("GET", current_url, headers=retry_headers, cookies=cookie_jar) as retry_resp:
                            retry_resp.raise_for_status()
                            cl = retry_resp.headers.get("content-length")
                            if cl and int(cl) > MAX_IMAGE_PROXY_BYTES:
                                raise ValueError("Image Content-Length exceeds limit")
                            chunks = []
                            total_bytes = 0
                            async for chunk in retry_resp.aiter_bytes():
                                total_bytes += len(chunk)
                                if total_bytes > MAX_IMAGE_PROXY_BYTES:
                                    raise ValueError("Image response size exceeds limit")
                                chunks.append(chunk)
                            resp_content = b"".join(chunks)
                        break
                    else:
                        raise httpx.HTTPStatusError(f"HTTP {resp_status}", request=None, response=None)

        if not resp_content:
            raise ValueError("Empty image response")

        img = Image.open(io.BytesIO(resp_content))

        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[3] if len(img.split()) > 3 else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        if img.width > w:
            ratio = w / float(img.width)
            h = int(float(img.height) * float(ratio))
            img = img.resize((w, h), Image.Resampling.LANCZOS)

        out_io = io.BytesIO()
        img.save(out_io, format="JPEG", quality=60)
        out_bytes = out_io.getvalue()

        await asyncio.to_thread(Path(cache_path).write_bytes, out_bytes)

        return Response(
            content=out_bytes,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=604800",
                "Access-Control-Allow-Origin": "*",
                "Vary": "Origin",
            },
        )

    except Exception as e:
        logger.warning(f"Failed to proxy image for url={url}: {e}")
        return Response(status_code=404)


@router.get("/api/reader/cached-image/{filename}")
async def get_cached_article_image(filename: str):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        raise HTTPException(status_code=404, detail="Image not found")

    cache_path = os.path.join(ARTICLE_IMAGE_CACHE_DIR, safe_name)
    if not os.path.exists(cache_path):
        raise HTTPException(status_code=404, detail="Image not found")

    media_type = "image/jpeg"
    if safe_name.endswith(".gif"):
        media_type = "image/gif"
    elif safe_name.endswith(".png"):
        media_type = "image/png"
    elif safe_name.endswith(".webp"):
        media_type = "image/webp"

    return FileResponse(
        cache_path,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=604800",
            "Access-Control-Allow-Origin": "*",
            "Vary": "Origin",
        },
    )

@router.get("/api/reader/favicon/{source_id}")
async def get_favicon(source_id: int):
    """Fetches and caches a 32x32 favicon for a source."""
    source = await db.get_feed_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    cache_path = os.path.join(FAVICON_CACHE_DIR, f"{source_id}.png")

    if os.path.exists(cache_path):
        return FileResponse(
            cache_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    # Resolve the real site URL (feed host may differ from the website).
    parsed = urlparse(resolve_site_url(source.get('site_url'), source['url']))
    domain = parsed.netloc

    # Try sources in order: Google favicon service, then /favicon.ico
    favicon_urls = [
        f"https://www.google.com/s2/favicons?domain={domain}&sz=32",
        f"{parsed.scheme}://{domain}/favicon.ico",
    ]

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for favicon_url in favicon_urls:
                try:
                    resp = await client.get(favicon_url)
                    if resp.status_code != 200 or len(resp.content) < 100:
                        continue

                    img = Image.open(io.BytesIO(resp.content))
                    if img.mode != 'RGBA':
                        img = img.convert('RGBA')
                    img = img.resize((32, 32), Image.Resampling.LANCZOS)

                    out_io = io.BytesIO()
                    img.save(out_io, format="PNG")
                    out_bytes = out_io.getvalue()

                    await asyncio.to_thread(Path(cache_path).write_bytes, out_bytes)

                    return Response(content=out_bytes, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})
                except Exception:
                    continue

    except Exception:
        pass

    # Fallback: gray placeholder
    img = Image.new('RGBA', (32, 32), color=(200, 200, 200, 255))
    out_io = io.BytesIO()
    img.save(out_io, format="PNG")
    out_bytes = out_io.getvalue()
    return Response(content=out_bytes, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


class FocalPointsRequest(BaseModel):
    api_key: str | None = None
    hashes: list[str | None] | None = []


@router.post("/api/reader/focal-points")
async def get_focal_points(req: FocalPointsRequest):
    # Key comes from the body only. In a query string it lands in access logs and in
    # any proxy in front of this; ranking/scores accepts one there only because it is a GET.
    if not await validate_api_key(req.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    raw_hashes = req.hashes or []
    if len(raw_hashes) > 1000:
        raise HTTPException(status_code=400, detail="Cannot request more than 1000 hashes at once")

    clean_hashes = [h for h in raw_hashes if h and isinstance(h, str)]
    if not clean_hashes:
        return {"focal": {}}

    found = await db.get_focal_points(clean_hashes)
    return {"focal": {k: [v[0], v[1]] for k, v in found.items()}}

