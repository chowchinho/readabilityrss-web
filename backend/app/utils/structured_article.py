"""Rebuild article bodies from JSON islands on client-rendered sites.

Some sites ship no <img> at all in their server HTML — the DOM is assembled by
JavaScript on the client. Readability still finds the text (that part is
server-rendered) but there are simply no image elements to extract, so lazy-image
conversion and image recovery both have nothing to work with. The images are in a
JSON payload embedded in the page, alongside the block structure that gives their
position, so the body can be rebuilt exactly rather than approximated.

Rendering the page instead would work but costs a headless browser fetch per
article and, on hk01, returns mostly reaction-button icons.
"""
import html as html_lib
import json
import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADING_TOKENS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def extract_structured_article(page_html: str, url: str) -> dict | None:
    """Rebuild content from a page's JSON payload, or None if not applicable.

    Returns {"content": html, "main_image": url} when a body was recovered.
    Never raises — callers fall back to normal extraction.
    """
    if not page_html or not url:
        return None
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    builder = _BUILDERS.get(host)
    if not builder:
        return None
    try:
        return builder(page_html)
    except Exception as exc:
        logger.warning(f"structured extraction failed for {host}: {exc}")
        return None


def _next_data(page_html: str) -> dict | None:
    tag = BeautifulSoup(page_html, "html.parser").find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except (ValueError, TypeError):
        return None


def _render_text_block(block: dict) -> list[str]:
    """A text block holds paragraphs, each a list of {type, content} tokens."""
    out = []
    for paragraph in block.get("htmlTokens") or []:
        if not isinstance(paragraph, list):
            continue
        tokens = [t for t in paragraph if isinstance(t, dict) and t.get("content")]
        if not tokens:
            continue
        text = html_lib.escape("".join(str(t["content"]) for t in tokens))
        # The first token's type sets the paragraph's tag; anything unrecognised
        # is treated as prose rather than dropped.
        tag = str(tokens[0].get("type") or "").lower()
        tag = tag if tag in _HEADING_TOKENS else "p"
        out.append(f"<{tag}>{text}</{tag}>")
    return out


def _paragraphs(value) -> list[str]:
    """Wrap a string, or a list of strings, as escaped <p> elements."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(f"<p>{html_lib.escape(text)}</p>")
    return out


def _text_of(paragraph_html: str) -> str:
    return BeautifulSoup(paragraph_html, "html.parser").get_text(strip=True)


def _render_image_block(block: dict) -> str | None:
    image = block.get("image")
    if not isinstance(image, dict):
        return None
    src = image.get("cdnUrl")
    if not src or not isinstance(src, str):
        return None
    src_attr = html_lib.escape(src, quote=True)
    caption = str(image.get("caption") or "").strip()
    if caption:
        caption_attr = html_lib.escape(caption, quote=True)
        return (
            f'<figure><img src="{src_attr}" alt="{caption_attr}"/>'
            f"<figcaption>{html_lib.escape(caption)}</figcaption></figure>"
        )
    return f'<figure><img src="{src_attr}" alt=""/></figure>'


def _build_hk01(page_html: str) -> dict | None:
    data = _next_data(page_html)
    if not data:
        return None
    article = (
        data.get("props", {}).get("initialProps", {}).get("pageProps", {}).get("article")
    )
    if not isinstance(article, dict):
        return None
    blocks = article.get("blocks")
    if not isinstance(blocks, list):
        return None

    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("blockType") or "").lower()
        # Only an explicit image block is this article's own image; other blocks
        # carry an `image` key too (embeds, and a related-articles widget whose
        # `articles[]` hold recommendation thumbnails).
        if block_type == "image":
            rendered = _render_image_block(block)
            if rendered:
                parts.append(rendered)
        elif block_type == "text":
            parts.extend(_render_text_block(block))
        elif block_type == "summary":
            parts.extend(_paragraphs(block.get("summary")))

    if not parts:
        return None

    # The opening paragraphs are article-level fields, not blocks, so rebuilding
    # without them loses the lede. `teaser` holds the full text; `description` is
    # a truncated meta field and only stands in when teaser is absent.
    lede = _paragraphs(article.get("teaser")) or _paragraphs(article.get("description"))
    body = "".join(parts)
    parts[:0] = [p for p in lede if _text_of(p) not in body]

    main_image = (article.get("mainImage") or {}).get("cdnUrl") or ""
    return {
        "content": f"<div>{''.join(parts)}</div>",
        "main_image": main_image if isinstance(main_image, str) else "",
    }


_BUILDERS = {"hk01.com": _build_hk01}
