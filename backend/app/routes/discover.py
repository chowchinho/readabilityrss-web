from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import re
from ..services.link_discovery import LinkDiscovery
from ..utils.fetch import fetch_html_async
from ..utils.url_safety import validate_url_ssrf

router = APIRouter(prefix="/api", tags=["discovery"])
discovery_service = LinkDiscovery()


def _detect_language(html: str) -> str:
    """Detect language from HTML lang attribute, meta tag, or RSS/Atom language element."""
    # HTML: <meta http-equiv="content-language" content="ja">
    match = re.search(
        r'<meta\s+http-equiv=["\']content-language["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().lower()
    # HTML: <html lang="ja">
    match = re.search(r'<html[^>]*\s+lang=["\']?([^\s"\'>]+)', html, re.IGNORECASE)
    if match:
        return match.group(1).strip().lower()
    # RSS 2.0: <language>ja</language> inside <channel>
    match = re.search(r'<language>\s*([^<]+?)\s*</language>', html, re.IGNORECASE)
    if match:
        return match.group(1).strip().lower()
    # Atom / XML: xml:lang="ja"
    match = re.search(r'xml:lang=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1).strip().lower()
    return "en"

class DiscoverRequest(BaseModel):
    url: str

class DiscoverWithSelectorRequest(BaseModel):
    url: str
    item_selector: str
    link_selector: str = "a"
    exclude_selector: str = None

async def fetch_html(url: str) -> str:
    if not validate_url_ssrf(url):
        raise HTTPException(status_code=400, detail="Invalid URL: must start with http:// or https:// and target a safe public host")
    try:
        return await fetch_html_async(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {str(e)}")

@router.post("/discover-links")
async def discover_links(req: DiscoverRequest):
    html = await fetch_html(req.url)
    result = discovery_service.discover(html, req.url)
    result['language'] = _detect_language(html)
    return result

@router.post("/discover-links-with-selector")
async def discover_links_with_selector(req: DiscoverWithSelectorRequest):
    html = await fetch_html(req.url)
    result = discovery_service.discover_with_selector(
        html, req.url, req.item_selector, req.link_selector, req.exclude_selector
    )
    result['language'] = _detect_language(html)
    return result
