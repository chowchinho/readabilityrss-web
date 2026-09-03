#!/usr/bin/env python3
"""Dump every currently-read/saved article with its feed, before retention purges it."""
import json, sqlite3, sys, datetime
sys.stdout.reconfigure(encoding="utf-8")

conn = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "backend/data/feeds.db")
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute("""
    SELECT a.id, a.url, a.title, a.pub_date, a.created_at, a.updated_at,
           a.is_read, a.is_saved, f.name AS feed_name, f.id AS source_id
    FROM feed_articles a JOIN feed_sources f ON f.id = a.source_id
    WHERE a.is_read = 1 OR a.is_saved = 1
""")]
out = {"captured_at": datetime.datetime.utcnow().isoformat() + "Z", "count": len(rows),
       "articles": rows}
with open("backend/data/read_history_snapshot.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"captured {len(rows)} read/saved articles")
