"""API route for parsing URLs and extracting RSS fields."""
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import requests
import httpx
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from ..services.parser import ReadabilityParser, parse_with_node_unfluff
from ..utils.fetch import fetch_html_async, fetch_html_rendered_async
from ..utils.url_safety import validate_url_ssrf

router = APIRouter(prefix="/api", tags=["parsing"])
parser = ReadabilityParser()


def fix_relative_urls(html: str, base_url: str) -> str:
    """
    Convert relative URLs in HTML to absolute URLs using the base URL.

    Args:
        html: HTML content with potentially relative URLs
        base_url: The original URL to use as base for resolving relative URLs

    Returns:
        HTML with all relative URLs converted to absolute URLs
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Fix <img src> attributes
    for img in soup.find_all('img'):
        if img.get('src'):
            img['src'] = urljoin(base_url, img['src'])
        if img.get('srcset'):
            # Handle srcset format: "url1 1x, url2 2x"
            srcset_items = img['srcset'].split(',')
            fixed_srcset = []
            for item in srcset_items:
                parts = item.strip().split()
                url = parts[0]
                rest = ' '.join(parts[1:]) if len(parts) > 1 else ''
                absolute_url = urljoin(base_url, url)
                fixed_srcset.append(f"{absolute_url} {rest}".strip())
            img['srcset'] = ', '.join(fixed_srcset)

    # Fix <link href> attributes (stylesheets)
    for link in soup.find_all('link'):
        if link.get('href'):
            link['href'] = urljoin(base_url, link['href'])

    # Fix <script src> attributes
    for script in soup.find_all('script'):
        if script.get('src'):
            script['src'] = urljoin(base_url, script['src'])

    # Fix <video src> and <source src> attributes
    for video in soup.find_all('video'):
        if video.get('src'):
            video['src'] = urljoin(base_url, video['src'])
    for source in soup.find_all('source'):
        if source.get('src'):
            source['src'] = urljoin(base_url, source['src'])

    # Fix <a href> attributes (for completeness)
    for a in soup.find_all('a'):
        if a.get('href') and not a['href'].startswith('#'):
            a['href'] = urljoin(base_url, a['href'])

    return str(soup)


class ParseRequest(BaseModel):
    """Request body for parse endpoint"""
    url: str
    overrides: dict = {}


class ParseResponse(BaseModel):
    """Response body from parse endpoint"""
    original_html: str
    title: str
    url: str
    description: str
    pub_date: str
    language: str
    main_image: str


@router.post("/parse")
async def parse_url(request: ParseRequest) -> dict:
    """
    Parse a URL and extract RSS fields

    Args:
        request: ParseRequest with url and optional overrides

    Returns:
        Dict with original_html and parsed RSS fields
    """
    try:
        # Validate URL and SSRF safety
        if not validate_url_ssrf(request.url):
            raise HTTPException(status_code=400, detail="Invalid URL: must start with http:// or https:// and target a safe public host")

        # Fetch URL (falls back to FlareSolverr on 403)
        html_content = await fetch_html_async(request.url)

        # Build selector overrides for extract_with_images
        overrides = request.overrides or {}
        parsed = await asyncio.to_thread(
            parser.extract_with_images,
            html_content,
            title_selector=overrides.get('title_selector'),
            content_selector=overrides.get('content_selector'),
            image_selector=overrides.get('image_selector'),
            publish_date_selector=overrides.get('date_selector'),
            content_exclude_selector=overrides.get('content_exclude_selector'),
            base_url=request.url
        )

        # If content is very thin, retry with FlareSolverr (JS-rendered)
        content_len = len(parsed.get("content") or "")
        if content_len < 200:
            try:
                rendered_html = await fetch_html_rendered_async(request.url)
                rendered_parsed = await asyncio.to_thread(
                    parser.extract_with_images,
                    rendered_html,
                    title_selector=overrides.get('title_selector'),
                    content_selector=overrides.get('content_selector'),
                    image_selector=overrides.get('image_selector'),
                    publish_date_selector=overrides.get('date_selector'),
                    content_exclude_selector=overrides.get('content_exclude_selector'),
                    base_url=request.url
                )
                if len(rendered_parsed.get("content") or "") > content_len:
                    parsed = rendered_parsed
                    html_content = rendered_html
            except Exception:
                pass  # keep original result

        # Fix relative URLs in HTML so they work in iframe
        html_with_absolute_urls = fix_relative_urls(html_content, request.url)

        return {
            "original_html": html_with_absolute_urls,
            "title": parsed["title"],
            "url": request.url,
            "description": parsed["content"],
            "pub_date": parsed.get("publish_date", "") or parsed.get("published_time", ""),
            "language": parsed.get("language", "en"),
            "main_image": parsed.get("main_image", ""),
            "images": parsed.get("images", []),
        }

    except HTTPException:
        # Re-raise HTTPException without wrapping
        raise
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")



@router.post("/parse/unfluff")
async def parse_unfluff(request: ParseRequest) -> dict:
    """
    Parse content using node-unfluff library.

    Args:
        request: ParseRequest with url

    Returns:
        Dict with success status, text content, images, and main_image
    """
    try:
        # Validate URL and SSRF safety
        if not validate_url_ssrf(request.url):
            raise HTTPException(status_code=400, detail="Invalid URL: must start with http:// or https:// and target a safe public host")

        # Fetch the URL
        async with httpx.AsyncClient() as client:
            response = await client.get(request.url, timeout=30)
            response.raise_for_status()

        # Parse with node-unfluff
        result = await parse_with_node_unfluff(response.text, request.url)

        return {
            "success": True,
            "text": result['text'],
            "images": result['images'],
            "main_image": result['main_image'],
            "url": request.url
        }
    except HTTPException:
        # Re-raise HTTPException without wrapping
        raise
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")
