"""Centralized services for article voting and reading event recording/denormalization."""
from typing import Optional, Any
from ..database import db as default_db
from .vote_weights import invalidate_weights_cache

VALID_VOTES = ("show_more", "show_less")


async def record_article_vote(
    article_id: int,
    vote: Optional[str],
    mark_read: bool = True,
    conn=None,
    db_instance=None,
) -> tuple[bool, Optional[str], Optional[dict]]:
    """
    Record or remove a user vote on an article, update read state and insert an event.

    Args:
        article_id: The article's database ID.
        vote: 'show_more', 'show_less', or None (to remove vote).
        mark_read: Whether to mark the article read on up/down voting.
        conn: Optional active DB connection.
        db_instance: Optional Database instance (defaults to app.database.db).

    Returns:
        tuple of (success: bool, error_reason: Optional[str], metadata: Optional[dict])
    """
    if vote is not None and vote not in VALID_VOTES:
        return False, "Invalid vote value", None

    active_db = db_instance or default_db
    connection = conn or await active_db._get_db()
    cursor = await connection.execute(
        """
        SELECT primary_topic, secondary_topics, region, article_type
        FROM feed_articles WHERE id = ?
        """,
        (article_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False, "Article not found", None

    metadata = {
        "primary_topic": row["primary_topic"],
        "secondary_topics": row["secondary_topics"],
        "region": row["region"],
        "article_type": row["article_type"],
    }

    if vote is None:
        await active_db.delete_article_vote(article_id)
    else:
        await active_db.upsert_article_vote(article_id, vote, metadata)
        if mark_read:
            await active_db.mark_item_read(article_id)
        await active_db.insert_events([{
            "article_id": article_id,
            "event_type": vote,
            **metadata,
        }])

    invalidate_weights_cache()
    return True, None, metadata


async def denormalize_event(event_dict: dict[str, Any], conn=None, db_instance=None) -> dict[str, Any]:
    """
    Denormalize missing labels and feed source info from feed_articles/feed_sources.

    Preserves existing event fields (such as client-supplied dwell_seconds or source_name).
    """
    ev = dict(event_dict)
    aid = ev.get("article_id")
    if not aid:
        return ev

    active_db = db_instance or default_db
    connection = conn or await active_db._get_db()
    cursor = await connection.execute(
        """
        SELECT a.source_id, f.name AS source_name, a.primary_topic, a.secondary_topics,
               a.region, a.article_type
        FROM feed_articles a
        LEFT JOIN feed_sources f ON f.id = a.source_id
        WHERE a.id = ?
        """,
        (aid,),
    )
    row = await cursor.fetchone()
    if row:
        r_dict = dict(row)
        for key in ("source_id", "source_name", "primary_topic", "secondary_topics", "region", "article_type"):
            if not ev.get(key) and r_dict.get(key) is not None:
                ev[key] = r_dict.get(key)

    return ev


async def denormalize_events(events: list[dict[str, Any]], conn=None, db_instance=None) -> list[dict[str, Any]]:
    """Denormalize a batch of events with missing article/source metadata."""
    active_db = db_instance or default_db
    connection = conn or await active_db._get_db()
    result = []
    for ev in events:
        denorm = await denormalize_event(ev, conn=connection, db_instance=active_db)
        result.append(denorm)
    return result
