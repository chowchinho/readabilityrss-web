import asyncio
import json
import re
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import app.main as main_module
import app.routes.reader as reader_module
import app.services.ondemand_translation as od_module
import app.utils.fever_key as fever_key_module
import app.utils.session as session_module
from app.database import Database
from app.main import app
from app.routes.auth import _hash_password
from app.services import translation

ORIGINAL_TRANSLATE_HTML_ITER = translation.translate_html_iter


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
    monkeypatch.setattr(fever_key_module, "db", database)
    monkeypatch.setattr(session_module, "db", database)
    monkeypatch.setattr(main_module, "db", database)
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
                "type": "source",
                "html": '<p data-tb="0">Photo: <img src="https://cdn.example.com/photo.jpg" alt="test"></p>',
            }
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
            yield {
                "type": "source",
                "html": '<p data-tb="0">One</p><p data-tb="1">Two</p>',
            }
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


def test_auth_api_key_valid_returns_200_stream(test_db, client, english_feed_article):
    api_key = "test_fever_api_key"
    asyncio.run(test_db.set_fever_auth("android_user", api_key))
    r = client.post(
        f"/api/reader/articles/{english_feed_article}/translate",
        json={"api_key": api_key},
    )
    assert r.status_code == 200
    events = _lines(r)
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done"


def test_auth_invalid_api_key_no_session_returns_401(test_db, client, english_feed_article):
    api_key = "test_fever_api_key"
    asyncio.run(test_db.set_fever_auth("android_user", api_key))
    r = client.post(
        f"/api/reader/articles/{english_feed_article}/translate",
        json={"api_key": "wrong_key"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_auth_no_body_valid_session_returns_200(test_db, client, english_feed_article):
    asyncio.run(test_db.set_web_auth("admin", _hash_password("adminpass")))
    token = "valid_web_session_token_123"
    asyncio.run(test_db.add_session_token(token))

    r = client.post(
        f"/api/reader/articles/{english_feed_article}/translate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    events = _lines(r)
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done"


def test_auth_no_body_no_session_web_auth_configured_returns_401(test_db, client, english_feed_article):
    asyncio.run(test_db.set_web_auth("admin", _hash_password("adminpass")))

    r = client.post(f"/api/reader/articles/{english_feed_article}/translate")
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_stream_endpoint_without_session_returns_401(test_db, client, english_feed_article):
    asyncio.run(test_db.set_web_auth("admin", _hash_password("adminpass")))

    r = client.get(f"/api/reader/articles/{english_feed_article}/translate/stream")
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_raw_true_skips_image_rewrite_for_block_and_source(client, image_article):
    r = client.post(
        f"/api/reader/articles/{image_article}/translate",
        json={"raw": True},
    )
    assert r.status_code == 200
    events = _lines(r)
    source_events = [e for e in events if e["type"] == "source"]
    assert source_events
    assert "https://cdn.example.com/photo.jpg" in source_events[0]["html"]
    assert "/api/reader/image-proxy" not in source_events[0]["html"]

    block_events = [e for e in events if e["type"] == "block"]
    assert block_events
    assert "https://cdn.example.com/photo.jpg" in block_events[0]["html"]
    assert "/api/reader/image-proxy" not in block_events[0]["html"]


def test_without_raw_rewrites_both_source_and_block(client, image_article):
    r = client.post(f"/api/reader/articles/{image_article}/translate")
    assert r.status_code == 200
    events = _lines(r)
    source_events = [e for e in events if e["type"] == "source"]
    assert source_events
    assert "https://cdn.example.com/photo.jpg" not in source_events[0]["html"]
    assert "/api/reader/image-proxy" in source_events[0]["html"]

    block_events = [e for e in events if e["type"] == "block"]
    assert block_events
    assert "https://cdn.example.com/photo.jpg" not in block_events[0]["html"]
    assert "/api/reader/image-proxy" in block_events[0]["html"]


def test_source_event_emitted_after_start_and_before_blocks(test_db, monkeypatch):
    """Pin the exact event ordering, data-tb coverage, and absence of data-tb in stored content."""
    article_id = _seed_article(test_db, "en", "<p>Alpha</p><p>Beta</p><p>Gamma</p>")

    def _bracket(text, target_language="zh-TW", *args, **kwargs):
        out = []
        for line in text.split("\n"):
            m = re.match(r"^\[(\d+)\]\s*(.*)$", line)
            out.append(f"[{m.group(1)}] T[{m.group(2)}]" if m else f"T[{line}]")
        return "\n".join(out), {}

    saved_calls = []
    async def _mock_save(aid, title, content, orig_title, orig_excerpt):
        saved_calls.append({"aid": aid, "content": content})

    monkeypatch.setattr(translation, "_qwen_settings", lambda: (True, "test-key"))
    monkeypatch.setattr(translation, "_qwen_call", _bracket)
    monkeypatch.setattr(translation, "translate_text", lambda text, *a, **k: ("標題", "Qwen-MT-flash"))
    monkeypatch.setattr(od_module.db, "save_ondemand_translation", _mock_save)

    monkeypatch.setattr(translation, "translate_html_iter", ORIGINAL_TRANSLATE_HTML_ITER)

    tc = TestClient(app)
    r = tc.post(f"/api/reader/articles/{article_id}/translate")
    assert r.status_code == 200
    events = _lines(r)

    # 1. source is emitted once, after start and before any block
    types = [e["type"] for e in events]
    assert types[0] == "start"
    source_idx = types.index("source")
    assert types.count("source") == 1

    first_block_idx = types.index("block")
    assert source_idx < first_block_idx

    # 2. its html carries data-tb values 0..n-1 each exactly once
    source_event = events[source_idx]
    soup = BeautifulSoup(source_event["html"], "html.parser")
    elements_with_tb = soup.find_all(attrs={"data-tb": True})
    tb_values = [el["data-tb"] for el in elements_with_tb]
    assert tb_values == ["0", "1", "2"]

    # 3. every block index appears among them
    blocks = [e for e in events if e["type"] == "block"]
    block_indices = [str(e["index"]) for e in blocks]
    assert block_indices == ["0", "1", "2"]

    # 4. Stored content after completed job contains no data-tb
    assert saved_calls
    stored_content = saved_calls[0]["content"]
    assert "data-tb" not in stored_content

