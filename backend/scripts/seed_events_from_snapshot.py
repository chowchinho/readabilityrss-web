#!/usr/bin/env python3
"""Seed user_article_events from read_history_snapshot.json.

Idempotent: an article that already has a seeded event is skipped, so this can be
re-run safely. Reuses labels already on feed_articles and only calls DeepSeek for
the remainder - by the time this runs, the backfill worker has usually tagged most
of the snapshot already.
"""
import json
import sqlite3
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.topic_classifier import tag_articles_batch


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else backend_dir / "data" / "feeds.db"
    snapshot_path = backend_dir / "data" / "read_history_snapshot.json"

    if not snapshot_path.exists():
        print(f"Error: {snapshot_path} does not exist")
        sys.exit(1)

    with open(snapshot_path, "r", encoding="utf-8") as f:
        articles = json.load(f).get("articles", [])
    print(f"snapshot holds {len(articles)} read/saved articles")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    already = {r[0] for r in conn.execute(
        "SELECT DISTINCT article_id FROM user_article_events WHERE event_type IN ('open','save')"
    )}
    pending = [a for a in articles if a["id"] not in already]
    print(f"  {len(already)} already seeded, {len(pending)} to insert")
    if not pending:
        conn.close()
        print("nothing to do")
        return

    # Labels the tagging worker has already produced - no reason to pay for them twice.
    ids = [a["id"] for a in pending]
    existing = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        q = f"""SELECT id, primary_topic, secondary_topics, region, article_type
                FROM feed_articles
                WHERE id IN ({','.join('?' * len(chunk))}) AND primary_topic IS NOT NULL"""
        for r in conn.execute(q, chunk):
            existing[r["id"]] = {
                "primary": r["primary_topic"],
                "secondary": r["secondary_topics"],
                "region": r["region"],
                "type": r["article_type"],
            }
    print(f"  {len(existing)} already tagged in feed_articles, "
          f"{len(pending) - len(existing)} need DeepSeek")

    need_tagging = [a for a in pending if a["id"] not in existing]
    if need_tagging:
        for aid, tags in tag_articles_batch(need_tagging).items():
            existing[aid] = tags

    rows = []
    for art in pending:
        tags = existing.get(art["id"], {})
        sec = tags.get("secondary")
        if isinstance(sec, (list, tuple)):
            sec = json.dumps(sec)
        # updated_at is when it was marked read; created_at is when it arrived.
        when = art.get("updated_at") or art.get("created_at")
        event = "save" if art.get("is_saved") else "open"
        rows.append((art["id"], art.get("source_id"), art.get("feed_name"), event,
                     tags.get("primary"), sec, tags.get("region"), tags.get("type"),
                     None, when))

    conn.executemany("""
        INSERT INTO user_article_events (
            article_id, source_id, source_name, event_type,
            primary_topic, secondary_topics, region, article_type,
            dwell_seconds, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
    """, rows)
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM user_article_events").fetchone()[0]
    labelled = conn.execute(
        "SELECT COUNT(*) FROM user_article_events WHERE primary_topic IS NOT NULL"
    ).fetchone()[0]
    print(f"inserted {len(rows)} events; table now holds {total}, {labelled} with labels")
    conn.close()


if __name__ == "__main__":
    main()
