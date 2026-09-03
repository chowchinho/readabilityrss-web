"""Articles must never outlive the feed source they belong to.

A deleted source with surviving articles is invisible everywhere — get_untagged_articles
and the reader query both INNER JOIN feed_sources — so the rows can never be tagged, read
or cleaned up. Ten such rows were found live on 2026-08-17, left by a duplicate feed
removed on 2026-05-28 while a scheduler fetch was still in flight.
"""
import asyncio
import pytest
from app.database import Database


@pytest.fixture
def orphan_db(tmp_path):
    db = Database(str(tmp_path / "orphans.db"))
    asyncio.run(db.init())
    yield db
    asyncio.run(db.close())


def test_batch_insert_rejects_unknown_source(orphan_db):
    inserted = asyncio.run(orphan_db.insert_feed_articles(999, [
        {"url": "http://example.com/1"},
        {"url": "http://example.com/2"},
    ]))
    assert inserted == 0


def test_single_insert_rejects_unknown_source(orphan_db):
    assert asyncio.run(orphan_db.insert_feed_article(999, "http://example.com/1")) is False


def test_fetch_landing_after_delete_leaves_no_orphans(orphan_db):
    """The live failure: the source is deleted while a fetch is still in flight."""
    sid = asyncio.run(orphan_db.create_feed_source(
        {"name": "Duplicate", "url": "http://example.com/rss"}))
    asyncio.run(orphan_db.delete_feed_source(sid))

    # The in-flight fetch lands after the delete has already committed.
    inserted = asyncio.run(orphan_db.insert_feed_articles(sid, [
        {"url": "http://example.com/late-1"},
        {"url": "http://example.com/late-2"},
    ]))
    assert inserted == 0

    orphans = asyncio.run(orphan_db.count_orphaned_articles())
    assert orphans == 0


def test_insert_still_works_for_a_live_source(orphan_db):
    sid = asyncio.run(orphan_db.create_feed_source(
        {"name": "Live", "url": "http://example.com/rss"}))
    assert asyncio.run(orphan_db.insert_feed_articles(sid, [
        {"url": "http://example.com/1"},
        {"url": "http://example.com/2"},
    ])) == 2
    assert asyncio.run(orphan_db.insert_feed_article(sid, "http://example.com/3")) is True


def test_purge_orphaned_articles_removes_pre_existing_rows(orphan_db):
    """Rows already orphaned before the guard existed still need clearing."""
    sid = asyncio.run(orphan_db.create_feed_source(
        {"name": "Gone", "url": "http://example.com/rss"}))
    asyncio.run(orphan_db.insert_feed_articles(sid, [{"url": "http://example.com/1"}]))

    async def orphan_it():
        conn = await orphan_db._get_db()
        await conn.execute("DELETE FROM feed_sources WHERE id = ?", (sid,))
        await conn.commit()

    asyncio.run(orphan_it())
    assert asyncio.run(orphan_db.count_orphaned_articles()) == 1

    assert asyncio.run(orphan_db.purge_orphaned_articles()) == 1
    assert asyncio.run(orphan_db.count_orphaned_articles()) == 0
