import asyncio
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import app
import app.routes.reader as reader_module
import app.routes.events as events_module
import app.routes.settings as settings_module


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_inspection.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(reader_module, "db", database)
    monkeypatch.setattr(events_module, "db", database)
    monkeypatch.setattr(settings_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def article_id(test_db):
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Feed I", "url": "http://example.com/i/rss"})
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/i/1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        aid = (await cursor.fetchone())["id"]
        await test_db.update_article_parsed(aid, "I1", "<p>Body</p>", "2026-08-08", "")
        await test_db.save_article_tags(aid, {
            "primary": "Travel", "secondary": ["Japan"],
            "region": "Japan", "type": "Feature",
            "confidence": "high", "ai_summary": "Summary",
        })
        return aid
    return asyncio.run(_seed())


def test_label_weights_endpoint_returns_the_four_axes(client, test_db):
    """Rows per axis, as the dashboard consumes them.

    This previously asserted the nested dict get_effective_weights returns internally,
    which passed while the dashboard table rendered nothing at all - it reads
    axes[axis] as a list. Assert the contract with the consumer, not the internals.
    """
    r = client.get("/api/reader/label-weights")
    assert r.status_code == 200
    body = r.json()
    assert set(body["axes"].keys()) >= {"topic", "type", "region", "secondary"}

    topic_rows = {row["label"]: row for row in body["axes"]["topic"]}
    assert "Travel" in topic_rows
    assert "effective" in topic_rows["Travel"]


def test_score_breakdown_returns_subtotals_and_rows(client, test_db, article_id):
    r = client.get(f"/api/reader/articles/{article_id}/score-breakdown")
    assert r.status_code == 200
    body = r.json()
    assert body["article_id"] == article_id
    assert "score" in body
    assert "reason" in body
    assert isinstance(body["rows"], list)
    rows_by_term = {row["term"]: row for row in body["rows"]}
    assert "topic:Travel" in rows_by_term
    assert "type:Feature" in rows_by_term
    assert "freshness" in rows_by_term


def test_breakdown_404s_for_a_missing_article(client, test_db):
    assert client.get("/api/reader/articles/999999/score-breakdown").status_code == 404
