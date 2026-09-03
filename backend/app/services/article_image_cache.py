import asyncio
import hashlib
import io
import os
import time
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from PIL import Image

from ..utils.fetch import HEADERS, get_flaresolverr_solution
from ..utils.focal_point import DEFAULT_FOCAL, compute_focal_point

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
ARTICLE_IMAGE_CACHE_DIR = os.path.join(DATA_DIR, "article_image_cache")
IMAGE_CACHE_DIR = os.path.join(DATA_DIR, "image_cache")
CACHED_IMAGE_URL_PREFIX = "/api/reader/cached-image/"
_CACHED_HASH_RE = re.compile(re.escape(CACHED_IMAGE_URL_PREFIX) + r"([0-9a-f]+)")
MAX_IMAGE_DIMENSION = 1200
JPEG_QUALITY = 70
_INVALID_REMOTE_URL_CHARS = set('<>"\' \t\r\n')

os.makedirs(ARTICLE_IMAGE_CACHE_DIR, exist_ok=True)
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)


def is_local_cached_image_url(url: str) -> bool:
    return bool(url and url.startswith(CACHED_IMAGE_URL_PREFIX))


def cached_image_path_from_url(url: str) -> str | None:
    if not is_local_cached_image_url(url):
        return None
    filename = url[len(CACHED_IMAGE_URL_PREFIX):]
    filename = os.path.basename(filename)
    if not filename:
        return None
    return os.path.join(ARTICLE_IMAGE_CACHE_DIR, filename)


def cached_image_hash_from_url(url: str) -> str | None:
    """The cache key for a cached-image URL — the filename without extension.

    This is the same md5 that cache_remote_image derives from the source URL, so
    it is the join key between an article's stored image URL and its focal point.
    """
    if not is_local_cached_image_url(url):
        return None
    filename = os.path.basename(url[len(CACHED_IMAGE_URL_PREFIX):])
    stem = filename.rsplit(".", 1)[0]
    return stem or None


def extract_cached_image_urls_from_content(content: str) -> set[str]:
    if not content:
        return set()
    soup = BeautifulSoup(content, "html.parser")
    return {
        img.get("src")
        for img in soup.find_all("img")
        if is_local_cached_image_url(img.get("src"))
    }


def remove_cached_file_for_url(url: str):
    path = cached_image_path_from_url(url)
    if path and os.path.exists(path):
        os.remove(path)


async def release_cached_images(articles: list[dict]) -> int:
    """Drop cache files for articles being removed. Returns files deleted.

    Every path that deletes articles must call this, or their images are
    stranded on disk forever — nothing else ever revisits them.
    """
    from ..database import db

    candidates = set()
    for article in articles:
        main_image = article.get("main_image")
        if is_local_cached_image_url(main_image):
            candidates.add(main_image)
        candidates.update(extract_cached_image_urls_from_content(article.get("content") or ""))

    if not candidates:
        return 0

    referenced_hashes = set()
    for content, main_image in await db.get_article_image_blobs():
        for blob in (content, main_image):
            if blob:
                referenced_hashes.update(_CACHED_HASH_RE.findall(blob))

    removed = 0
    orphaned_hashes = []
    for url in candidates:
        img_hash = cached_image_hash_from_url(url)
        if img_hash not in referenced_hashes:
            remove_cached_file_for_url(url)
            orphaned_hashes.append(img_hash)
            removed += 1
    if orphaned_hashes:
        await db.delete_focal_points(orphaned_hashes)
    return removed


async def sweep_orphaned_cached_images(min_age_seconds: int = 3600) -> tuple[int, int]:
    """Delete cache files no article references. Returns (files, bytes freed).

    Backstop for leaks the per-delete cleanup cannot catch: a parse cancelled
    between caching its images and saving the article never records the URLs
    it wrote, so there is nothing to release at the time. Comparing the whole
    cache against the whole database is the only way to find those.

    min_age_seconds protects a parse that is mid-flight right now — it has
    written its files but not yet saved the article that references them, so
    they are indistinguishable from orphans. The retry loop runs independently
    of the refresh cycle, so that overlap is expected, not hypothetical.
    """
    from ..database import db

    referenced = set()
    for content, main_image in await db.get_article_image_blobs():
        for blob in (content, main_image):
            if blob:
                referenced.update(_CACHED_HASH_RE.findall(blob))

    cutoff = time.time() - min_age_seconds
    removed = 0
    freed = 0
    swept_hashes = []
    for root, _, names in os.walk(ARTICLE_IMAGE_CACHE_DIR):
        for name in names:
            if name.rsplit(".", 1)[0] in referenced:
                continue
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
                if stat.st_mtime > cutoff:
                    continue
                os.remove(path)
            except OSError:
                continue
            removed += 1
            freed += stat.st_size
            swept_hashes.append(name.rsplit(".", 1)[0])

    if swept_hashes:
        await db.delete_focal_points(swept_hashes)
    return removed, freed


async def sweep_derived_image_cache(max_age_seconds: int = 7 * 86400) -> tuple[int, int]:
    """Reclaim proxy/derived image cache files older than max_age_seconds (default 7 days)."""
    if not os.path.exists(IMAGE_CACHE_DIR):
        return 0, 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    freed = 0
    for root, _, names in os.walk(IMAGE_CACHE_DIR):
        for name in names:
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
                if stat.st_mtime > cutoff:
                    continue
                os.remove(path)
            except OSError:
                continue
            removed += 1
            freed += stat.st_size
    return removed, freed


def build_absolute_asset_url(base_url: str, url: str) -> str:
    if not url:
        return url
    if url.startswith(("http://", "https://")):
        return url
    return f"{base_url.rstrip('/')}{url}"


_SRCSET_ATTRS = ("srcset", "data-srcset", "data-lazy-srcset")
_SRCSET_CANDIDATE_RE = re.compile(r"(\S+)(?:\s+([\d.]+)([wx]))?")

# Analytics/ad beacons that some sites embed as <img>. They are not article
# content, and proxying them makes the server fetch ad-tech endpoints per read.
_TRACKING_IMAGE_HOSTS = (
    "scorecardresearch.com",
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "adnxs.com",
    "media.net",
    "valuecommerce.com",
    "adsrvr.org",
    "rubiconproject.com",
    "casalemedia.com",
    "pubmatic.com",
    "openx.net",
    "criteo.com",
    "quantserve.com",
    "atdmt.com",
)
_TRACKING_PATH_HINTS = ("/atrk.gif", "/cksync", "/getuid", "/g/collect", "/pixel", "/beacon")


def absolutize_image_url(src: str, base_url: str) -> str | None:
    """Resolve a possibly relative image src against the article URL.

    Returns None for values that can never be an image (empty, javascript:,
    or parser junk like src="true" seen on HKEPC).
    """
    if not src:
        return None
    src = src.strip()
    if not src or is_local_cached_image_url(src) or src.startswith("data:"):
        return src or None
    if src.startswith(("http://", "https://")):
        return src
    if src.startswith("//"):
        scheme = urlparse(base_url).scheme if base_url else "https"
        return f"{scheme or 'https'}:{src}"
    if src.startswith(("javascript:", "about:", "#")):
        return None
    if not base_url:
        return None
    # Bare tokens with no path separator or extension are parser noise, not URLs.
    if "/" not in src and "." not in src:
        return None
    resolved = urljoin(base_url, src)
    return resolved if resolved.startswith(("http://", "https://")) else None


def best_srcset_candidate(srcset: str) -> str | None:
    """Pick the highest-resolution URL from a srcset value."""
    if not srcset:
        return None
    best_url = None
    best_score = -1.0
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        match = _SRCSET_CANDIDATE_RE.match(part)
        if not match:
            continue
        url = match.group(1)
        if not url:
            continue
        size, unit = match.group(2), match.group(3)
        try:
            score = float(size) if size else 0.0
        except ValueError:
            score = 0.0
        if unit == "x":
            score *= 1000  # normalise density descriptors against width ones
        if best_url is None or score > best_score:
            best_url, best_score = url, score
    return best_url


def image_source_candidates(img) -> list[str]:
    """Every URL that could serve this <img>, best-first.

    Order matters: the real src wins, then the image's own responsive
    candidates, then any <source> of an enclosing <picture> (some sites put the
    only usable URL there and leave the <img> with a placeholder).
    """
    candidates = [img.get("src") or ""]
    for attr in _SRCSET_ATTRS:
        candidates.append(best_srcset_candidate(img.get(attr) or "") or "")
    parent = img.find_parent("picture")
    if parent:
        for source in parent.find_all("source"):
            candidates.append(
                best_srcset_candidate(source.get("srcset") or source.get("data-srcset") or "") or ""
            )
    return [c for c in candidates if c]


def _is_tracking_image_url(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if any(host == h or host.endswith("." + h) for h in _TRACKING_IMAGE_HOSTS):
        return True
    return any(hint in parsed.path.lower() for hint in _TRACKING_PATH_HINTS)


def _is_pixel_sized(img) -> bool:
    for attr in ("width", "height"):
        value = (img.get(attr) or "").strip().rstrip("px")
        if value.isdigit() and int(value) <= 1:
            return True
    return False


def _is_svg_image_url(url: str) -> bool:
    """SVG <img> is site chrome — icons, logos, social buttons, spinners.

    Article photography is never SVG, and the parser already strips inline
    <svg> for the same reason. Only the path decides, so a photo whose query
    string happens to mention an icon is left alone.
    """
    if not url:
        return False
    if url.startswith("data:image/svg"):
        return True
    path = urlparse(url).path if "://" in url else url.split("?")[0].split("#")[0]
    return path.lower().endswith((".svg", ".svgz"))


def remove_non_content_images(soup) -> int:
    """Drop analytics beacons, 1x1 pixels and SVG chrome from article content."""
    removed = 0
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if _is_tracking_image_url(src) or _is_pixel_sized(img) or _is_svg_image_url(src):
            img.decompose()
            removed += 1
    return removed


def strip_overriding_sources(soup) -> int:
    """Remove responsive sources that would override an already-cached <img src>.

    The browser prefers srcset/<source> over src, so an image with a cached src
    but a remote srcset still downloads from the origin — defeating the cache and
    leaving nothing for offline mode. Cached files are already normalised to
    <=1200px JPEG, so responsive variants add nothing.
    """
    stripped = 0
    for img in soup.find_all("img"):
        if not is_local_cached_image_url(img.get("src")):
            continue
        touched = False
        for attr in _SRCSET_ATTRS:
            value = img.get(attr)
            if value and not is_local_cached_image_url(best_srcset_candidate(value) or ""):
                del img[attr]
                touched = True
        parent = img.find_parent("picture")
        if parent:
            for source in parent.find_all("source"):
                candidate = best_srcset_candidate(
                    source.get("srcset") or source.get("data-srcset") or ""
                )
                if candidate and not is_local_cached_image_url(candidate):
                    source.decompose()
                    touched = True
        if touched:
            stripped += 1
    return stripped


async def cache_article_images(content: str, main_image: str, base_url: str = "") -> tuple[str, str]:
    rewritten_content = content or ""
    rewritten_main_image = main_image or ""

    if rewritten_content:
        soup = BeautifulSoup(rewritten_content, "html.parser")
        remove_non_content_images(soup)

        for img in soup.find_all("img"):
            resolved = None
            for candidate in image_source_candidates(img):
                resolved = absolutize_image_url(candidate, base_url)
                if resolved:
                    break
            if not resolved:
                continue
            if resolved != img.get("src"):
                img["src"] = resolved
            if not _is_remote_image_url(resolved):
                continue
            cached_url = await cache_remote_image(resolved)
            if cached_url:
                img["src"] = cached_url

        strip_overriding_sources(soup)
        rewritten_content = str(soup)

    rewritten_main_image = absolutize_image_url(rewritten_main_image, base_url) or ""
    if _is_remote_image_url(rewritten_main_image):
        cached_main_image = await cache_remote_image(rewritten_main_image)
        if cached_main_image:
            rewritten_main_image = cached_main_image

    return rewritten_content, rewritten_main_image


async def cache_remote_image(url: str) -> str | None:
    if not _is_remote_image_url(url):
        return None

    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()

    existing = _find_existing_cached_file(url_hash)
    if existing:
        return f"{CACHED_IMAGE_URL_PREFIX}{existing}"

    try:
        raw_bytes = await _download_image_bytes(url)
        if not raw_bytes:
            return None

        ext, out_bytes, focal = await asyncio.to_thread(
            _prepare_cached_image, url_hash, raw_bytes, url
        )
        filename = f"{url_hash}.{ext}"
        cache_path = os.path.join(ARTICLE_IMAGE_CACHE_DIR, filename)
        if not os.path.exists(cache_path):
            with open(cache_path, "wb") as f:
                f.write(out_bytes)

        # A failed focal write must not lose the cached image — the reader falls
        # back to 50/50, which is what it used before this existed.
        try:
            from ..database import db
            await db.set_focal_point(url_hash, focal[0], focal[1])
        except Exception:
            pass

        return f"{CACHED_IMAGE_URL_PREFIX}{filename}"
    except Exception:
        return None


async def _download_image_bytes(url: str) -> bytes:
    proxy_headers = HEADERS.copy()
    parsed_url = urlparse(url)
    proxy_headers["Referer"] = f"{parsed_url.scheme}://{parsed_url.netloc}/"

    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url, headers=proxy_headers)
                if response.status_code in (403, 503):
                    solution = await asyncio.to_thread(get_flaresolverr_solution, url, 45000)
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
                        retry = await client.get(url, headers=retry_headers, cookies=cookie_jar)
                        retry.raise_for_status()
                        return retry.content
                response.raise_for_status()
                return response.content
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
            if attempt + 1 < max_attempts:
                await asyncio.sleep(0.5)
                continue
            raise


def _prepare_cached_image(url_hash: str, raw_bytes: bytes, url: str) -> tuple[str, bytes, tuple[int, int]]:
    # Animated GIFs used to be stored raw, which made 0.3% of files 21% of the
    # cache (single files reached 50 MB). Pillow opens them on frame 0, so
    # falling through re-encodes that first frame and drops the animation.
    image = Image.open(io.BytesIO(raw_bytes))

    max_dimension, quality = _image_encode_settings()
    image = _normalize_image_mode(image)
    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    # Computed here because the image is already decoded and already in a worker
    # thread — a second pass would mean decoding every image twice.
    try:
        focal = compute_focal_point(image)
    except Exception:
        focal = DEFAULT_FOCAL

    out_io = io.BytesIO()
    image.save(out_io, format="JPEG", quality=quality, optimize=True)
    return "jpg", out_io.getvalue(), focal


def _image_encode_settings() -> tuple[int, int]:
    """Dimension/quality from system settings, falling back to the defaults."""
    try:
        from ..database import db
        s = db.get_system_settings_sync()
        return (
            int(s.get("image_max_dimension") or MAX_IMAGE_DIMENSION),
            int(s.get("image_jpeg_quality") or JPEG_QUALITY),
        )
    except Exception:
        return MAX_IMAGE_DIMENSION, JPEG_QUALITY


def _normalize_image_mode(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA")
        background = Image.new("RGB", alpha.size, (255, 255, 255))
        background.paste(alpha, mask=alpha.getchannel("A"))
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _find_existing_cached_file(url_hash: str) -> str | None:
    for ext in ("jpg", "gif", "png", "webp"):
        filename = f"{url_hash}.{ext}"
        if os.path.exists(os.path.join(ARTICLE_IMAGE_CACHE_DIR, filename)):
            return filename
    return None


def _is_remote_image_url(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    if any(ch in url for ch in _INVALID_REMOTE_URL_CHARS):
        return False

    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


_PAGE_IMAGE_URL_RE = re.compile(r"https?://[^\s\",'<>]+")
_LAZY_IMG_ATTRS = (
    "src", "data-src", "data-original", "data-lazy", "data-lazy-src",
    "srcset", "data-srcset",
)


def is_signed_expiring_url(url: str) -> bool:
    """True if the URL carries a time-limited signature (CloudFront / S3 presigned).

    Such URLs return 403 once the signature expires, so an image still pointing at
    one — because caching failed while the signature was briefly valid — will break.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False
    return (
        ("Signature=" in url and "Expires=" in url)
        or "Key-Pair-Id=" in url
        or "X-Amz-Signature=" in url
    )


def content_has_uncached_signed_images(content: str, main_image: str = "") -> bool:
    """True if the article still references signed/expiring images that aren't cached."""
    if is_signed_expiring_url(main_image) and not is_local_cached_image_url(main_image):
        return True
    if not content:
        return False
    soup = BeautifulSoup(content, "html.parser")
    return any(
        is_signed_expiring_url(img.get("src")) and not is_local_cached_image_url(img.get("src"))
        for img in soup.find_all("img")
    )


def _image_urls_by_path(page_html: str) -> dict[str, str]:
    """Map url-path -> full image URL from a page.

    Signed URLs keep a stable path (the CDN object key) while their query-string
    signature rotates, so matching stored images to fresh ones by path survives
    signature regeneration.
    """
    result: dict[str, str] = {}
    if not page_html:
        return result
    soup = BeautifulSoup(page_html, "html.parser")
    for img in soup.find_all("img"):
        for attr in _LAZY_IMG_ATTRS:
            value = img.get(attr)
            if not value:
                continue
            for candidate in _PAGE_IMAGE_URL_RE.findall(value):
                key = urlparse(candidate).path
                if key:
                    result.setdefault(key, candidate)
    return result


async def recache_signed_images(content: str, main_image: str, page_html: str) -> tuple[str, str, int]:
    """Re-cache expiring signed images using fresh URLs from the page (matched by path).

    Returns (new_content, new_main_image, recached_count). Images that are already
    cached, not signed/expiring, or whose path is absent from the fresh page are left
    untouched. A no-op (count 0) when the page still serves expired signatures —
    caching simply fails until the origin regenerates them.
    """
    fresh_by_path = _image_urls_by_path(page_html)
    recached = 0

    async def _swap(src: str) -> str | None:
        nonlocal recached
        if not is_signed_expiring_url(src) or is_local_cached_image_url(src):
            return None
        fresh_url = fresh_by_path.get(urlparse(src).path)
        if not fresh_url:
            return None
        cached_url = await cache_remote_image(fresh_url)
        if cached_url:
            recached += 1
            return cached_url
        return None

    new_content = content or ""
    if new_content:
        soup = BeautifulSoup(new_content, "html.parser")
        for img in soup.find_all("img"):
            swapped = await _swap(img.get("src"))
            if swapped:
                img["src"] = swapped
        new_content = str(soup)

    new_main_image = main_image or ""
    swapped_main = await _swap(new_main_image)
    if swapped_main:
        new_main_image = swapped_main

    return new_content, new_main_image, recached
