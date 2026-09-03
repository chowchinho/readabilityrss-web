import io
import shutil
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup
from PIL import Image

from app.database import Database
from app.services import article_image_cache as cache_mod


def _jpeg_bytes(color=(10, 20, 30), size=(1600, 900)):
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_cache_article_images_rewrites_local_urls(monkeypatch):
    cache_dir = Path("backend/data/test_article_image_cache")
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cache_mod, "ARTICLE_IMAGE_CACHE_DIR", str(cache_dir))

    async def fake_download(_url):
        return _jpeg_bytes()

    monkeypatch.setattr(cache_mod, "_download_image_bytes", fake_download)

    content = '<p>Hello</p><img src="https://example.com/body.jpg">'
    new_content, new_main = await cache_mod.cache_article_images(content, "https://example.com/main.png")

    assert cache_mod.CACHED_IMAGE_URL_PREFIX in new_content
    assert new_main.startswith(cache_mod.CACHED_IMAGE_URL_PREFIX)
    assert any(cache_dir.iterdir())
    shutil.rmtree(cache_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def temp_db():
    db_path = Path("backend/data") / f"test-cache-{uuid.uuid4().hex}.db"
    db = Database(db_path=str(db_path))
    await db.init()
    yield db
    await db.close()
    if db_path.exists():
        db_path.unlink()


@pytest.mark.asyncio
async def test_database_prunable_articles_and_references(temp_db):
    db = temp_db
    source_id = await db.create_feed_source({"name": "Source", "url": "https://example.com"})

    for idx in range(3):
        inserted = await db.insert_feed_article(source_id, f"https://example.com/{idx}")
        assert inserted
        image_url = f"/api/reader/cached-image/{idx}.jpg"
        content = f'<p>Article {idx}</p><img src="{image_url}">'
        await db.update_article_parsed(idx + 1, f"Title {idx}", content, "2026-03-31", image_url)

    conn = await db._get_db()
    for article_id, created_at in (
        (1, "2026-03-31 10:00:00"),
        (2, "2026-03-31 11:00:00"),
        (3, "2026-03-31 12:00:00"),
    ):
        await conn.execute(
            "UPDATE feed_articles SET created_at = ?, updated_at = ? WHERE id = ?",
            (created_at, created_at, article_id)
        )
    await conn.commit()

    prunable = await db.get_prunable_articles(source_id, keep=2)
    assert len(prunable) == 1
    assert prunable[0]["id"] == 1
    assert await db.has_article_image_reference("/api/reader/cached-image/0.jpg")

    await db.delete_old_articles(source_id, keep=2)
    assert not await db.has_article_image_reference("/api/reader/cached-image/0.jpg")


def test_is_remote_image_url_rejects_malformed_urls():
    assert cache_mod._is_remote_image_url("https://example.com/image.jpg")
    assert not cache_mod._is_remote_image_url("https://asset.watch.impress.co.jp/img/foo.jpg< src=")
    assert not cache_mod._is_remote_image_url("https://asset.watch.impress.co.jp/img/foo.jpg bar")


def test_is_signed_expiring_url():
    cloudfront = "https://d3.cloudfront.net/a/b.jpg?Expires=1779896210&Key-Pair-Id=K7&Signature=abc"
    s3 = "https://bucket.s3.amazonaws.com/x.jpg?X-Amz-Signature=deadbeef&X-Amz-Expires=3600"
    assert cache_mod.is_signed_expiring_url(cloudfront)
    assert cache_mod.is_signed_expiring_url(s3)
    assert not cache_mod.is_signed_expiring_url("https://example.com/plain.jpg")
    assert not cache_mod.is_signed_expiring_url("/api/reader/cached-image/abc.jpg")
    assert not cache_mod.is_signed_expiring_url("")


def test_content_has_uncached_signed_images():
    signed = "https://cf.net/p/img1.jpg?Expires=1&Key-Pair-Id=K&Signature=s"
    assert cache_mod.content_has_uncached_signed_images(f'<img src="{signed}">')
    assert cache_mod.content_has_uncached_signed_images("", main_image=signed)
    assert not cache_mod.content_has_uncached_signed_images('<img src="https://cf.net/p/img1.jpg">')
    assert not cache_mod.content_has_uncached_signed_images('<img src="/api/reader/cached-image/x.jpg">')
    assert not cache_mod.content_has_uncached_signed_images("")


@pytest.mark.asyncio
async def test_recache_signed_images_matches_by_path(monkeypatch):
    async def fake_cache(_url):
        return "/api/reader/cached-image/CACHED.jpg"
    monkeypatch.setattr(cache_mod, "cache_remote_image", fake_cache)

    path = "/hobby/jp/product/abc/def1.jpg"
    expired = f"https://cf.net{path}?Expires=1&Key-Pair-Id=K&Signature=OLD"
    fresh = f"https://cf.net{path}?Expires=9999999999&Key-Pair-Id=K&Signature=NEW"
    plain = "https://other.com/keep.jpg"
    content = f'<img src="{expired}"><img src="{plain}">'
    page_html = f'<html><body><img src="{fresh}"></body></html>'

    new_content, new_main, recached = await cache_mod.recache_signed_images(content, expired, page_html)

    assert recached == 2  # one content image + the main_image (same path)
    assert "/api/reader/cached-image/CACHED.jpg" in new_content
    assert "Signature=OLD" not in new_content
    assert plain in new_content  # non-signed image left untouched
    assert new_main == "/api/reader/cached-image/CACHED.jpg"


@pytest.mark.asyncio
async def test_recache_signed_images_no_match_leaves_unchanged(monkeypatch):
    async def fake_cache(_url):
        return "/api/reader/cached-image/X.jpg"
    monkeypatch.setattr(cache_mod, "cache_remote_image", fake_cache)

    expired = "https://cf.net/p/a.jpg?Expires=1&Key-Pair-Id=K&Signature=OLD"
    content = f'<img src="{expired}">'
    # Fresh page only has a different path -> no match -> nothing recached.
    page_html = '<img src="https://cf.net/p/OTHER.jpg?Expires=9&Key-Pair-Id=K&Signature=NEW">'

    new_content, _new_main, recached = await cache_mod.recache_signed_images(content, "", page_html)

    assert recached == 0
    assert "Signature=OLD" in new_content


@pytest.mark.asyncio
async def test_recache_signed_images_skips_when_cache_fails(monkeypatch):
    # Simulates the page still serving an expired signature: download fails, returns None.
    async def fake_cache(_url):
        return None
    monkeypatch.setattr(cache_mod, "cache_remote_image", fake_cache)

    path = "/p/a.jpg"
    expired = f"https://cf.net{path}?Expires=1&Key-Pair-Id=K&Signature=OLD"
    fresh = f"https://cf.net{path}?Expires=2&Key-Pair-Id=K&Signature=ALSO_EXPIRED"
    content = f'<img src="{expired}">'
    page_html = f'<img src="{fresh}">'

    new_content, _new_main, recached = await cache_mod.recache_signed_images(content, "", page_html)

    assert recached == 0
    assert "Signature=OLD" in new_content  # unchanged until a fresh signature works


# --- responsive-source stripping (srcset / <picture><source>) ---


def test_strip_overriding_sources_drops_remote_srcset_when_src_cached():
    html = (
        '<img src="/api/reader/cached-image/a.jpg" '
        'srcset="https://cdn.example.com/a-320.jpg 320w, https://cdn.example.com/a-640.jpg 640w">'
    )
    soup = BeautifulSoup(html, "html.parser")
    assert cache_mod.strip_overriding_sources(soup) == 1
    img = soup.find("img")
    assert img.get("srcset") is None
    assert img["src"] == "/api/reader/cached-image/a.jpg"


def test_strip_overriding_sources_removes_picture_source():
    html = (
        "<picture>"
        '<source srcset="https://cdn.example.com/a.webp" type="image/webp">'
        '<img src="/api/reader/cached-image/a.jpg">'
        "</picture>"
    )
    soup = BeautifulSoup(html, "html.parser")
    assert cache_mod.strip_overriding_sources(soup) == 1
    assert soup.find("source") is None
    assert soup.find("img")["src"] == "/api/reader/cached-image/a.jpg"


def test_strip_overriding_sources_keeps_srcset_when_src_not_cached():
    # Nothing cached to fall back on — dropping srcset would lose the image.
    html = '<img src="https://cdn.example.com/a.jpg" srcset="https://cdn.example.com/a-2x.jpg 2x">'
    soup = BeautifulSoup(html, "html.parser")
    assert cache_mod.strip_overriding_sources(soup) == 0
    assert soup.find("img").get("srcset") is not None


def test_strip_overriding_sources_leaves_cached_srcset_alone():
    html = (
        '<img src="/api/reader/cached-image/a.jpg" '
        'srcset="/api/reader/cached-image/a.jpg 1x">'
    )
    soup = BeautifulSoup(html, "html.parser")
    assert cache_mod.strip_overriding_sources(soup) == 0
    assert soup.find("img").get("srcset") is not None


# --- srcset candidate selection ---


def test_best_srcset_candidate_prefers_largest_width():
    srcset = "https://x.com/s.jpg 320w, https://x.com/l.jpg 1280w, https://x.com/m.jpg 640w"
    assert cache_mod.best_srcset_candidate(srcset) == "https://x.com/l.jpg"


def test_best_srcset_candidate_falls_back_to_first():
    assert cache_mod.best_srcset_candidate("https://x.com/a.jpg") == "https://x.com/a.jpg"
    assert cache_mod.best_srcset_candidate("") is None


# --- relative / protocol-relative absolutisation ---


def test_absolutize_image_url():
    base = "https://www.nippon.com/ja/japan-topics/g02621/"
    assert (
        cache_mod.absolutize_image_url("/ja/img/x.jpg", base)
        == "https://www.nippon.com/ja/img/x.jpg"
    )
    # protocol-relative (TAMIYA / BANDAI pattern)
    assert (
        cache_mod.absolutize_image_url("//d7z.cloudfront.net/a.jpg", base)
        == "https://d7z.cloudfront.net/a.jpg"
    )
    # already absolute, cached, and junk values are left alone
    assert cache_mod.absolutize_image_url("https://x.com/a.jpg", base) == "https://x.com/a.jpg"
    assert cache_mod.absolutize_image_url("/api/reader/cached-image/a.jpg", base) == "/api/reader/cached-image/a.jpg"
    assert cache_mod.absolutize_image_url("true", base) is None
    assert cache_mod.absolutize_image_url("", base) is None
    assert cache_mod.absolutize_image_url("/x.jpg", "") is None


# --- tracking pixel removal ---


def test_remove_non_content_images():
    html = (
        '<p>Body</p>'
        '<img src="https://sb.scorecardresearch.com/p/?c1=2&c2=100">'
        '<img src="https://www.google-analytics.com/g/collect?v=2">'
        '<img src="https://ib.adnxs.com/getuid?x">'
        '<img src="https://cs.media.net/cksync?cs=3">'
        '<img src="https://example.com/real.jpg" width="1" height="1">'
        '<img src="https://example.com/keep.jpg">'
    )
    soup = BeautifulSoup(html, "html.parser")
    assert cache_mod.remove_non_content_images(soup) == 5
    remaining = [i["src"] for i in soup.find_all("img")]
    assert remaining == ["https://example.com/keep.jpg"]


def test_removes_svg_images_as_site_chrome():
    """SVG <img> is site chrome — icons, logos, spinners — never article content.

    Matches the parser, which already strips inline <svg>.
    """
    html = (
        '<img src="https://web-mu.jp/images/common/sns_x.svg">'
        '<img src="https://hobby.dengeki.com/common/img/kadokawa-group.svg">'
        '<img src="https://www.harpersbazaar.com/icons/watch-on-youtube.1ebfe98.svg?primary=%2523fff">'
        '<img src="https://example.com/logo.SVGZ">'
        '<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">'
        '<img src="https://example.com/photo.jpg">'
    )
    soup = BeautifulSoup(html, "html.parser")
    assert cache_mod.remove_non_content_images(soup) == 5
    assert [i["src"] for i in soup.find_all("img")] == ["https://example.com/photo.jpg"]


def test_svg_removal_does_not_misfire_on_lookalike_urls():
    """Only the URL path decides — a photo is not chrome because 'svg' appears elsewhere."""
    html = (
        '<img src="https://cdn.example.com/photos/svg-conference-2026.jpg">'
        '<img src="https://cdn.example.com/a.jpg?ref=logo.svg">'
        '<img src="https://svg.example.com/real-photo.png">'
    )
    soup = BeautifulSoup(html, "html.parser")
    assert cache_mod.remove_non_content_images(soup) == 0
    assert len(soup.find_all("img")) == 3


@pytest.mark.asyncio
async def test_cache_article_images_end_to_end_sanitises(monkeypatch):
    """src absolutised + cached, srcset dropped, <source> removed, tracker stripped."""
    async def fake_cache(url):
        assert url.startswith("http"), f"uncached relative url leaked: {url}"
        return "/api/reader/cached-image/CACHED.jpg"
    monkeypatch.setattr(cache_mod, "cache_remote_image", fake_cache)

    content = (
        '<img src="/media/a.jpg" srcset="https://cdn.toretabi.jp/a-2x.jpg 2x">'
        '<picture><source srcset="https://cdn.x.com/b.webp"><img src="//cdn.x.com/b.jpg"></picture>'
        '<img src="https://sb.scorecardresearch.com/p/?c1=2">'
    )
    new_content, new_main = await cache_mod.cache_article_images(
        content, "//cdn.x.com/main.jpg", base_url="https://www.toretabi.jp/travel_info/entry-1.html"
    )

    assert "scorecardresearch" not in new_content
    assert "srcset" not in new_content
    assert "<source" not in new_content
    assert "cdn.toretabi.jp" not in new_content
    assert new_content.count("/api/reader/cached-image/CACHED.jpg") == 2
    assert new_main == "/api/reader/cached-image/CACHED.jpg"


@pytest.mark.asyncio
async def test_cache_article_images_recovers_src_from_srcset(monkeypatch):
    """The rare <img> with no usable src: promote the best srcset candidate, then strip."""
    async def fake_cache(url):
        return "/api/reader/cached-image/FROM_SRCSET.jpg" if "large" in url else None
    monkeypatch.setattr(cache_mod, "cache_remote_image", fake_cache)

    content = '<img srcset="https://x.com/small.jpg 320w, https://x.com/large.jpg 1280w">'
    new_content, _ = await cache_mod.cache_article_images(content, "", base_url="https://x.com/a")

    assert "/api/reader/cached-image/FROM_SRCSET.jpg" in new_content
    assert "srcset" not in new_content


def test_cached_image_hash_from_url_extracts_stem():
    from app.services.article_image_cache import cached_image_hash_from_url
    assert cached_image_hash_from_url("/api/reader/cached-image/abc123.jpg") == "abc123"


def test_cached_image_hash_from_url_rejects_remote_urls():
    from app.services.article_image_cache import cached_image_hash_from_url
    assert cached_image_hash_from_url("https://example.com/photo.jpg") is None
    assert cached_image_hash_from_url("") is None
    assert cached_image_hash_from_url(None) is None


def test_prepare_cached_image_returns_focal_point():
    """The focal point rides along with the encode — the image is decoded once."""
    import io
    from PIL import Image
    from app.services.article_image_cache import _prepare_cached_image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 140, 120)).save(buffer, format="JPEG")

    ext, out_bytes, focal = _prepare_cached_image(
        "somehash", buffer.getvalue(), "https://example.com/a.jpg"
    )

    assert ext == "jpg"
    assert out_bytes
    assert isinstance(focal, tuple) and len(focal) == 2
    assert all(0 <= value <= 100 for value in focal)


@pytest.mark.asyncio
async def test_download_image_bytes_retries_transient_error(monkeypatch):
    import httpx
    from unittest.mock import AsyncMock, MagicMock
    from app.services.article_image_cache import _download_image_bytes

    calls = 0
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake-image-bytes"
    mock_resp.raise_for_status = MagicMock()

    async def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("Network blip")
        return mock_resp

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await _download_image_bytes("https://example.com/test.jpg")
    assert result == b"fake-image-bytes"
    assert calls == 2

