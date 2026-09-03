import io
import os
import shutil
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image

from app.database import Database
from app.services import article_image_cache as cache_mod


def _jpeg_bytes(color=(10, 20, 30), size=(64, 64)):
    img = Image.new("RGB", color=color, size=size)
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


@pytest_asyncio.fixture
async def env(monkeypatch, tmp_path):
    cache_dir = tmp_path / "article_image_cache"
    cache_dir.mkdir()
    monkeypatch.setattr(cache_mod, "ARTICLE_IMAGE_CACHE_DIR", str(cache_dir))

    db = Database(db_path=str(tmp_path / "feeds.db"))
    await db.init()
    monkeypatch.setattr(cache_mod, "db", db, raising=False)
    import app.database as database_mod
    monkeypatch.setattr(database_mod, "db", db)

    yield db, cache_dir
    await db.close()
    shutil.rmtree(cache_dir, ignore_errors=True)


def _write_cached(cache_dir: Path, url_hash: str, age_seconds: int = 7200) -> Path:
    path = cache_dir / f"{url_hash}.jpg"
    path.write_bytes(_jpeg_bytes())
    # The sweep ignores recently written files, so age fixtures past that guard.
    old = time.time() - age_seconds
    os.utime(path, (old, old))
    return path


async def _add_article(db, source_id: int, url: str, content: str, main_image: str = ""):
    await db.insert_feed_article(source_id, url)
    articles = await db.get_pending_articles(source_id=source_id)
    article = [a for a in articles if a["url"] == url][0]
    await db.update_article_parsed(article["id"], "t", content, "", main_image)
    return article["id"]


@pytest.mark.asyncio
async def test_release_cached_images_removes_unreferenced_files(env):
    db, cache_dir = env
    source_id = await db.create_feed_source({"name": "s", "url": "http://e.com"})
    h = uuid.uuid4().hex
    path = _write_cached(cache_dir, h)
    article = {"content": f'<img src="/api/reader/cached-image/{h}.jpg">', "main_image": ""}

    removed = await cache_mod.release_cached_images([article])

    assert removed == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_release_cached_images_keeps_files_another_article_still_uses(env):
    db, cache_dir = env
    source_id = await db.create_feed_source({"name": "s", "url": "http://e.com"})
    h = uuid.uuid4().hex
    path = _write_cached(cache_dir, h)
    cached_url = f"/api/reader/cached-image/{h}.jpg"
    await _add_article(db, source_id, "http://e.com/keep", f'<img src="{cached_url}">')

    removed = await cache_mod.release_cached_images([{"content": f'<img src="{cached_url}">', "main_image": ""}])

    assert removed == 0
    assert path.exists()


@pytest.mark.asyncio
async def test_release_cached_images_handles_main_image_and_null_content(env):
    db, cache_dir = env
    await db.create_feed_source({"name": "s", "url": "http://e.com"})
    h = uuid.uuid4().hex
    path = _write_cached(cache_dir, h)

    removed = await cache_mod.release_cached_images(
        [{"content": None, "main_image": f"/api/reader/cached-image/{h}.jpg"}]
    )

    assert removed == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_sweep_removes_files_no_article_references(env):
    db, cache_dir = env
    source_id = await db.create_feed_source({"name": "s", "url": "http://e.com"})
    kept_hash = uuid.uuid4().hex
    orphan_hash = uuid.uuid4().hex
    kept = _write_cached(cache_dir, kept_hash)
    orphan = _write_cached(cache_dir, orphan_hash)
    await _add_article(
        db, source_id, "http://e.com/a",
        f'<img src="/api/reader/cached-image/{kept_hash}.jpg">',
    )

    removed, freed = await cache_mod.sweep_orphaned_cached_images()

    assert removed == 1
    assert freed > 0
    assert kept.exists()
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_sweep_spares_files_written_by_an_in_flight_parse(env):
    """A parse caches images before saving the article that references them.

    The retry loop runs independently of the refresh cycle, so the sweep can
    fire in that gap. Without the age guard it would delete images that are
    about to be referenced, leaving the saved article showing broken pictures.
    """
    db, cache_dir = env
    await db.create_feed_source({"name": "s", "url": "http://e.com"})
    just_written = _write_cached(cache_dir, uuid.uuid4().hex, age_seconds=0)

    removed, _ = await cache_mod.sweep_orphaned_cached_images()

    assert removed == 0
    assert just_written.exists()


@pytest.mark.asyncio
async def test_sweep_keeps_files_referenced_only_by_main_image(env):
    db, cache_dir = env
    source_id = await db.create_feed_source({"name": "s", "url": "http://e.com"})
    h = uuid.uuid4().hex
    path = _write_cached(cache_dir, h)
    await _add_article(
        db, source_id, "http://e.com/a", "<p>no images</p>",
        main_image=f"/api/reader/cached-image/{h}.jpg",
    )

    removed, _ = await cache_mod.sweep_orphaned_cached_images()

    assert removed == 0
    assert path.exists()
