"""An on-demand translation must refresh the card snippets, not just the body."""
import pytest

from app.database import Database


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    yield


@pytest.mark.asyncio
async def test_save_ondemand_translation_recomputes_snippets(tmp_path):
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
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

        await test_db.save_ondemand_translation(
            article_id,
            title="中文標題",
            content="<p>中文內文段落。</p>",
            original_title="English title",
            original_excerpt="English body paragraph.",
        )
        cursor = await db.execute("SELECT * FROM feed_articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        assert row["title"] == "中文標題"
        assert row["original_title"] == "English title"
        assert "English" not in (row["snippet"] or "")
        assert (row["snippet"] or "").strip() != ""
    finally:
        test_db.close_sync()
