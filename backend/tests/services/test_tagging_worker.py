import pytest
from app.database import Database
from app.services import tagging_worker, topic_classifier

@pytest.mark.asyncio
async def test_worker_leaves_labels_null_when_api_fails(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "t.db")); await db.init()
    sid = await db.create_feed_source({"name": "F", "url": "http://x"})
    await db.insert_feed_articles(sid, [{"url": "http://x/1"}])
    art = (await db.get_pending_articles(sid))[0]
    await db.update_article_parsed(art["id"], "Title", "<p>Body</p>", "2026-08-08", "")

    def failing_call(payload):
        raise Exception("API error")

    monkeypatch.setattr(topic_classifier, "_call_deepseek", failing_call)

    processed = await tagging_worker.run_tagging_backfill(db=db, limit=10)
    assert processed == 0

    # Assert article row still exists and labels remain NULL
    cursor = await (await db._get_db()).execute("SELECT * FROM feed_articles WHERE id = ?", (art["id"],))
    row = dict(await cursor.fetchone())
    assert row["primary_topic"] is None
    assert row["topic_extracted_at"] is None

    await db.close()


@pytest.mark.asyncio
async def test_backfill_makes_no_api_call_when_ai_is_disabled(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "off.db")); await db.init()
    # Closed in a finally: a failing assertion would otherwise leave aiosqlite's
    # non-daemon connection thread running and hang the whole pytest session.
    try:
        sid = await db.create_feed_source({"name": "F", "url": "http://x"})
        await db.insert_feed_articles(sid, [{"url": "http://x/1"}])
        art = (await db.get_pending_articles(sid))[0]
        await db.update_article_parsed(art["id"], "Title", "<p>Body</p>", "2026-08-08", "")

        await db.update_system_settings({"ai_enabled": False})

        calls = []
        monkeypatch.setattr(
            tagging_worker, "tag_articles_batch", lambda arts: calls.append(arts) or {})

        processed = await tagging_worker.run_tagging_backfill(db=db, limit=10)

        assert processed == 0
        assert calls == []

        cursor = await (await db._get_db()).execute(
            "SELECT topic_extracted_at FROM feed_articles WHERE id = ?", (art["id"],))
        assert dict(await cursor.fetchone())["topic_extracted_at"] is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_unparsed_articles_are_not_offered_for_tagging(tmp_path):
    """Tagging an article before the parser fills it in burns it permanently: the
    model gets an empty body, returns unknown, and topic_extracted_at is stamped."""
    db = Database(str(tmp_path / "race.db")); await db.init()
    try:
        sid = await db.create_feed_source({"name": "F", "url": "http://x"})
        await db.insert_feed_articles(sid, [{"url": "http://x/1"}])
        art = (await db.get_pending_articles(sid))[0]

        assert await db.get_untagged_articles(limit=10) == []

        await db.update_article_parsed(art["id"], "Title", "<p>Body</p>", "2026-08-08", "")
        assert len(await db.get_untagged_articles(limit=10)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_tagging_prefers_original_language_over_translation(tmp_path):
    db = Database(str(tmp_path / "orig.db")); await db.init()
    try:
        sid = await db.create_feed_source({"name": "F", "url": "http://x"})
        await db.insert_feed_articles(sid, [{"url": "http://x/1"}, {"url": "http://x/2"}])
        translated, untranslated = await db.get_pending_articles(sid)

        await db.update_article_parsed(
            translated["id"], "東京車站開了咖啡館", "<p>翻譯後的內容</p>", "2026-08-08", "",
            "東京駅にカフェがオープン", "オリジナルの本文")
        await db.update_article_parsed(
            untranslated["id"], "English Title", "<p>English body</p>", "2026-08-08", "")

        rows = {r["id"]: r for r in await db.get_untagged_articles(limit=10)}

        assert rows[translated["id"]]["title"] == "東京駅にカフェがオープン"
        assert rows[translated["id"]]["body"] == "オリジナルの本文"
        # No original stored (pre-migration rows, or an untranslated feed): fall back.
        assert rows[untranslated["id"]]["title"] == "English Title"
        assert rows[untranslated["id"]]["body"] == "<p>English body</p>"
    finally:
        await db.close()
