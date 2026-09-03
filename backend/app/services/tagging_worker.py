"""Background worker job to tag untagged articles via DeepSeek."""
import asyncio
import logging
from ..database import db as global_db
from .topic_classifier import tag_articles_batch

logger = logging.getLogger(__name__)

async def run_tagging_backfill(db=None, limit: int = 200) -> int:
    database = db or global_db
    try:
        if not database.get_system_settings_sync().get("ai_enabled", True):
            return 0

        untagged = await database.get_untagged_articles(limit=limit)
        if not untagged:
            return 0

        logger.info(f"Tagging backfill starting for {len(untagged)} articles")
        # tag_articles_batch is blocking requests.post, one call per 8 articles with a
        # 240s timeout each. On the event loop a full 200-article backfill freezes every
        # HTTP response - reader, API and Fever alike - for minutes at a time.
        tagged_map = await asyncio.to_thread(tag_articles_batch, untagged)

        saved_count = 0
        for art in untagged:
            aid = art["id"]
            if aid in tagged_map:
                tags = tagged_map[aid]
                await database.save_article_tags(aid, tags)
                saved_count += 1

        logger.info(f"Tagging backfill saved tags for {saved_count}/{len(untagged)} articles")
        return saved_count
    except Exception as e:
        logger.error(f"Error running tagging backfill: {e}")
        return 0


async def backfill_event_labels(db=None) -> int:
    """Fill in labels on events that were recorded before their article was tagged.

    An article is untagged for up to a full worker cycle after ingest, which is exactly
    when it sits at the top of the feed and is most likely to be read. Events recorded
    in that window arrive with no labels, and the article they point at is eventually
    purged at the per-feed cap - so without this pass the most interesting events lose
    their labels permanently. Runs every cycle, far inside the shortest purge window.
    """
    database = db or global_db
    try:
        filled = await database.backfill_event_labels()
        if filled:
            logger.info(f"Backfilled labels onto {filled} events")
        return filled
    except Exception as e:
        logger.error(f"Error backfilling event labels: {e}")
        return 0
