"""translated_from / translated_to on the reader article endpoints.

The reader shows a "JA → 中文" pill from these fields, so an article must only
carry translated_from when the pipeline actually translated it.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import app
import app.routes.reader as reader_module
from app.routes.reader import _translated_from


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_translation_fields.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(reader_module, "db", database)
    yield database
    asyncio.run(database.close())


def _seed(test_db, *, language, title, original_title):
    async def run():
        sid = await test_db.create_feed_source({"name": "Feed T", "url": "http://example.com/t/rss"})
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/t/1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        aid = (await cursor.fetchone())["id"]
        await test_db.update_article_parsed(aid, title, "<p>Body</p>", "2026-08-08", "")
        await conn.execute("UPDATE feed_articles SET original_title = ? WHERE id = ?", (original_title, aid))
        await conn.execute("UPDATE feed_sources SET detected_language = ? WHERE id = ?", (language, sid))
        await conn.commit()
        return sid, aid
    return asyncio.run(run())


@pytest.mark.parametrize("original, title, language, expected", [
    ("元のタイトル", "翻譯後的標題", "ja-jp", "ja"),
    ("Original headline", "翻譯後的標題", "en", "en"),
    (None, "Headline", "ja", None),
    ("Same", "Same", "ja", None),
    # Chinese sources only get a script conversion, which is not a translation.
    ("简体标题", "簡體標題", "zh-cn", None),
    ("Headline", "標題", "", None),
])
def test_translated_from(original, title, language, expected):
    assert _translated_from(original, title, language) == expected


def test_list_and_detail_report_translation(test_db):
    sid, aid = _seed(test_db, language="ja", title="翻譯後的標題", original_title="元のタイトル")
    client = TestClient(app)

    listed = client.get(f"/api/reader/articles?source_id={sid}&sort=latest").json()["articles"]
    assert listed[0]["translated_from"] == "ja"
    assert listed[0]["translated_to"] == "zh-TW"

    detail = client.get(f"/api/reader/articles/{aid}").json()
    assert detail["translated_from"] == "ja"
    assert detail["original_title"] == "元のタイトル"


def test_untranslated_article_has_no_original_title(test_db):
    sid, aid = _seed(test_db, language="en", title="Headline", original_title="Headline")
    client = TestClient(app)

    listed = client.get(f"/api/reader/articles?source_id={sid}&sort=latest").json()["articles"]
    assert listed[0]["translated_from"] is None

    detail = client.get(f"/api/reader/articles/{aid}").json()
    assert detail["translated_from"] is None
    assert detail["original_title"] is None
