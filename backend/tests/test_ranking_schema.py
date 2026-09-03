import pytest
from app.database import Database

@pytest.mark.asyncio
async def test_events_survive_article_deletion(tmp_path):
    db = Database(str(tmp_path / "t.db")); await db.init()
    sid = await db.create_feed_source({"name": "F", "url": "http://x"})
    await db.insert_feed_articles(sid, [{"url": "http://x/1"}])
    art = (await db.get_pending_articles(sid))[0]
    await db.insert_events([{
        "article_id": art["id"], "source_id": sid, "event_type": "open",
        "primary_topic": "Travel", "region": "Japan", "article_type": "Feature",
        "secondary_topics": '["Night Views"]', "dwell_seconds": 12,
    }])
    await db.delete_old_articles(sid, keep=0)
    rows = await db.get_events()
    assert len(rows) == 1
    assert rows[0]["primary_topic"] == "Travel"
    await db.close()


@pytest.mark.asyncio
async def test_event_labels_are_backfilled_once_the_article_is_tagged(tmp_path):
    """An impression on a not-yet-tagged article must not lose its labels forever."""
    db = Database(str(tmp_path / "b.db"))
    await db.init()
    sid = await db.create_feed_source({"name": "F", "url": "http://x"})
    await db.insert_feed_articles(sid, [{"url": "http://x/1"}])
    art = (await db.get_pending_articles(sid))[0]

    # Read happens first — the tagging worker has not reached this article yet.
    await db.insert_events([{"article_id": art["id"], "source_id": sid,
                             "event_type": "impression"}])
    assert (await db.get_events())[0]["primary_topic"] is None

    await db.save_article_tags(art["id"], {
        "primary": "Travel", "secondary": ["Night Views"],
        "region": "Japan", "type": "Feature", "confidence": "high",
        "ai_summary": "s",
    })

    filled = await db.backfill_event_labels()
    assert filled == 1
    ev = (await db.get_events())[0]
    assert ev["primary_topic"] == "Travel"
    assert ev["region"] == "Japan"
    assert ev["article_type"] == "Feature"

    # Idempotent — a second pass must not rewrite already-filled rows.
    assert await db.backfill_event_labels() == 0
    await db.close()


@pytest.mark.asyncio
async def test_article_impressions_upsert_and_non_impression_ignored(tmp_path):
    db = Database(str(tmp_path / "imp.db"))
    await db.init()
    sid = await db.create_feed_source({"name": "F", "url": "http://x"})
    await db.insert_feed_articles(sid, [{"url": "http://x/1"}, {"url": "http://x/2"}])
    arts = await db.get_pending_articles(sid)
    a1, a2 = arts[0]["id"], arts[1]["id"]

    # Non-impression event should NOT create a row in article_impressions
    await db.insert_events([{"article_id": a1, "source_id": sid, "event_type": "open"}])
    exp = await db.get_article_exposure([a1, a2])
    assert exp == {}

    # First impression for a1
    await db.insert_events([{"article_id": a1, "source_id": sid, "event_type": "impression"}])
    exp = await db.get_article_exposure([a1, a2])
    assert a1 in exp
    assert exp[a1][0] == 1
    assert exp[a1][1] is not None
    assert a2 not in exp

    # Second impression for a1
    await db.insert_events([{"article_id": a1, "source_id": sid, "event_type": "impression"}])
    exp = await db.get_article_exposure([a1])
    assert exp[a1][0] == 2

    # get_article_exposure edge cases
    assert await db.get_article_exposure([]) == {}
    assert await db.get_article_exposure(None) == {}
    assert await db.get_article_exposure([99999]) == {}

    await db.close()


@pytest.mark.asyncio
async def test_article_impressions_startup_backfill(tmp_path):
    db_file = str(tmp_path / "backfill.db")
    # Manually setup user_article_events table with historical events
    conn = await Database(db_file)._get_db()
    await conn.execute("""
        CREATE TABLE user_article_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER,
            source_id INTEGER,
            source_name TEXT,
            event_type TEXT NOT NULL,
            primary_topic TEXT,
            secondary_topics TEXT,
            region TEXT,
            article_type TEXT,
            dwell_seconds INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.execute("""
        INSERT INTO user_article_events (article_id, event_type, created_at)
        VALUES (101, 'impression', '2026-08-10 12:00:00'),
               (101, 'impression', '2026-08-11 12:00:00'),
               (102, 'impression', '2026-08-12 12:00:00'),
               (103, 'open', '2026-08-13 12:00:00')
    """)
    await conn.commit()
    await conn.close()

    # Now init db through normal Database.init() which should trigger backfill
    db = Database(db_file)
    await db.init()

    exp = await db.get_article_exposure([101, 102, 103])
    assert exp[101][0] == 2
    assert exp[101][1] == '2026-08-11 12:00:00'
    assert exp[102][0] == 1
    assert exp[102][1] == '2026-08-12 12:00:00'
    assert 103 not in exp

    # Re-running init should not duplicate counts
    await db.init()
    exp2 = await db.get_article_exposure([101, 102, 103])
    assert exp2[101][0] == 2

    await db.close()
