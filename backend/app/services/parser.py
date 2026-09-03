"""Readability parser for extracting content from HTML."""
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from readability import Document as ReadabilityDocument
from bs4 import BeautifulSoup
from ..utils.image_recovery import ImageRecovery
from ..utils.boilerplate_filter import BoilerplateFilter
from ..utils.lazy_images import LAZY_IMAGE_ATTRIBUTES, extract_srcset_candidate, is_placeholder_src

logger = logging.getLogger(__name__)


class ScoredDocument(ReadabilityDocument):
    """Captures images that survive readability's sanitize() pass."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._approved_images = []

    def sanitize(self, node, candidates):
        from lxml.html import fromstring
        result = super().sanitize(node, candidates)
        self._approved_images = []

        # sanitize() may return an lxml Element or a string
        if isinstance(result, str):
            try:
                tree = fromstring(result)
            except Exception:
                return result
        else:
            tree = result

        for img in tree.xpath('.//img'):
            src = img.get('src') or img.get('data-src') or ''
            if src and not src.startswith('data:'):
                self._approved_images.append({
                    'url': src,
                    'src': src,
                    'alt': img.get('alt', ''),
                    'width': img.get('width'),
                    'height': img.get('height'),
                })
        return result


# Alias for backwards compatibility
Document = ReadabilityDocument


class ReadabilityParser:
    """Parser for extracting RSS fields from HTML content using readability."""

    def __init__(self):
        """Initialize parser with supporting filters."""
        self.boilerplate_filter = BoilerplateFilter()

    def parse(self, html_content: str, content_exclude_selector: str = None, preferred_image: str = None, base_url: str = None) -> dict:
        """
        Parse HTML content and extract RSS fields.

        Args:
            html_content: Raw HTML string

        Returns:
            Dict with title, content, language, published_time, main_image
        """
        # 1. Apply global exclusions to the raw HTML first
        soup = BeautifulSoup(html_content, "lxml")
        if content_exclude_selector:
            try:
                for excluded in soup.select(content_exclude_selector):
                    excluded.decompose()
            except Exception as e:
                logger.warning("Failed to apply content_exclude_selector '%s': %s", content_exclude_selector, e)
        
        # 2. Pre-process lazy images BEFORE readability sees the HTML
        # Use the (potentially modified) HTML from the soup
        html_to_process = str(soup)
        processed_html = self.convert_lazy_images(html_to_process)
        doc = ScoredDocument(processed_html)

        title = doc.short_title() or "Untitled"
        content = doc.summary()

        # Apply custom exclusions if provided (post-processing fallback)
        content_soup = None
        if content_exclude_selector:
            try:
                content_soup = BeautifulSoup(content, "lxml")
                # Also check for the selector in the summary output
                for excluded in content_soup.select(content_exclude_selector):
                    excluded.decompose()
                # lxml wraps fragments in <html><body>; use .body to get just the content
                content = content_soup.body.decode_contents() if content_soup.body else content_soup.decode_contents()
            except Exception as e:
                logger.warning("Failed to apply content_exclude_selector on summary '%s': %s", content_exclude_selector, e)
                content_soup = None

        # Auto-fallback: if readability extracted <50% of the <article> tag's
        # text, readability likely failed on a component-heavy DOM (e.g. BBC).
        # Use <article> contents directly instead.
        # We use the modified soup here so exclusions are already applied.
        article_tag = soup.select_one("article")
        if article_tag:
            if content_soup is not None:
                content_text_len = len(content_soup.get_text(strip=True))
            else:
                content_text_len = len(BeautifulSoup(content, "lxml").get_text(strip=True))
            article_text_len = len(article_tag.get_text(strip=True))
            if article_text_len > 0 and content_text_len / article_text_len < 0.5:
                # Apply custom exclusions to the fallback as well
                if content_exclude_selector:
                    try:
                        for excluded in article_tag.select(content_exclude_selector):
                            excluded.decompose()
                    except Exception as e:
                        logger.warning("Failed to apply content_exclude_selector on fallback '%s': %s", content_exclude_selector, e)
                
                self.boilerplate_filter.remove_boilerplate_elements(article_tag)
                fallback_content = self.convert_lazy_images(str(article_tag.decode_contents()))
                fallback_content = self._tidy_html(fallback_content)
                # Only use the fallback if it actually has content;
                # the boilerplate filter may strip too aggressively on some sites.
                if fallback_content:
                    content = fallback_content

        # Clean non-content elements (SVGs, scripts, forms, etc.) from all paths.
        # The <article>-fallback branch above already tidied its own output, so that
        # path is tidied twice. That looks redundant and is not: _tidy_html is NOT
        # idempotent — measured on 8 real feed pages, a second pass changed the output
        # every time, and on 5 of them it removed more than whitespace. Skipping the
        # second call on the fallback path therefore leaves content the old code
        # stripped. Tried in phase 7 and reverted; it bought ~5% parse time and changed
        # extraction output. Do not "optimise" this again without first making
        # _tidy_html idempotent and proving it.
        content = self._tidy_html(content)

        language = self._extract_language(html_content)
        publish_date = self._extract_publish_date(html_content)
        main_image = self._extract_main_image(html_content, content_html=content, preferred_image=preferred_image, base_url=base_url)

        return {
            "title": title,
            "content": content,
            "language": language,
            "publish_date": publish_date,
            "main_image": main_image,
            "approved_images": doc._approved_images,
        }

    def parse_with_overrides(self, html_content: str, overrides: dict = None) -> dict:
        """
        Parse HTML with optional CSS selector overrides

        Args:
            html_content: Raw HTML string
            overrides: Dict with optional keys:
                - title_selector: CSS selector to extract title
                - content_selector: CSS selector to extract content
                - image_selector: CSS selector to extract main image
                - date_selector: CSS selector to extract publish date

        Returns:
            Dict with extracted RSS fields
        """
        overrides = overrides or {}
        content_exclude_selector = overrides.get("content_exclude_selector")

        # 1. Apply global exclusions to the root document first
        soup = BeautifulSoup(html_content, "lxml")
        if content_exclude_selector:
            try:
                for excluded in soup.select(content_exclude_selector):
                    excluded.decompose()
            except Exception as e:
                logger.warning("Failed to apply content_exclude_selector '%s': %s", content_exclude_selector, e)
        
        # Use cleaned HTML for further parsing (Readability title/date extraction)
        cleaned_html = str(soup)
        result = self.parse(cleaned_html, content_exclude_selector=content_exclude_selector)

        # Override title if selector provided
        if "title_selector" in overrides:
            try:
                element = soup.select_one(overrides["title_selector"])
                if element:
                    result["title"] = element.get_text(strip=True)
            except Exception as e:
                logger.warning("Failed to apply title_selector '%s': %s", overrides["title_selector"], e)

        # Override content if selector provided
        if "content_selector" in overrides:
            selector = overrides["content_selector"]
            # Support multiple comma-separated selectors: take the first match of each
            selector_parts = [s.strip() for s in selector.split(',') if s.strip()]
            
            content_elements = []
            seen_ids = set()
            
            for part in selector_parts:
                try:
                    el = soup.select_one(part)
                except Exception as e:
                    logger.warning("Failed to apply content_selector part '%s': %s", part, e)
                    el = None
                if el and id(el) not in seen_ids:
                    # Check if this element is a descendant of any already picked element
                    # or if any already picked element is a descendant of this one
                    is_nested = False
                    to_remove = []
                    for existing in content_elements:
                        if el in existing.parents:
                            is_nested = True
                            break
                        if existing in el.parents:
                            to_remove.append(existing)
                    
                    if not is_nested:
                        # If this element is a parent of existing ones, remove the children
                        for child in to_remove:
                            content_elements.remove(child)
                            seen_ids.remove(id(child))
                        
                        content_elements.append(el)
                        seen_ids.add(id(el))
            
            if content_elements:
                final_content_parts = []
                for content_element in content_elements:
                    # Apply custom user exclusions within each selected section
                    if content_exclude_selector:
                        try:
                            for excluded in content_element.select(content_exclude_selector):
                                excluded.decompose()
                        except Exception as e:
                            logger.warning("Failed to apply content_exclude_selector '%s' on section: %s", content_exclude_selector, e)
                    
                    # Remove boilerplate content from this section
                    self.boilerplate_filter.remove_boilerplate_elements(content_element)
                    
                    part_html = str(content_element.decode_contents())
                    final_content_parts.append(part_html)
                
                # Join all parts
                combined_html = "\n".join(final_content_parts)
                combined_html = self.convert_lazy_images(combined_html)
                combined_html = self._tidy_html(combined_html)
                result["content"] = combined_html

        # Override image if selector provided
        if "image_selector" in overrides:
            try:
                element = soup.select_one(overrides["image_selector"])
                if element:
                    if element.name == "img":
                        # Handle lazy loading attributes
                        lazy_attrs = [
                            "data-src", "data-lazy-src", "data-original", "data-lazy",
                            "data-actualsrc", "data-lazyload", "data-full-url", "data-image",
                            "origin"
                        ]
                        real_url = None
                        for attr in lazy_attrs:
                            if element.get(attr):
                                real_url = element.get(attr)
                                break
                        
                        result["main_image"] = real_url or element.get("src", "")
                    else:
                        result["main_image"] = element.get_text(strip=True)
            except Exception as e:
                logger.warning("Failed to apply image_selector '%s': %s", overrides["image_selector"], e)

        # Override publish date if selector provided
        if "date_selector" in overrides:
            try:
                element = soup.select_one(overrides["date_selector"])
                if element:
                    # Prefer ISO datetime attribute on <time> (handles unpadded text like "2026.5.27")
                    if element.name == "time" and element.get("datetime"):
                        raw = element.get("datetime")
                    else:
                        time_child = element.find("time") if hasattr(element, "find") else None
                        if time_child and time_child.get("datetime"):
                            raw = time_child.get("datetime")
                        else:
                            raw = element.get_text(strip=True)
                    parsed = self._parse_and_validate_date(raw)
                    result["publish_date"] = parsed if parsed else raw
            except Exception as e:
                logger.warning("Failed to apply date_selector '%s': %s", overrides["date_selector"], e)

        return result

    def _tidy_html(self, html: str) -> str:
        """
        Clean up HTML content by removing excessive whitespace and empty tags.
        
        Args:
            html: HTML content string
            
        Returns:
            Tidied HTML string
        """
        if not html:
            return ""
            
        # Parse with BeautifulSoup for structural cleaning
        soup = BeautifulSoup(html, "html.parser")

        # 1. Remove non-content elements that leak through readability
        # SVGs: inline icons, decorative graphics (comment bubbles, share icons)
        # script/style: inline JS/CSS that shouldn't appear in RSS
        # canvas: interactive charts/visualizations
        # form/input/button/select/textarea: newsletter signups, polls, comment boxes
        for tag in soup.find_all(['svg', 'script', 'style', 'canvas',
                                  'form', 'input', 'button', 'select', 'textarea']):
            tag.decompose()

        # iframes: keep video embeds (YouTube, Vimeo), remove everything else
        for iframe in soup.find_all('iframe'):
            src = (iframe.get('src') or '').lower()
            if not any(v in src for v in ['youtube', 'vimeo', 'dailymotion', 'player']):
                iframe.decompose()

        # 2. Strip inline styles from layout/structural elements.
        # Skipping span/code/pre to preserve syntax-highlighting on tech articles.
        _STRIP_STYLE_TAGS = [
            'div', 'p', 'section', 'article', 'figure', 'figcaption',
            'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'blockquote', 'table', 'thead', 'tbody', 'tr', 'td', 'th',
            'header', 'footer', 'a', 'img',
        ]
        for tag in soup.find_all(_STRIP_STYLE_TAGS):
            if tag.get('style'):
                del tag['style']

        # 3. Remove empty tags (p, div, span, etc. that have no content or images)
        for tag in soup.find_all(['p', 'div', 'span', 'section', 'article']):
            # If tag has no text content and no images/videos/iframes
            if not tag.get_text(strip=True) and not tag.find_all(['img', 'video', 'iframe', 'picture']):
                tag.decompose()
        
        # Convert back to string
        html = str(soup)
        
        # 4. Collapse multiple whitespace/newlines
        # Replace multiple spaces with a single space
        html = re.sub(r'[ \t]+', ' ', html)
        # Remove whitespace at the beginning of lines
        html = re.sub(r'^[ \t]+', '', html, flags=re.MULTILINE)
        # Remove whitespace at the end of lines
        html = re.sub(r'[ \t]+$', '', html, flags=re.MULTILINE)

        # 5. Handle excessive line breaks
        # Replace 3 or more consecutive newlines with just 2
        html = re.sub(r'\n{3,}', '\n\n', html)
        
        return html.strip()

    def convert_lazy_images(self, html_content: str) -> str:
        """
        Convert lazy-loaded images to standard <img> tags.

        Handles:
        - data-src → src
        - data-lazy-src → src
        - data-srcset → srcset (EIO plugin format)
        - Removes lazy-loading attributes and placeholder src

        Args:
            html_content: HTML string with potentially lazy-loaded images

        Returns:
            HTML with lazy images converted to standard src
        """
        soup = BeautifulSoup(html_content, "html.parser")

        for noscript in soup.find_all("noscript"):
            raw_noscript = noscript.decode_contents()
            if "<img" not in raw_noscript:
                continue
            noscript_soup = BeautifulSoup(raw_noscript, "html.parser")
            fallback_img = noscript_soup.find("img")
            if not fallback_img:
                continue

            previous_img = noscript.find_previous_sibling("img")
            if previous_img and self._is_placeholder_src(previous_img.get("src")):
                fallback_src = self._extract_img_candidate(fallback_img)
                if fallback_src:
                    previous_img["src"] = fallback_src
                if fallback_img.get("srcset"):
                    previous_img["srcset"] = fallback_img.get("srcset")

        for picture in soup.find_all("picture"):
            img = picture.find("img")
            if not img:
                continue
            candidate = None
            for source in picture.find_all("source"):
                candidate = self._extract_srcset_candidate(
                    source.get("data-srcset") or source.get("srcset")
                )
                if candidate:
                    if source.get("data-srcset") and not source.get("srcset"):
                        source["srcset"] = source["data-srcset"]
                    break
            if candidate and self._is_placeholder_src(img.get("src")):
                img["src"] = candidate

        # Find all img tags
        for img in soup.find_all("img"):
            srcset_value = img.get("data-srcset") or img.get("data-lazy-srcset")
            if srcset_value:
                img["srcset"] = srcset_value

            real_url = self._extract_img_candidate(img)
            if real_url:
                img["src"] = real_url

            # Remove lazy-loading attributes to clean up the HTML
            all_lazy_attrs = self._lazy_image_attributes() + [
                "data-srcset", "data-lazy-srcset", "loading",
                "data-eio", "data-eio-rwidth", "data-eio-rheight",
                "data-sizes", "sizes",
            ]
            for attr in all_lazy_attrs:
                if attr in img.attrs:
                    del img[attr]

            # Strip inline styles — reader CSS handles all img sizing
            if img.get("style"):
                del img["style"]

            # Remove lazyload class
            if img.get("class"):
                classes = img["class"]
                if isinstance(classes, list):
                    img["class"] = [c for c in classes if c != "lazyload"]
                    if not img["class"]:
                        del img["class"]
                else:
                    classes_str = str(classes).replace("lazyload", "").strip()
                    if classes_str:
                        img["class"] = classes_str
                    else:
                        del img["class"]

        return str(soup)

    @staticmethod
    def _lazy_image_attributes() -> list[str]:
        return LAZY_IMAGE_ATTRIBUTES

    @staticmethod
    def _is_placeholder_src(src: str) -> bool:
        return is_placeholder_src(src)

    def _extract_img_candidate(self, img) -> str:
        for attr in self._lazy_image_attributes():
            if img.get(attr):
                return img.get(attr)

        srcset_value = (
            img.get("data-srcset")
            or img.get("data-lazy-srcset")
            or img.get("srcset")
        )
        candidate = self._extract_srcset_candidate(srcset_value)
        if candidate and self._is_placeholder_src(img.get("src")):
            return candidate

        src = img.get("src", "")
        if src and not self._is_placeholder_src(src):
            return src

        return candidate or src

    @staticmethod
    def _extract_srcset_candidate(srcset: str) -> str:
        return extract_srcset_candidate(srcset)

    def _extract_language(self, html_content: str) -> str:
        """
        Extract language from meta tags or html lang attribute.

        Looks for <meta http-equiv="content-language" content="...">
        Fallback to <html lang="...">
        Default to "en"

        Args:
            html_content: Raw HTML string

        Returns:
            Language code as string (default "en")
        """
        # Try to find http-equiv="content-language"
        match = re.search(
            r'<meta\s+http-equiv=["\']content-language["\']\s+content=["\']([^"\']+)["\']',
            html_content,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)

        # Try to find html lang attribute
        match = re.search(r'<html[^>]*\s+lang=["\']?([^\s"\'>]+)', html_content, re.IGNORECASE)
        if match:
            return match.group(1)

        # Default to "en"
        return "en"

    def _extract_publish_date(self, html_content: str) -> str:
        """
        Extract publish date from HTML using two-stage detection.

        Stage 1: Check metadata sources (article:published_time, og:published_time, etc.)
        Stage 2: If metadata fails, scan content for dates near keywords or fallback scan

        Args:
            html_content: Raw HTML string

        Returns:
            ISO formatted date (YYYY-MM-DD) or empty string if not found
        """
        # Stage 1: Try metadata first
        metadata_date = self._extract_publish_date_from_metadata(html_content)
        if metadata_date:
            return metadata_date

        # Stage 2: Fall back to content detection
        content_date = self._extract_publish_date_from_content(html_content)
        if content_date:
            return content_date

        # No date found
        return ""

    def _extract_main_image(self, html_content: str, content_html: str = None, preferred_image: str = None, base_url: str = None) -> str:
        """
        Extract main image from various sources with priority:
        1. Preferred image (e.g. from RSS feed)
        2. og:image meta tag
        3. twitter:image meta tag
        4. <link rel="image_src"> tag
        5. First substantial image from content_html as fallback
        
        Normalization: Converts all URLs to absolute (handles // and relative paths via base_url).
        Thumbnail Filtering: If metadata returns a thumbnail (_s. or _thumb), continues to fallback.
        """
        from urllib.parse import urljoin
        
        def normalize_url(url: str) -> str:
            if not url:
                return ""
            url = url.strip()
            if base_url:
                return urljoin(base_url, url)
            if url.startswith('//'):
                return f"https:{url}"
            return url

        def is_thumbnail(url: str) -> bool:
            if not url:
                return False
            # Common thumbnail patterns: _s.jpg (Tamiya), _thumb.jpg, -150x150.jpg, etc.
            lower_url = url.lower()
            return any(p in lower_url for p in ['_s.', '_thumb.', '-150x150.', '-100x100.', 'avatar'])

        # 1. Preferred image
        if preferred_image:
            return normalize_url(preferred_image)

        # Helper for robust meta tag extraction (attribute order independent)
        def get_meta_content(html: str, property_name: str, attr_name: str = "property") -> str:
            # Matches <meta property="og:image" content="..."> or <meta content="..." property="og:image">
            # and variations with single quotes or extra spacing.
            tag_regex = re.compile(rf'<meta[^>]+(?:{attr_name}|name)=["\']{property_name}["\'][^>]*>', re.I)
            match = tag_regex.search(html)
            if match:
                tag = match.group(0)
                content_match = re.search(r'content=["\']([^"\']+)["\']', tag, re.I)
                if content_match:
                    return normalize_url(content_match.group(1))
            return ""

        # 2-4. Metadata sources
        metadata_image = ""
        # Check og:image
        metadata_image = get_meta_content(html_content, "og:image")
        
        # If no og:image, check twitter:image
        if not metadata_image:
            metadata_image = get_meta_content(html_content, "twitter:image")
            if not metadata_image:
                metadata_image = get_meta_content(html_content, "twitter:image", attr_name="name")
        
        # If no twitter, check image_src
        if not metadata_image:
            link_regex = re.compile(r'<link[^>]+rel=["\']image_src["\'][^>]*>', re.I)
            match = link_regex.search(html_content)
            if match:
                tag = match.group(0)
                href_match = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
                if href_match:
                    metadata_image = normalize_url(href_match.group(1))

        # If we found a high-quality metadata image, return it.
        # If it's a thumbnail (like Tamiya's _s.jpg), we keep it as a backup but look for a better one in content.
        if metadata_image and not is_thumbnail(metadata_image):
            return metadata_image

        # 5. Content fallback
        content_fallback = ""
        if content_html:
            soup = BeautifulSoup(content_html, "lxml")
            # Blacklist for UI elements and tiny images
            blacklist = re.compile(r'icon|logo|avatar|nav|sidebar|social|\bads?\b|banner|pixel|spacer', re.I)
            
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src')
                if not src or src.startswith('data:'):
                    continue
                
                src = normalize_url(src)
                
                # Filter by attributes
                if blacklist.search(src) or blacklist.search(str(img.get('class', ''))) or blacklist.search(str(img.get('id', ''))):
                    continue
                
                # Check dimensions if available
                width = img.get('width', '')
                height = img.get('height', '')
                try:
                    if width and height:
                        w, h = int(width), int(height)
                        if w < 100 or h < 100:
                            continue
                except (ValueError, TypeError):
                    pass
                
                # If we get here, it's a good candidate.
                # If it's a thumbnail, we skip it if we have something better.
                if not is_thumbnail(src):
                    return src
                elif not content_fallback:
                    content_fallback = src

        # 6. Last resort: Original HTML fallback (for component-heavy sites where readability fails)
        # Only do this if metadata_image was a thumbnail or empty, AND content had no good images.
        raw_soup = BeautifulSoup(html_content, "lxml")
        strict_blacklist = re.compile(r'icon|logo|avatar|nav|sidebar|social|\bads?\b|banner|pixel|spacer|header|footer|menu|sidebar|comment|slider|placeholder', re.I)
        
        # Priority 1: Product images (non-thumbnail)
        for img in raw_soup.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if not src or src.startswith('data:'):
                continue
            src = normalize_url(src)
            if is_thumbnail(src) or strict_blacklist.search(src):
                continue
            
            # Tamiya and many e-commerce sites use /item/ or /product/ for the main visuals
            if any(p in src.lower() for p in ['/item/', '/product/', '/goods/']):
                return src

        # Priority 2: Any substantial image
        for img in raw_soup.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if not src or src.startswith('data:'):
                continue
            src = normalize_url(src)
            if strict_blacklist.search(src):
                continue
            
            width = img.get('width', '')
            height = img.get('height', '')
            try:
                if width and height:
                    w, h = int(width), int(height)
                    if w > 200 and h > 100:
                        return src
            except (ValueError, TypeError):
                pass

        return metadata_image or content_fallback or ""

    def _parse_and_validate_date(self, date_string: str) -> str:
        """
        Parse a date string in multiple formats and normalize to ISO (YYYY-MM-DD).

        Supports:
        - ISO format: 2026-03-17, 2026-03-17T14:30:00Z
        - Slash format: 2026/03/17 or 03/17/2026
        - Dot format: 2026.03.17

        Args:
            date_string: Raw date string to parse

        Returns:
            ISO formatted date (YYYY-MM-DD) or empty string if invalid
        """
        from datetime import datetime, timedelta

        if not date_string or not isinstance(date_string, str):
            return ""

        date_string = date_string.strip()

        # Try ISO format (YYYY-MM-DD) or with time (YYYY-MM-DDTHH:MM:SSZ)
        iso_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_string)
        if iso_match:
            year, month, day = iso_match.groups()
            parsed_date = self._validate_and_return_date(year, month, day)
            if parsed_date:
                return parsed_date

        # Try slash format YYYY/MM/DD
        slash_yyyymmdd_match = re.match(r'(\d{4})/(\d{2})/(\d{2})', date_string)
        if slash_yyyymmdd_match:
            year, month, day = slash_yyyymmdd_match.groups()
            parsed_date = self._validate_and_return_date(year, month, day)
            if parsed_date:
                return parsed_date

        # Try slash format MM/DD/YYYY
        slash_mmddyyyy_match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_string)
        if slash_mmddyyyy_match:
            month, day, year = slash_mmddyyyy_match.groups()
            parsed_date = self._validate_and_return_date(year, month, day)
            if parsed_date:
                return parsed_date

        # Try dot format YYYY.MM.DD
        dot_match = re.match(r'(\d{4})\.(\d{2})\.(\d{2})', date_string)
        if dot_match:
            year, month, day = dot_match.groups()
            parsed_date = self._validate_and_return_date(year, month, day)
            if parsed_date:
                return parsed_date

        # Try natural language format "Month DD, YYYY" or "Month DD YYYY"
        months_full = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }
        months_abbr = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
            'may': 5, 'jun': 6, 'jul': 7, 'aug': 8,
            'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
        }

        # Pattern: "Month DD, YYYY" or "Month DD YYYY"
        natural_match = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', date_string)
        if natural_match:
            month_name, day, year = natural_match.groups()
            month_lower = month_name.lower()

            # Check full month name
            if month_lower in months_full:
                month_num = months_full[month_lower]
                parsed_date = self._validate_and_return_date(year, str(month_num), day)
                if parsed_date:
                    return parsed_date

            # Check abbreviated month name
            if month_lower in months_abbr:
                month_num = months_abbr[month_lower]
                parsed_date = self._validate_and_return_date(year, str(month_num), day)
                if parsed_date:
                    return parsed_date

        # Pattern: "DD Month YYYY" or "DD Month, YYYY"
        natural_match2 = re.match(r'(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})', date_string)
        if natural_match2:
            day, month_name, year = natural_match2.groups()
            month_lower = month_name.lower()

            # Check full month name
            if month_lower in months_full:
                month_num = months_full[month_lower]
                parsed_date = self._validate_and_return_date(year, str(month_num), day)
                if parsed_date:
                    return parsed_date

            # Check abbreviated month name
            if month_lower in months_abbr:
                month_num = months_abbr[month_lower]
                parsed_date = self._validate_and_return_date(year, str(month_num), day)
                if parsed_date:
                    return parsed_date

        # Try Chinese/Japanese format YYYY年MM月DD日 or YYYY年M月D日
        cjk_match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_string)
        if cjk_match:
            year, month, day = cjk_match.groups()
            parsed_date = self._validate_and_return_date(year, month, day)
            if parsed_date:
                return parsed_date

        return ""

    def _now(self):
        """Reference clock for the freshness window below.

        A separate method purely so tests can pin it. Asserting on a hard-coded date
        against the real clock makes a test that passes today and fails once that date
        drifts outside the 90-day window - which is exactly what happened to the whole
        date-detection suite. Override this, never the stdlib.
        """
        from datetime import datetime
        return datetime.now()

    def _validate_and_return_date(self, year: str, month: str, day: str) -> str:
        """
        Validate year, month, day and perform sanity checks.

        Returns:
            ISO formatted date (YYYY-MM-DD) or empty string if invalid
        """
        from datetime import datetime, timedelta

        try:
            year_int = int(year)
            month_int = int(month)
            day_int = int(day)

            # Basic range checks
            if month_int < 1 or month_int > 12:
                return ""
            if day_int < 1 or day_int > 31:
                return ""

            # Create datetime object to validate date (catches invalid days like Feb 29 in non-leap years)
            parsed_datetime = datetime(year_int, month_int, day_int)

            today = self._now().date()

            # Sanity check: not in far future (allow up to 7 days for timezone variance)
            days_in_future = (parsed_datetime.date() - today).days
            if days_in_future > 7:
                return ""

            # Sanity check: not too old (allow articles from the past 90 days for recent news)
            days_old = (today - parsed_datetime.date()).days
            if days_old > 90:
                return ""

            # Return as ISO format
            return f"{year_int:04d}-{month_int:02d}-{day_int:02d}"

        except (ValueError, TypeError):
            return ""

    def _extract_publish_date_from_metadata(self, html_content: str) -> str:
        """
        Extract publish date from metadata sources in priority order.

        Checks in order:
        1. article:published_time
        2. og:published_time
        3. datePublished (JSON-LD)
        4. publish_date meta name
        5. date meta name
        6. article.created meta name

        Args:
            html_content: Raw HTML string

        Returns:
            ISO formatted date (YYYY-MM-DD) or empty string if not found
        """
        import json

        # Priority 1: article:published_time
        match = re.search(
            r'<meta\s+property=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']',
            html_content,
            re.IGNORECASE,
        )
        if match:
            date = self._parse_and_validate_date(match.group(1))
            if date:
                return date

        # Priority 2: og:published_time
        match = re.search(
            r'<meta\s+property=["\']og:published_time["\']\s+content=["\']([^"\']+)["\']',
            html_content,
            re.IGNORECASE,
        )
        if match:
            date = self._parse_and_validate_date(match.group(1))
            if date:
                return date

        # Priority 3: datePublished in JSON-LD
        json_ld_pattern = r'<script[^>]*type=["\']application/ld\+json["\']*[^>]*>([^<]+)</script>'
        for json_match in re.finditer(json_ld_pattern, html_content, re.IGNORECASE):
            try:
                json_data = json.loads(json_match.group(1))
                if isinstance(json_data, dict):
                    if 'datePublished' in json_data:
                        date = self._parse_and_validate_date(json_data['datePublished'])
                        if date:
                            return date
                    # Also check nested @graph for datePublished
                    if '@graph' in json_data:
                        for item in json_data.get('@graph', []):
                            if isinstance(item, dict) and 'datePublished' in item:
                                date = self._parse_and_validate_date(item['datePublished'])
                                if date:
                                    return date
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        # Priority 4: publish_date meta name
        match = re.search(
            r'<meta\s+name=["\']publish_date["\']\s+content=["\']([^"\']+)["\']',
            html_content,
            re.IGNORECASE,
        )
        if match:
            date = self._parse_and_validate_date(match.group(1))
            if date:
                return date

        # Priority 5: date meta name
        match = re.search(
            r'<meta\s+name=["\']date["\']\s+content=["\']([^"\']+)["\']',
            html_content,
            re.IGNORECASE,
        )
        if match:
            date = self._parse_and_validate_date(match.group(1))
            if date:
                return date

        # Priority 6: article.created meta name
        match = re.search(
            r'<meta\s+name=["\']article\.created["\']\s+content=["\']([^"\']+)["\']',
            html_content,
            re.IGNORECASE,
        )
        if match:
            date = self._parse_and_validate_date(match.group(1))
            if date:
                return date

        return ""

    def _extract_publish_date_from_content(self, html_content: str) -> str:
        """
        Extract publish date from HTML content using keyword search or fallback scan.

        First tries to find dates near language-specific keywords:
        - English: "published", "posted", "updated", "written", "on", "date"
        - Chinese: "发表于", "发布于", "更新于", "发表日期"
        - Japanese: "掲載日", "更新日", "投稿日", "公開日"

        If no keyword match found, scans entire content for any date pattern.

        Args:
            html_content: Raw HTML string

        Returns:
            ISO formatted date (YYYY-MM-DD) or empty string if not found
        """
        # Define keywords for each language
        keywords = {
            'en': ['published', 'posted', 'updated', 'written', 'on', 'date'],
            'zh': ['发表于', '发布于', '更新于', '发表日期'],
            'ja': ['掲載日', '更新日', '投稿日', '公開日'],
        }

        # Clean HTML to plain text (remove tags but keep content)
        soup = BeautifulSoup(html_content, "lxml")

        # Remove script and style tags
        for script in soup(["script", "style"]):
            script.decompose()

        text = soup.get_text(separator=" ", strip=True)

        # Step 1: Try keyword-based detection
        for lang, lang_keywords in keywords.items():
            for keyword in lang_keywords:
                # Find all occurrences of the keyword
                keyword_pattern = re.escape(keyword)
                for match in re.finditer(keyword_pattern, text, re.IGNORECASE):
                    start = match.start()
                    end = match.end()

                    # Extract 100 characters before and after the keyword
                    context_start = max(0, start - 100)
                    context_end = min(len(text), end + 100)
                    context = text[context_start:context_end]

                    # Look for date patterns in this context
                    date_patterns = [
                        r'\d{4}-\d{2}-\d{2}',  # ISO
                        r'\d{4}/\d{2}/\d{2}',  # Slash
                        r'\d{2}/\d{2}/\d{4}',  # Slash MM/DD/YYYY
                        r'\d{4}\.\d{2}\.\d{2}',  # Dot
                        r'[A-Za-z]+\s+\d{1,2},?\s+\d{4}',  # Month DD, YYYY
                        r'\d{1,2}\s+[A-Za-z]+\s+\d{4}',  # DD Month YYYY
                        r'\d{4}年\d{1,2}月\d{1,2}日',  # Chinese/Japanese
                    ]

                    for pattern in date_patterns:
                        date_match = re.search(pattern, context)
                        if date_match:
                            date_str = date_match.group(0)
                            parsed_date = self._parse_and_validate_date(date_str)
                            if parsed_date:
                                return parsed_date

        # Step 2: Fallback - scan entire content for any date pattern
        date_patterns = [
            r'\d{4}-\d{2}-\d{2}',  # ISO
            r'\d{4}/\d{2}/\d{2}',  # Slash
            r'\d{2}/\d{2}/\d{4}',  # Slash MM/DD/YYYY
            r'\d{4}\.\d{2}\.\d{2}',  # Dot
            r'[A-Za-z]+\s+\d{1,2},?\s+\d{4}',  # Month DD, YYYY
            r'\d{1,2}\s+[A-Za-z]+\s+\d{4}',  # DD Month YYYY
            r'\d{4}年\d{1,2}月\d{1,2}日',  # Chinese/Japanese
        ]

        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                date_str = match.group(0)
                parsed_date = self._parse_and_validate_date(date_str)
                if parsed_date:
                    return parsed_date

        return ""

    def merge_content_with_images(self, content: str, recovered_images: list) -> str:
        """
        Merge recovered images back into the content HTML.
        
        Args:
            content: Content HTML (summary)
            recovered_images: List of images from recover_images()
            
        Returns:
            HTML with images interleaved
        """
        recovery = ImageRecovery(boilerplate_filter=self.boilerplate_filter)
        return recovery.merge_content_with_images(content, recovered_images)

    def recover_images_from_html(
        self,
        html: str = None,
        container_selector: str = None,
        soup: BeautifulSoup = None,
    ) -> list:
        """
        Recover images from HTML using optional container selector (supports comma-separated).
        """
        if soup is None:
            if html is None:
                return []
            soup = BeautifulSoup(html, 'lxml')

        # Find containers if selector provided
        if container_selector:
            # Support multiple comma-separated selectors: take the first match of each
            selector_parts = [s.strip() for s in container_selector.split(',') if s.strip()]
            containers = []
            seen_ids = set()
            for part in selector_parts:
                el = soup.select_one(part)
                if el and id(el) not in seen_ids:
                    containers.append(el)
                    seen_ids.add(id(el))
        else:
            # We extract from the whole body
            containers = [soup.body if soup.body else soup]

        # Use ImageRecovery to extract and filter images from all containers
        recovery = ImageRecovery(boilerplate_filter=self.boilerplate_filter)
        all_recovered = []
        seen_urls = set()
        
        for container in containers:
            images = recovery.extract_images(container, apply_boilerplate_filter=True)
            for img in images:
                if img['url'] not in seen_urls:
                    all_recovered.append(img)
                    seen_urls.add(img['url'])
                    
        return all_recovered

    def extract_with_images(
        self,
        html: str,
        preferred_image: str = None,
        container_selector: str = None,
        image_selector: str = None,
        title_selector: str = None,
        content_selector: str = None,
        publish_date_selector: str = None,
        content_exclude_selector: str = None,
        base_url: str = None
    ) -> dict:
        """
        Extract content and images from HTML in a single pass.

        Uses ReadabilityParser for text extraction and ImageRecovery for images.
        When explicit selectors are provided, uses them. When not provided,
        combines Readability extraction with automatic image recovery.

        Args:
            html: HTML string to parse
            container_selector: CSS selector for content container (for images)
            image_selector: CSS selector for main image
            title_selector: CSS selector for title
            content_selector: CSS selector for content
            publish_date_selector: CSS selector for publish date
            content_exclude_selector: CSS selector for elements to exclude from content

        Returns:
            Dict with keys: title, content, images, main_image, publish_date, language, etc.
        """
        # Build overrides dict from provided selectors
        overrides = {}
        if title_selector:
            overrides['title_selector'] = title_selector
        if content_selector:
            overrides['content_selector'] = content_selector
        if image_selector:
            overrides['image_selector'] = image_selector
        if publish_date_selector:
            overrides['date_selector'] = publish_date_selector
        if content_exclude_selector:
            overrides['content_exclude_selector'] = content_exclude_selector

        # First extract content using existing parser
        if overrides:
            result = self.parse_with_overrides(html, overrides)
            # Apply preferred image if main_image not already set by an explicit image_selector
            if not result.get('main_image') and preferred_image:
                result['main_image'] = preferred_image
        else:
            result = self.parse(html, preferred_image=preferred_image, base_url=base_url)

        # Use readability-approved images first; fall back to anchor-text recovery
        approved_images = result.get('approved_images', [])

        if approved_images:
            # Approved images are already inline in summary() output —
            # no merge needed, just record them as metadata
            result['images'] = approved_images
        else:
            # Fallback: recover images via anchor-text matching and merge them in
            # We use the cleaned_html if available to ensure exclusions were applied
            # (cleaned_html is available in parse_with_overrides and parse)
            # Actually, parse() and parse_with_overrides() don't return it.
            # We apply exclusions manually here to the html before recovery
            recovery_soup = BeautifulSoup(html, 'lxml')
            if content_exclude_selector:
                for excluded in recovery_soup.select(content_exclude_selector):
                    excluded.decompose()
            
            recovered_images = self.recover_images_from_html(container_selector=container_selector, soup=recovery_soup)
            result['images'] = recovered_images
            if recovered_images and result.get('content'):
                result['content'] = self.merge_content_with_images(
                    result['content'],
                    recovered_images,
                )
                
                # If STILL no images in content after merge (usually because no anchor text was found),
                # and we recovered high-quality images, prepend the first few to the content.
                # This fixes gallery-heavy sites like Bandai Hobby.
                if '<img' not in result['content'] and len(recovered_images) > 0:
                    soup_final = BeautifulSoup(result['content'], "lxml")
                    if not soup_final.find_all('img'):
                        # Prepend first 5 recovered images as a gallery
                        gallery_div = soup_final.new_tag('div', attrs={"class": "recovered-gallery", "style": "display: flex; flex-direction: column; gap: 20px; align-items: center; margin-bottom: 30px;"})
                        for img_data in recovered_images[:5]:
                            img_tag = soup_final.new_tag('img', src=img_data['url'], alt=img_data.get('alt', ''), style="max-width: 100%; height: auto; border-radius: 8px;")
                            gallery_div.append(img_tag)
                        
                        # Insert at the top of content
                        first_el = soup_final.find(['p', 'div', 'h1', 'h2', 'section'])
                        if first_el:
                            first_el.insert_before(gallery_div)
                        else:
                            soup_final.insert(0, gallery_div)
                        result['content'] = soup_final.body.decode_contents() if soup_final.body else str(soup_final)

        return result


def _sync_parse_with_node_unfluff(html_content: str, url: str):
    temp_file = None
    try:
        # Create temporary file with HTML content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html',
                                         encoding='utf-8', delete=False) as f:
            f.write(html_content)
            temp_file = f.name

        try:
            # Run node-unfluff via command line
            result = subprocess.run(
                ['node', '-e', f"""
const Unfluff = require('unfluff');
const fs = require('fs');
const html = fs.readFileSync('{temp_file}', 'utf-8');
const article = Unfluff(html);
const output = {{
    text: article.text,
    image: article.image,
    images: article.images
}};
console.log(JSON.stringify(output, null, 2));
"""],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                raise Exception(f"node-unfluff error: {result.stderr}")

            data = json.loads(result.stdout)

            return {
                'text': data.get('text', ''),
                'images': data.get('images', []),
                'main_image': data.get('image', None)
            }

        finally:
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)

    except Exception as e:
        raise Exception(f"Failed to parse with node-unfluff: {str(e)}") from e


async def parse_with_node_unfluff(html_content: str, url: str):
    """
    Parse HTML using node-unfluff and return text + images.

    subprocess.run blocks for up to 10s; on the event loop that freezes every other
    request in this single-process server, so it runs in a worker thread instead.

    Args:
        html_content: Raw HTML string
        url: Source URL (for context)

    Returns:
        Dict with text, images, and main_image

    Raises:
        Exception: If node-unfluff is not installed or parsing fails
    """
    return await asyncio.to_thread(_sync_parse_with_node_unfluff, html_content, url)
