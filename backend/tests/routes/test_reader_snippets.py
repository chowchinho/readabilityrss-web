import asyncio

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import app
from app.services.snippets import build_snippets
import app.routes.reader as reader_module


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_snippets.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(reader_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client():
    return TestClient(app)


def _seed(test_db, content):
    async def _run():
        sid = await test_db.create_feed_source(
            {"name": "Feed S", "url": "http://example.com/s/rss"}
        )
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/s/1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute(
            "SELECT id FROM feed_articles WHERE source_id = ?", (sid,)
        )
        aid = (await cursor.fetchone())["id"]
        await test_db.update_article_parsed(aid, "S1", content, "2026-08-14", "")
        return aid
    return asyncio.run(_run())


def _set_snippets(test_db, article_id, snippet, featured):
    async def _run():
        conn = await test_db._get_db()
        await conn.execute(
            "UPDATE feed_articles SET snippet = ?, featured_snippet = ? WHERE id = ?",
            (snippet, featured, article_id),
        )
        await conn.commit()
    asyncio.run(_run())


def _find(articles, article_id):
    for article in articles:
        if article["id"] == article_id:
            return article
    raise AssertionError(f"article {article_id} not in response")


def test_stored_snippet_is_returned_verbatim(test_db, client):
    """A stored value must be served as-is, not regenerated."""
    article_id = _seed(test_db, "<p>Stored body.</p>")
    _set_snippets(test_db, article_id, "SENTINEL", "SENTINEL FEATURED")

    resp = client.get("/api/reader/articles?limit=50")
    article = _find(resp.json()["articles"], article_id)
    assert article["snippet"] == "SENTINEL"
    assert article["featured_snippet"] == "SENTINEL FEATURED"


def test_null_columns_fall_back_to_building_on_the_fly(test_db, client):
    content = "<p>Fallback body text.</p>"
    article_id = _seed(test_db, content)
    _set_snippets(test_db, article_id, None, None)

    resp = client.get("/api/reader/articles?limit=50")
    article = _find(resp.json()["articles"], article_id)
    expected_snippet, expected_featured = build_snippets(content)
    assert article["snippet"] == expected_snippet
    assert article["featured_snippet"] == expected_featured
