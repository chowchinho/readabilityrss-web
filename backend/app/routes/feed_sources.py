from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..database import db
from .discover import fetch_html
from ..services.link_discovery import LinkDiscovery
from ..services.article_image_cache import release_cached_images
import json
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feed-sources", tags=["feed_sources"])
discovery_service = LinkDiscovery()

class FeedSourceCreate(BaseModel):
    category_id: int
    name: str
    url: str
    item_selector: Optional[str] = None
    link_selector: Optional[str] = None
    excluded_urls: Optional[list[str]] = None
    detected_language: Optional[str] = None
    translate_to: Optional[str] = None
    content_selector: Optional[str] = None
    title_selector: Optional[str] = None
    date_selector: Optional[str] = None
    image_selector: Optional[str] = None
    content_exclude_selector: Optional[str] = None
    negative_keywords: Optional[str] = None
    desktop_view_mode: Optional[str] = 'standard'
    mobile_view_mode: Optional[str] = 'standard'
    translator: Optional[str] = 'google'
    use_parse_date: Optional[bool] = False

class FeedSourceUpdate(BaseModel):
    category_id: int
    name: str
    url: str
    item_selector: Optional[str] = None
    link_selector: Optional[str] = None
    excluded_urls: Optional[list[str]] = None
    detected_language: Optional[str] = None
    translate_to: Optional[str] = None
    content_selector: Optional[str] = None
    title_selector: Optional[str] = None
    date_selector: Optional[str] = None
    image_selector: Optional[str] = None
    content_exclude_selector: Optional[str] = None
    negative_keywords: Optional[str] = None
    desktop_view_mode: Optional[str] = 'standard'
    mobile_view_mode: Optional[str] = 'standard'
    translator: Optional[str] = 'google'
    use_parse_date: Optional[bool] = False

async def _check_source_status(source: dict, html: Optional[str] = None):
    url = source['url']
    item_selector = source.get('item_selector')
    link_selector = source.get('link_selector', 'a')
    
    start_time = time.time()
    try:
        if html is None:
            html = await fetch_html(url)
        fetch_time = time.time() - start_time
        
        if item_selector and not item_selector.startswith('item > link') and not item_selector.startswith('entry > link'):
            result = discovery_service.discover_with_selector(html, url, item_selector, link_selector)
        else:
            result = discovery_service.discover(html, url)
            
        links = result.get('links', [])

        # Apply exclusion patterns if stored
        exclusion_json = source.get('exclusion_patterns')
        if exclusion_json:
            patterns = json.loads(exclusion_json)
            links = discovery_service.apply_exclusion_patterns(links, patterns)

        links_count = len(links)
        
        # Determine status
        status = 'red'
        error = None
        has_images = True # Assume true for now as we don't fetch individual pages
        
        if links_count == 0:
            status = 'red'
            error = 'No links found matching selectors'
        else:
            prev_count = source.get('last_fetch_links_count') or 0
            if fetch_time > 5 or (prev_count > 0 and links_count < prev_count * 0.5):
                status = 'yellow'
            else:
                status = 'green'
                
        update_data = {
            'status': status,
            'last_fetch_at': 'CURRENT_TIMESTAMP',
            'last_fetch_links_count': links_count,
            'last_fetch_has_images': has_images,
            'last_error': error
        }
        await db.update_feed_source(source['id'], update_data)
        return update_data
        
    except Exception as e:
        update_data = {
            'status': 'red',
            'last_fetch_at': 'CURRENT_TIMESTAMP',
            'last_fetch_links_count': 0,
            'last_fetch_has_images': False,
            'last_error': str(e)
        }
        await db.update_feed_source(source['id'], update_data)
        return update_data

@router.get("")
async def list_feed_sources():
    return await db.get_feed_sources()

@router.post("")
async def create_feed_source(source: FeedSourceCreate):
    try:
        excluded_urls = source.excluded_urls
        data = source.model_dump(exclude={'excluded_urls'})

        # Derive exclusion patterns from excluded URLs if possible
        fetched_html = None
        if excluded_urls:
            try:
                fetched_html = await fetch_html(source.url)
                if source.item_selector and not source.item_selector.startswith(('item > link', 'entry > link')):
                    result = discovery_service.discover_with_selector(fetched_html, source.url, source.item_selector, source.link_selector or 'a')
                else:
                    result = discovery_service.discover(fetched_html, source.url)

                all_urls = [l['url'] for l in result.get('links', [])]
                included_urls = [u for u in all_urls if u not in set(excluded_urls)]
                patterns = discovery_service.derive_exclusion_patterns(source.url, included_urls, excluded_urls)
                if patterns:
                    data['exclusion_patterns'] = json.dumps(patterns)
            except Exception:
                pass

        id = await db.create_feed_source(data)

        # Run status check
        source_data = await db.get_feed_source(id)
        if source_data:
            try:
                await _check_source_status(source_data, html=fetched_html)
            except Exception:
                pass

        return {"id": id}
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create feed source")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{id}")
async def update_feed_source(id: int, source: FeedSourceUpdate):
    try:
        # Capture existing selectors before update to detect changes
        existing = await db.get_feed_source(id)

        excluded_urls = source.excluded_urls
        data = source.model_dump(exclude={'excluded_urls'})

        # Derive exclusion patterns from excluded URLs
        fetched_html = None
        if excluded_urls is not None:
            if excluded_urls:
                try:
                    fetched_html = await fetch_html(source.url)
                    if source.item_selector and not source.item_selector.startswith(('item > link', 'entry > link')):
                        result = discovery_service.discover_with_selector(fetched_html, source.url, source.item_selector, source.link_selector or 'a')
                    else:
                        result = discovery_service.discover(fetched_html, source.url)

                    all_urls = [l['url'] for l in result.get('links', [])]
                    included_urls = [u for u in all_urls if u not in set(excluded_urls)]
                    patterns = discovery_service.derive_exclusion_patterns(source.url, included_urls, excluded_urls)
                    data['exclusion_patterns'] = json.dumps(patterns) if patterns else None
                except Exception:
                    pass
            else:
                # Empty excluded_urls list means clear all patterns
                data['exclusion_patterns'] = None

        await db.update_feed_source(id, data)

        # Check if any CSS selector changed
        selector_fields = ['content_selector', 'title_selector', 'date_selector', 'image_selector']
        selectors_changed = existing and any(
            existing.get(f) != getattr(source, f)
            for f in selector_fields
        )

        # Re-check status after update
        source_data = await db.get_feed_source(id)
        if source_data:
            try:
                await _check_source_status(source_data, html=fetched_html)
            except Exception:
                pass

            if selectors_changed:
                # Reset all existing articles so they re-parse with the new selectors
                await db.reset_all_articles(id)
                from ..services.scheduler import _refresh_source
                await _refresh_source(source_data)

        return {"success": True}
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update feed source {id}")
        raise HTTPException(status_code=500, detail="Internal server error")

class FeedSourcePatch(BaseModel):
    category_id: Optional[int] = None
    name: Optional[str] = None
    url: Optional[str] = None
    desktop_view_mode: Optional[str] = None
    mobile_view_mode: Optional[str] = None
    enabled: Optional[bool] = None

@router.patch("/{id}")
async def patch_feed_source(id: int, source: FeedSourcePatch):
    try:
        data = source.model_dump(exclude_unset=True)
        if not data:
            return {"success": True, "message": "No changes provided"}
            
        await db.update_feed_source(id, data)
        return {"success": True}
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to patch feed source {id}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/{id}/toggle-enabled")
async def toggle_feed_source_enabled(id: int):
    try:
        source = await db.get_feed_source(id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        new_enabled = await db.toggle_feed_source_enabled(id)
        return {"success": True, "enabled": new_enabled}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to toggle feed source {id}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/{id}")
async def delete_feed_source(id: int):
    try:
        articles = await db.get_all_source_articles(id)
        await db.delete_feed_source(id)
        await release_cached_images(articles)
        return {"success": True}
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to delete feed source {id}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/{id}/flush")
async def flush_feed_source(id: int):
    try:
        source = await db.get_feed_source(id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        articles = await db.get_all_source_articles(id)
        conn = await db._get_db()
        await conn.execute("DELETE FROM feed_articles WHERE source_id = ?", (id,))
        await conn.execute("UPDATE feed_sources SET feed_article_count = 0 WHERE id = ?", (id,))
        await conn.commit()
        await release_cached_images(articles)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to flush feed source {id}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/{id}/check")
async def check_feed_source(id: int):
    try:
        source = await db.get_feed_source(id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
            
        result = await _check_source_status(source)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to check feed source {id}")
        raise HTTPException(status_code=500, detail="Internal server error")
