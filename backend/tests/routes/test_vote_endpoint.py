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
    database = Database(db_path=str(tmp_path / "test_vote_endpoint.db"))
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
        sid = await test_db.create_feed_source({"name": "Feed V", "url": "http://example.com/v/rss"})
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/v/1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        row = await cursor.fetchone()
        aid = row["id"]
        await test_db.update_article_parsed(aid, "V1", "<p>Body</p>", "2026-08-08", "")
        await test_db.save_article_tags(aid, {
            "primary": "Consumer Tech",
            "secondary": ["Samsung"],
            "region": "Global",
            "type": "News",
            "confidence": "high",
            "ai_summary": "Summary",
        })
        return aid
    return asyncio.run(_seed())


def test_vote_persists_and_is_returned_by_the_list(client, test_db, article_id):
    r = client.post(f"/api/reader/articles/{article_id}/vote",
                    json={"vote": "show_more"})
    assert r.status_code == 200
    assert r.json()["vote"] == "show_more"

    listed = client.get("/api/reader/articles").json()["articles"]
    row = next(a for a in listed if a["id"] == article_id)
    assert row["vote"] == "show_more"


def test_voting_twice_leaves_one_row(client, test_db, article_id):
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_more"})
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_more"})
    assert len(asyncio.run(test_db.get_article_votes())) == 1


def test_changing_the_vote_replaces_it(client, test_db, article_id):
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_more"})
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_less"})
    rows = asyncio.run(test_db.get_article_votes())
    assert len(rows) == 1 and rows[0]["vote"] == "show_less"


def test_null_clears_the_vote(client, test_db, article_id):
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_more"})
    r = client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": None})
    assert r.json()["vote"] is None
    assert asyncio.run(test_db.get_article_votes()) == []


def test_voting_marks_the_article_read(client, test_db, article_id):
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_less"})
    listed = client.get("/api/reader/articles").json()["articles"]
    row = next(a for a in listed if a["id"] == article_id)
    assert row["is_read"] == 1


def test_voting_emits_no_open_event(client, test_db, article_id):
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_more"})
    events = asyncio.run(test_db.get_events())
    assert [e for e in events if e["event_type"] == "open"] == []
    assert [e["event_type"] for e in events] == ["show_more"]


def test_an_invalid_vote_is_rejected(client, test_db, article_id):
    r = client.post(f"/api/reader/articles/{article_id}/vote",
                    json={"vote": "show_sideways"})
    assert r.status_code == 400
    assert asyncio.run(test_db.get_article_votes()) == []


def test_voting_on_a_missing_article_404s(client, test_db):
    assert client.post("/api/reader/articles/999999/vote",
                       json={"vote": "show_more"}).status_code == 404


def test_bulk_mark_read_emits_no_events(client, test_db, article_id):
    resp = client.post("/api/reader/mark-read", json={"article_ids": [article_id]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1

    events = asyncio.run(test_db.get_events())
    assert events == [], (
        "bulk mark-read must not emit a read_no_vote or open event; clearing "
        "the inbox is an administrative action, not a signal of distaste"
    )


def test_label_weights_returns_the_shape_the_dashboard_reads(client, test_db, article_id):
    """The dashboard iterates axes[axis] as a list of rows carrying `label`.

    Returning get_effective_weights' nested dict straight out satisfies every backend
    test and still renders a completely empty table, because `data.axes` is undefined
    and every axis falls through to []. Shape is the contract here.
    """
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_more"})

    body = client.get("/api/reader/label-weights").json()

    assert "axes" in body, "dashboard reads body.axes"
    for axis in ("topic", "type", "region", "secondary"):
        assert isinstance(body["axes"].get(axis), list), f"{axis} must be a list of rows"

    topic_rows = {r["label"]: r for r in body["axes"]["topic"]}
    voted = topic_rows["Consumer Tech"]
    for field in ("label", "declared", "votes", "explicit", "behavioural", "effective"):
        assert field in voted, f"dashboard column {field} missing"
    assert voted["votes"] == 1
    assert voted["explicit"] > 0


def test_label_weights_reports_the_impression_coverage_check(client, test_db, article_id):
    """Spec 3.7: a label with a target floor and no impressions means the exploration
    floor is not working, and it can never be voted back up. Only the topic axis has
    impression data."""
    asyncio.run(test_db.insert_events([
        {"article_id": article_id, "event_type": "impression",
         "primary_topic": "Consumer Tech"},
    ]))

    body = client.get("/api/reader/label-weights?days=30").json()

    assert body["impression_window_days"] == 30
    assert body["impressions"]["Consumer Tech"] == 1

    rows = {r["label"]: r for r in body["axes"]["topic"]}
    assert rows["Consumer Tech"]["impressions"] == 1
    assert rows["Consumer Tech"]["unseen"] is False
    # Every other declared topic was never shown in the window.
    starved = [label for label, r in rows.items() if r.get("unseen")]
    assert "Politics" in starved


def test_impression_window_is_clamped(client, test_db, article_id):
    assert client.get("/api/reader/label-weights?days=0").json()["impression_window_days"] == 1
    assert client.get("/api/reader/label-weights?days=9999").json()["impression_window_days"] == 365
