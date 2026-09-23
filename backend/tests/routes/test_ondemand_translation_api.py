"""The on-demand translation endpoint: eligibility, event order, image rewriting."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.routes.reader as reader_module
import app.services.ondemand_translation as od_module
from app.database import Database
from app.main import app
from app.services import translation


@pytest.fixture(autouse=True)
def clear_registry():
    od_module.clear()
    yield
    od_module.clear()


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_ondemand_api.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(reader_module, "db", database)
    monkeypatch.setattr(od_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client(test_db):
    return TestClient(app)


def _seed_article(test_db, lang, content, title="Sample Title", url="https://example.com/a"):
    async def _run():
        source_data = {
            "name": f"Feed {lang}",
            "url": f"https://example.com/feed_{lang}",
        }
        if lang is not None:
            source_data["detected_language"] = lang
        sid = await test_db.create_feed_source(source_data)
        await test_db.insert_feed_articles(sid, [{"url": url}])
        conn = await test_db._get_db()
        cursor = await conn.execute(
            "SELECT id FROM feed_articles WHERE source_id = ?", (sid,)
        )
        aid = (await cursor.fetchone())["id"]
        await test_db.update_article_parsed(aid, title, content, "2026-09-22", "")
        return aid
    return asyncio.run(_run())


@pytest.fixture
def english_feed_article(test_db):
    return _seed_article(test_db, "en", "<p>One</p><p>Two</p>")


@pytest.fixture
def chinese_feed_article(test_db):
    return _seed_article(test_db, "zh-TW", "<p>中文</p>")


@pytest.fixture
def undetected_feed_article(test_db):
    return _seed_article(test_db, None, "<p>Undetected</p>")


@pytest.fixture
def translated_article(test_db):
    content = translation.badge_html("Qwen-MT-flash") + "<p>Already translated</p>"
    return _seed_article(test_db, "en", content)


@pytest.fixture
def image_article(test_db):
    content = '<p>Photo: <img src="https://cdn.example.com/photo.jpg" alt="test"></p>'
    return _seed_article(test_db, "en", content, url="https://example.com/photo_article")


@pytest.fixture(autouse=True)
def stub_translation(monkeypatch):
    def _fake_iter(html, target_language="zh-TW", translator="qwen"):
        if "photo.jpg" in html:
            yield {
                "type": "block",
                "index": 0,
                "html": '<blockquote><p>Photo: <img src="https://cdn.example.com/photo.jpg"></p></blockquote><p>照片：<img src="https://cdn.example.com/photo.jpg"></p>',
            }
            yield {
                "type": "result",
                "html": '<p>translated</p>',
                "provider": "Qwen-MT-flash",
                "total": 1,
            }
        else:
            yield {"type": "block", "index": 0, "html": "<blockquote><p>One</p></blockquote><p>一</p>"}
            yield {"type": "block", "index": 1, "html": "<blockquote><p>Two</p></blockquote><p>二</p>"}
            yield {
                "type": "result",
                "html": "<p>translated body</p>",
                "provider": "Qwen-MT-flash",
                "total": 2,
            }

    monkeypatch.setattr(translation, "translate_html_iter", _fake_iter)
    monkeypatch.setattr(translation, "translate_text", lambda text, target_language, *a, **k: ("中文標題", "Qwen-MT-flash"))
    monkeypatch.setattr(od_module.db, "save_ondemand_translation", AsyncMock())
    monkeypatch.setattr(od_module.db, "log_translation_usage", AsyncMock())


def _lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_unknown_article_is_404(client):
    assert client.post("/api/reader/articles/999999/translate").status_code == 404


def test_an_already_translated_article_is_409(client, translated_article):
    r = client.post(f"/api/reader/articles/{translated_article}/translate")
    assert r.status_code == 409


def test_a_chinese_feed_is_409(client, chinese_feed_article):
    r = client.post(f"/api/reader/articles/{chinese_feed_article}/translate")
    assert r.status_code == 409


def test_an_english_feed_is_eligible(client, english_feed_article):
    r = client.post(f"/api/reader/articles/{english_feed_article}/translate")
    assert r.status_code == 200


def test_a_feed_with_no_detected_language_is_eligible(client, undetected_feed_article):
    """detected_language is unset on most feeds; the button must still appear."""
    r = client.post(f"/api/reader/articles/{undetected_feed_article}/translate")
    assert r.status_code == 200


def test_the_stream_is_ndjson_in_order(client, english_feed_article, stub_translation):
    r = client.post(f"/api/reader/articles/{english_feed_article}/translate")
    events = _lines(r)
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done"
    assert [e["index"] for e in events if e["type"] == "block"] == [0, 1]


def test_streamed_blocks_are_image_rewritten(client, image_article, stub_translation):
    """A streamed block must go through the same proxy rewrite the detail route applies."""
    r = client.post(f"/api/reader/articles/{image_article}/translate")
    blocks = [e for e in _lines(r) if e["type"] == "block"]
    assert blocks
    assert "https://cdn.example.com/photo.jpg" not in blocks[0]["html"]
    assert "/api/reader/image-proxy" in blocks[0]["html"]
