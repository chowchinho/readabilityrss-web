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
    database = Database(db_path=str(tmp_path / "test_ai_switch.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(reader_module, "db", database)
    monkeypatch.setattr(events_module, "db", database)
    monkeypatch.setattr(settings_module, "db", database)
    yield database
    asyncio.run(database.close())


BASE_SETTINGS = {
    "max_articles_per_feed": 50,
    "feed_refresh_interval_hours": 1.0,
    "target_language": "zh-TW",
    "image_max_dimension": 1200,
    "image_jpeg_quality": 70,
}


def test_ai_enabled_defaults_to_true_on_a_fresh_database(test_db):
    settings = asyncio.run(test_db.get_system_settings())
    assert settings["ai_enabled"] is True
    assert test_db.get_system_settings_sync()["ai_enabled"] is True


def test_ai_enabled_round_trips_through_the_settings_api(test_db):
    client = TestClient(app)

    resp = client.put("/api/settings", json={**BASE_SETTINGS, "ai_enabled": False})
    assert resp.status_code == 200
    assert resp.json()["settings"]["ai_enabled"] is False
    assert client.get("/api/settings").json()["ai_enabled"] is False

    resp = client.put("/api/settings", json={**BASE_SETTINGS, "ai_enabled": True})
    assert resp.status_code == 200
    assert client.get("/api/settings").json()["ai_enabled"] is True


def test_omitting_ai_enabled_preserves_the_stored_value(test_db):
    """A dashboard bundle cached from before this feature must not silently switch AI
    back on the next time someone saves an unrelated setting."""
    client = TestClient(app)
    client.put("/api/settings", json={**BASE_SETTINGS, "ai_enabled": False})

    resp = client.put("/api/settings", json=BASE_SETTINGS)
    assert resp.status_code == 200
    assert client.get("/api/settings").json()["ai_enabled"] is False


def _seed_tagged_feed(test_db, name, url_prefix, count=6):
    async def _seed():
        sid = await test_db.create_feed_source({"name": name, "url": f"{url_prefix}/rss"})
        await test_db.insert_feed_articles(
            sid, [{"url": f"{url_prefix}/{i}"} for i in range(count)])
        conn = await test_db._get_db()
        cursor = await conn.execute(
            "SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        for i, r in enumerate(await cursor.fetchall()):
            await test_db.update_article_parsed(r["id"], f"T{i}", "<p>Body</p>", "2026-08-08", "")
            await test_db.save_article_tags(r["id"], {
                "primary": "Travel" if i % 2 else "Politics",
                "secondary": ["Japan"],
                "region": "Japan" if i % 2 else "United States",
                "type": "Feature",
                "confidence": "high",
                "ai_summary": "A summary.",
            })
        return sid
    return asyncio.run(_seed())


def test_config_endpoint_reports_the_flag(test_db):
    client = TestClient(app)
    assert client.get("/api/reader/config").json() == {"ai_enabled": True}

    asyncio.run(test_db.update_system_settings({"ai_enabled": False}))
    assert client.get("/api/reader/config").json() == {"ai_enabled": False}


def test_smart_sort_is_ignored_while_ai_is_off(test_db):
    """A reader tab cached from before the switch was flipped must not keep getting
    ranked output just because it still sends sort=smart."""
    sid = _seed_tagged_feed(test_db, "Feed A", "http://example.com/a")
    client = TestClient(app)

    latest = [a["id"] for a in
              client.get(f"/api/reader/articles?source_id={sid}&sort=latest").json()["articles"]]

    asyncio.run(test_db.update_system_settings({"ai_enabled": False}))

    body = client.get(f"/api/reader/articles?source_id={sid}&sort=smart").json()
    assert [a["id"] for a in body["articles"]] == latest
    for art in body["articles"]:
        assert art["score"] is None
        assert art["recommendation_reason"] == ""
        assert art["topics"]["primary"] in ("Travel", "Politics")


def test_random_sort_still_shuffles_while_ai_is_off(test_db):
    sid = _seed_tagged_feed(test_db, "Feed B", "http://example.com/b", count=10)
    asyncio.run(test_db.update_system_settings({"ai_enabled": False}))
    client = TestClient(app)

    url = f"/api/reader/articles?source_id={sid}&sort=random"
    first = [a["id"] for a in client.get(url).json()["articles"]]
    second = [a["id"] for a in client.get(url).json()["articles"]]
    latest = [a["id"] for a in
              client.get(f"/api/reader/articles?source_id={sid}&sort=latest").json()["articles"]]

    assert first == second
    assert sorted(first) == sorted(latest)


def test_single_article_endpoint_drops_the_score_while_ai_is_off(test_db):
    sid = _seed_tagged_feed(test_db, "Feed C", "http://example.com/c", count=1)
    client = TestClient(app)
    art_id = client.get(f"/api/reader/articles?source_id={sid}").json()["articles"][0]["id"]

    asyncio.run(test_db.update_system_settings({"ai_enabled": False}))

    body = client.get(f"/api/reader/articles/{art_id}").json()
    assert body["score"] is None
    assert body["recommendation_reason"] == ""
    assert body["topics"]["primary"] in ("Travel", "Politics")
    assert body["ai_summary"] == "A summary."


def test_ranking_is_unchanged_while_ai_is_on(test_db):
    sid = _seed_tagged_feed(test_db, "Feed D", "http://example.com/d")
    client = TestClient(app)
    body = client.get(f"/api/reader/articles?source_id={sid}&sort=smart").json()
    assert len(body["articles"]) == 6
    for art in body["articles"]:
        assert isinstance(art["score"], (int, float))
        assert art["recommendation_reason"] != ""


def test_events_are_discarded_while_ai_is_off(test_db):
    sid = _seed_tagged_feed(test_db, "Feed E", "http://example.com/e", count=1)
    client = TestClient(app)
    art_id = client.get(f"/api/reader/articles?source_id={sid}").json()["articles"][0]["id"]

    asyncio.run(test_db.update_system_settings({"ai_enabled": False}))

    resp = client.post("/api/reader/events", json={
        "events": [{"article_id": art_id, "event_type": "show_more"}]
    })

    assert resp.status_code == 200
    assert resp.json() == {"success": True, "count": 0}
    assert asyncio.run(test_db.get_events()) == []


def test_events_are_still_recorded_while_ai_is_on(test_db):
    sid = _seed_tagged_feed(test_db, "Feed F", "http://example.com/f", count=1)
    client = TestClient(app)
    art_id = client.get(f"/api/reader/articles?source_id={sid}").json()["articles"][0]["id"]

    resp = client.post("/api/reader/events", json={
        "events": [{"article_id": art_id, "event_type": "show_more"}]
    })

    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert len(asyncio.run(test_db.get_events())) == 1
