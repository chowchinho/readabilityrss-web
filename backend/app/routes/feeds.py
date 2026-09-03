"""RSS 2.0 feed and OPML export endpoints."""
import os
import mimetypes
from fastapi import APIRouter, HTTPException, Request, Response
from email.utils import formatdate
from datetime import datetime
import xml.etree.ElementTree as ET
from ..database import db
from ..services.article_image_cache import build_absolute_asset_url

PUBLIC_URL = os.getenv("PUBLIC_URL", "")

router = APIRouter(tags=["feeds"])


async def _article_limit() -> int:
    """Serve as many articles as the configured per-feed cap stores."""
    settings = await db.get_system_settings()
    return int(settings.get("max_articles_per_feed", 50))

# Register namespaces so ET uses 'content:' prefix instead of 'ns0:'
ET.register_namespace('content', 'http://purl.org/rss/1.0/modules/content/')
ET.register_namespace('atom', 'http://www.w3.org/2005/Atom')


def _to_rfc2822(date_str: str) -> str:
    """Convert ISO date (YYYY-MM-DD) or timestamp to RFC 2822 format."""
    if not date_str:
        return formatdate(usegmt=True)
    try:
        if 'T' in date_str or ' ' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
        return formatdate(dt.timestamp(), usegmt=True)
    except (ValueError, TypeError):
        return formatdate(usegmt=True)


@router.get("/api/activity-log")
async def get_activity_log(since: int = 0):
    """Poll for new activity log entries since a given ID."""
    from ..services.scheduler import get_activity_log
    entries = get_activity_log(since_id=since)
    return {"entries": entries}


@router.get("/api/refresh-status")
async def get_refresh_status():
    """Return current refresh state for dashboard polling."""
    from ..services.scheduler import is_refreshing, get_current_source_id, get_parse_progress, get_queued_source_ids
    progress = get_parse_progress()
    return {
        "refreshing": is_refreshing(),
        "current_source_id": get_current_source_id(),
        "parse_done": progress["done"],
        "parse_total": progress["total"],
        "queued_source_ids": get_queued_source_ids(),
    }


@router.get("/feed/opml")
async def export_opml(request: Request):
    """Export all feed sources as OPML 2.0 for Miniflux import."""
    sources = await db.get_feed_sources()
    # Exclude disabled feeds from OPML export
    sources = [s for s in sources if s.get('enabled', 1)]

    root = ET.Element('opml', version='2.0')
    head = ET.SubElement(root, 'head')
    ET.SubElement(head, 'title').text = 'ReadabilityRSS Feeds'
    ET.SubElement(head, 'dateCreated').text = formatdate(usegmt=True)

    body = ET.SubElement(root, 'body')

    # Group by category
    categories = {}
    for source in sources:
        cat = source.get('category_name') or 'Uncategorized'
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(source)

    base = PUBLIC_URL or str(request.base_url).rstrip('/')
    for cat_name, cat_sources in categories.items():
        cat_outline = ET.SubElement(body, 'outline', text=cat_name, title=cat_name)
        for source in cat_sources:
            feed_url = f"{base}/feed/{source['id']}/rss"
            ET.SubElement(cat_outline, 'outline',
                          type='rss',
                          text=source['name'],
                          title=source['name'],
                          xmlUrl=feed_url,
                          htmlUrl=source['url'])

    xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding='unicode')
    return Response(
        content=xml_str,
        media_type='text/x-opml; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="readabilityrss.opml"'}
    )


@router.get("/api/feed-sources/{source_id}/articles")
async def get_source_articles(source_id: int):
    """Return parsed articles for a feed source (for dashboard preview)."""
    source = await db.get_feed_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")
    articles = await db.get_feed_articles(source_id, limit=await _article_limit())
    return {"articles": [
        {
            "id": a["id"],
            "url": a["url"],
            "title": a.get("title") or "Untitled",
            "content": a.get("content", ""),
            "pub_date": a.get("pub_date", ""),
            "main_image": a.get("main_image", ""),
        }
        for a in articles
    ]}


async def _run_generate(source):
    """Run generate for one source, then drain the queue."""
    import asyncio
    from ..services.scheduler import _refresh_source, set_refreshing, set_current_source_id, dequeue_generate
    set_refreshing(True)
    try:
        await _refresh_source(source)
    finally:
        set_current_source_id(None)
        set_refreshing(False)
        next_src = dequeue_generate()
        if next_src:
            asyncio.create_task(_run_generate(next_src))


@router.post("/api/feed-sources/{source_id}/generate")
async def generate_feed(source_id: int):
    """Manually trigger feed generation for a single source (runs in background)."""
    import asyncio
    from ..services.scheduler import is_refreshing, enqueue_generate

    source = await db.get_feed_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")

    if is_refreshing():
        queued = enqueue_generate(source)
        msg = f"{source['name']} queued" if queued else f"{source['name']} already queued"
        return {"success": True, "queued": True, "message": msg}

    asyncio.create_task(_run_generate(source))
    return {"success": True, "queued": False, "message": f"Generate started for {source['name']}"}


@router.post("/api/feed-sources/refresh-all")
async def refresh_all_feeds():
    """Manually trigger feed generation for all sources (runs in background)."""
    import asyncio
    from ..services.scheduler import _refresh_source, _log_event, is_refreshing, set_refreshing

    if is_refreshing():
        return {"success": False, "message": "Refresh already in progress"}

    sources = await db.get_feed_sources()
    # Only refresh enabled feeds
    sources = [s for s in sources if s.get('enabled', 1)]

    async def _bg_refresh():
        set_refreshing(True)
        try:
            _log_event("info", "SCHEDULER", f"Manual refresh started — {len(sources)} sources")
            for source in sources:
                try:
                    await _refresh_source(source)
                except Exception as e:
                    _log_event("error", source.get("name", "SOURCE"), f"Manual refresh failed: {str(e)[:150]}")
            _log_event("ok", "SCHEDULER", f"Manual refresh complete — {len(sources)} sources processed")
        finally:
            set_refreshing(False)

    asyncio.create_task(_bg_refresh())
    return {"success": True, "message": f"Refresh started for {len(sources)} sources"}


@router.get("/feed/{source_id}/rss")
async def get_rss_feed(source_id: int, request: Request):
    """Generate RSS 2.0 feed for a feed source."""
    source = await db.get_feed_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")

    articles = await db.get_feed_articles(source_id, limit=await _article_limit())

    # Build RSS 2.0 XML (namespace registered at module level via ET.register_namespace)
    rss = ET.Element('rss', version='2.0')

    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = source['name']
    ET.SubElement(channel, 'link').text = source['url']
    ET.SubElement(channel, 'description').text = f"RSS feed for {source['name']} via ReadabilityRSS"
    ET.SubElement(channel, 'lastBuildDate').text = formatdate(usegmt=True)

    base = PUBLIC_URL or str(request.base_url).rstrip('/')
    self_url = f"{base}/feed/{source_id}/rss"
    atom_link = ET.SubElement(channel, 'atom:link')
    atom_link.set('xmlns:atom', 'http://www.w3.org/2005/Atom')
    atom_link.set('href', self_url)
    atom_link.set('rel', 'self')
    atom_link.set('type', 'application/rss+xml')

    for article in articles:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = article.get('title') or 'Untitled'
        ET.SubElement(item, 'link').text = article['url']
        ET.SubElement(item, 'guid').text = article['url']
        ET.SubElement(item, 'pubDate').text = _to_rfc2822(article.get('pub_date', ''))

        # content:encoded with full HTML
        content_el = ET.SubElement(item, '{http://purl.org/rss/1.0/modules/content/}encoded')
        content_el.text = article.get('content', '')

        # Enclosure for main image
        if article.get('main_image'):
            enclosure_url = build_absolute_asset_url(base, article['main_image'])
            enclosure_type = mimetypes.guess_type(enclosure_url)[0] or 'image/jpeg'
            ET.SubElement(item, 'enclosure',
                          url=enclosure_url,
                          type=enclosure_type,
                          length='0')

    xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(rss, encoding='unicode')
    return Response(content=xml_str, media_type='application/rss+xml; charset=utf-8')
