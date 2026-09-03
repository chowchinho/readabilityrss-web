import pytest

from app.database import Database


async def _make_article(test_db, content):
    source_id = await test_db.create_feed_source({
        "name": "Test Feed",
        "url": "https://example.com/feed",
    })
    await test_db.insert_feed_articles(source_id, [{"url": "https://example.com/a"}])
    db = await test_db._get_db()
    cursor = await db.execute(
        "SELECT id FROM feed_articles WHERE source_id = ?", (source_id,)
    )
    article_id = (await cursor.fetchone())["id"]
    await test_db.update_article_parsed(
        article_id, "Title", content, "2026-08-14", "https://example.com/i.jpg"
    )
    return article_id


async def _row(test_db, article_id):
    db = await test_db._get_db()
    cursor = await db.execute(
        "SELECT snippet, featured_snippet FROM feed_articles WHERE id = ?", (article_id,)
    )
    return await cursor.fetchone()


@pytest.mark.asyncio
async def test_columns_exist_after_init(tmp_path):
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        db = await test_db._get_db()
        cursor = await db.execute("PRAGMA table_info(feed_articles)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "snippet" in columns
        assert "featured_snippet" in columns
    finally:
        test_db.close_sync()


@pytest.mark.asyncio
async def test_parse_populates_both_columns(tmp_path):
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        article_id = await _make_article(test_db, "<p>Parsed body text.</p>")
        row = await _row(test_db, article_id)
        assert row["snippet"] == "Parsed body text."
        assert row["featured_snippet"] == "Parsed body text."
    finally:
        test_db.close_sync()


@pytest.mark.asyncio
async def test_image_recache_refreshes_the_snippet(tmp_path):
    """The stale-preview case: re-caching rewrites content, so it must rewrite snippets."""
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        article_id = await _make_article(test_db, "<p>Before recache.</p>")
        await test_db.update_article_images(
            article_id, "<p>After recache.</p>", "https://example.com/new.jpg"
        )
        row = await _row(test_db, article_id)
        assert row["snippet"] == "After recache."
    finally:
        test_db.close_sync()


@pytest.mark.asyncio
async def test_backfill_fills_only_null_rows(tmp_path):
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        article_id = await _make_article(test_db, "<p>Backfilled text.</p>")
        db = await test_db._get_db()
        await db.execute(
            "UPDATE feed_articles SET snippet = NULL, featured_snippet = NULL WHERE id = ?",
            (article_id,),
        )
        await db.commit()

        assert await test_db.backfill_snippets(200) == 1
        row = await _row(test_db, article_id)
        assert row["snippet"] == "Backfilled text."
    finally:
        test_db.close_sync()


@pytest.mark.asyncio
async def test_backfill_survives_a_row_that_cannot_be_parsed(tmp_path, monkeypatch):
    """One unparseable article must not strand every row behind it.

    The stored value has to be non-NULL, or the row is re-selected on the next batch
    and the backfill loop never terminates.
    """
    import app.database as database_module

    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        source_id = await test_db.create_feed_source({
            "name": "Test Feed",
            "url": "https://example.com/feed",
        })
        await test_db.insert_feed_articles(source_id, [
            {"url": "https://example.com/poison"},
            {"url": "https://example.com/good"},
        ])
        db = await test_db._get_db()
        cursor = await db.execute(
            "SELECT id FROM feed_articles WHERE source_id = ? ORDER BY id", (source_id,)
        )
        poison_id, good_id = [r["id"] for r in await cursor.fetchall()]
        await test_db.update_article_parsed(poison_id, "P", "<p>Poison.</p>", "2026-08-14", "")
        await test_db.update_article_parsed(good_id, "G", "<p>Good body.</p>", "2026-08-14", "")
        await db.execute("UPDATE feed_articles SET snippet = NULL, featured_snippet = NULL")
        await db.commit()

        real_build = database_module.build_snippets

        def exploding_build(content):
            if content and "Poison" in content:
                raise RecursionError("maximum recursion depth exceeded")
            return real_build(content)

        monkeypatch.setattr(database_module, "build_snippets", exploding_build)

        assert await test_db.backfill_snippets(200) == 2
        assert await test_db.backfill_snippets(200) == 0

        assert (await _row(test_db, poison_id))["snippet"] == ""
        assert (await _row(test_db, good_id))["snippet"] == "Good body."
    finally:
        test_db.close_sync()


@pytest.mark.asyncio
async def test_backfill_is_resumable_and_idempotent(tmp_path):
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        await _make_article(test_db, "<p>Body.</p>")
        assert await test_db.backfill_snippets(200) == 0
    finally:
        test_db.close_sync()
