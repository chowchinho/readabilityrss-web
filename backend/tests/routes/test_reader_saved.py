import asyncio

import app.routes.events as events_module
import app.routes.reader as reader_module
import app.routes.settings as settings_module
import pytest
from app.database import Database
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_reader_saved.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(reader_module, "db", database)
    monkeypatch.setattr(events_module, "db", database)
    monkeypatch.setattr(settings_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client():
    return TestClient(app)


def test_pruning_keeps_saved_articles_and_agrees_with_get_prunable(test_db):
    async def _run():
        sid = await test_db.create_feed_source({"name": "Prune Feed", "url": "http://example.com/prune/rss"})
        await test_db.insert_feed_articles(sid, [
            {"url": "http://example.com/prune/1"},
            {"url": "http://example.com/prune/2"},
            {"url": "http://example.com/prune/3"},
        ])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ? ORDER BY id ASC", (sid,))
        rows = await cursor.fetchall()
        aid1, aid2, aid3 = [r["id"] for r in rows]

        # Set distinct created_at timestamps: aid1 oldest, aid2 middle, aid3 newest
        await conn.execute("UPDATE feed_articles SET created_at = '2026-01-01 10:00:00' WHERE id = ?", (aid1,))
        await conn.execute("UPDATE feed_articles SET created_at = '2026-01-02 10:00:00' WHERE id = ?", (aid2,))
        await conn.execute("UPDATE feed_articles SET created_at = '2026-01-03 10:00:00' WHERE id = ?", (aid3,))
        await conn.commit()

        # Mark aid1 (the oldest) as saved
        await test_db.mark_item_saved(aid1)

        # With keep=1, aid3 is the newest unsaved article.
        # aid2 is the older unsaved article and should be pruned.
        # aid1 is saved and must NEVER be pruned.
        prunable = await test_db.get_prunable_articles(sid, keep=1)
        prunable_ids = [a["id"] for a in prunable]
        assert prunable_ids == [aid2], f"Expected only unsaved aid2 to be prunable, got {prunable_ids}"

        await test_db.delete_old_articles(sid, keep=1)

        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ? ORDER BY id ASC", (sid,))
        remaining_ids = [r["id"] for r in await cursor.fetchall()]
        assert remaining_ids == [aid1, aid3], f"Expected saved aid1 and newest aid3 to remain, got {remaining_ids}"

    asyncio.run(_run())


def test_save_and_unsave_round_trip_including_saved_at(client, test_db):
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Save Feed", "url": "http://example.com/save/rss"})
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/save/1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        row = await cursor.fetchone()
        aid = row["id"]
        await test_db.update_article_parsed(aid, "Title 1", "<p>Content 1</p>", "2026-05-01", "")
        return aid

    aid = asyncio.run(_seed())

    # Initially unsaved
    detail = client.get(f"/api/reader/articles/{aid}").json()
    assert detail["is_saved"] == 0
    assert detail["saved_at"] is None

    # POST save
    resp = client.post(f"/api/reader/articles/{aid}/save")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "is_saved": 1}

    # Verify article detail has saved_at
    detail = client.get(f"/api/reader/articles/{aid}").json()
    assert detail["is_saved"] == 1
    assert detail["saved_at"] is not None

    # Verify saved list includes article
    saved_list = client.get("/api/reader/articles?saved=true").json()["articles"]
    matched = [a for a in saved_list if a["id"] == aid]
    assert len(matched) == 1
    assert matched[0]["is_saved"] == 1
    assert matched[0]["saved_at"] is not None

    # POST unsave
    resp = client.post(f"/api/reader/articles/{aid}/unsave")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "is_saved": 0}

    # Verify article detail cleared saved_at
    detail = client.get(f"/api/reader/articles/{aid}").json()
    assert detail["is_saved"] == 0
    assert detail["saved_at"] is None

    # Verify saved list no longer includes article
    saved_list = client.get("/api/reader/articles?saved=true").json()["articles"]
    assert not any(a["id"] == aid for a in saved_list)


def test_save_and_unsave_404_on_missing_id(client, test_db):
    resp = client.post("/api/reader/articles/999999/save")
    assert resp.status_code == 404

    resp = client.post("/api/reader/articles/999999/unsave")
    assert resp.status_code == 404


def test_saved_list_ignores_since_and_3day_and_orders_by_saved_at(client, test_db):
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Order Feed", "url": "http://example.com/order/rss"})
        await test_db.insert_feed_articles(sid, [
            {"url": "http://example.com/order/1"},
            {"url": "http://example.com/order/2"},
            {"url": "http://example.com/order/3"},
            {"url": "http://example.com/order/4"},
        ])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ? ORDER BY id ASC", (sid,))
        rows = await cursor.fetchall()
        a1, a2, a3, a4 = [r["id"] for r in rows]

        # All articles are old (months ago) and read
        for aid in (a1, a2, a3, a4):
            await test_db.update_article_parsed(aid, f"Title {aid}", f"<p>Body {aid}</p>", "2025-01-01", "")
            await test_db.mark_item_read(aid)

        await conn.execute("UPDATE feed_articles SET created_at = '2025-01-01 10:00:00', updated_at = '2025-01-01 10:00:00' WHERE id = ?", (a1,))
        await conn.execute("UPDATE feed_articles SET created_at = '2025-01-02 10:00:00', updated_at = '2025-01-02 10:00:00' WHERE id = ?", (a2,))
        await conn.execute("UPDATE feed_articles SET created_at = '2025-01-03 10:00:00', updated_at = '2025-01-03 10:00:00' WHERE id = ?", (a3,))
        await conn.execute("UPDATE feed_articles SET created_at = '2025-01-04 10:00:00', updated_at = '2025-01-04 10:00:00' WHERE id = ?", (a4,))

        # a1: saved with saved_at = 2026-05-01 12:00:00
        # a2: saved with saved_at = 2026-05-02 12:00:00 (newer save -> should sort first)
        # a3: saved with saved_at = NULL (legacy save -> should sort last among saved)
        # a4: unsaved
        await conn.execute("UPDATE feed_articles SET is_saved = 1, saved_at = '2026-05-01 12:00:00' WHERE id = ?", (a1,))
        await conn.execute("UPDATE feed_articles SET is_saved = 1, saved_at = '2026-05-02 12:00:00' WHERE id = ?", (a2,))
        await conn.execute("UPDATE feed_articles SET is_saved = 1, saved_at = NULL WHERE id = ?", (a3,))
        await conn.execute("UPDATE feed_articles SET is_saved = 0, saved_at = NULL WHERE id = ?", (a4,))
        await conn.commit()
        return a1, a2, a3, a4

    a1, a2, a3, a4 = asyncio.run(_seed())

    # Default articles view (normal view) skips old read articles (past 3 days)
    normal = client.get("/api/reader/articles").json()["articles"]
    assert not any(a["id"] in (a1, a2, a3, a4) for a in normal)

    # Saved list with a 'since' in the future:
    # 'since' is ignored and the 3-day clause is skipped.
    # Orders by saved_at DESC, created_at DESC with NULL saved_at last.
    saved_resp = client.get("/api/reader/articles?saved=true&since=2026-12-31T00:00:00").json()
    saved_ids = [a["id"] for a in saved_resp["articles"]]
    assert saved_ids == [a2, a1, a3], f"Expected [a2, a1, a3], got {saved_ids}"
    assert a4 not in saved_ids


def test_total_saved_in_reader_feeds(client, test_db):
    feeds = client.get("/api/reader/feeds").json()
    assert feeds["total_saved"] == 0

    async def _seed():
        sid = await test_db.create_feed_source({"name": "Count Feed", "url": "http://example.com/count/rss"})
        await test_db.insert_feed_articles(sid, [
            {"url": "http://example.com/count/1"},
            {"url": "http://example.com/count/2"},
        ])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ? ORDER BY id ASC", (sid,))
        rows = await cursor.fetchall()
        aid1, aid2 = [r["id"] for r in rows]
        await test_db.update_article_parsed(aid1, "C1", "<p>C1</p>", "2026-05-01", "")
        await test_db.update_article_parsed(aid2, "C2", "<p>C2</p>", "2026-05-02", "")
        return aid1, aid2

    aid1, aid2 = asyncio.run(_seed())

    # Still 0 saved
    assert client.get("/api/reader/feeds").json()["total_saved"] == 0

    # Save aid1
    client.post(f"/api/reader/articles/{aid1}/save")
    assert client.get("/api/reader/feeds").json()["total_saved"] == 1

    # 'since' filter does not affect total_saved
    assert client.get("/api/reader/feeds?since=2099-01-01").json()["total_saved"] == 1

    # Save aid2
    client.post(f"/api/reader/articles/{aid2}/save")
    assert client.get("/api/reader/feeds").json()["total_saved"] == 2

    # Unsave aid1
    client.post(f"/api/reader/articles/{aid1}/unsave")
    assert client.get("/api/reader/feeds").json()["total_saved"] == 1
