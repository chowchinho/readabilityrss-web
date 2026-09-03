#!/usr/bin/env python3
"""Dry run: tag a sample of real articles, score them, and print the ranked feed.

Touches nothing live - reads the local DB, writes only a CSV for review.
"""
import argparse
import collections
import csv
import os
import random
import sqlite3
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.topic_classifier import tag_articles_batch
from app.services.ranking import score_article
from app.services.rerank import calibrated_rerank

parser = argparse.ArgumentParser()
parser.add_argument("--n", type=int, default=200)
parser.add_argument("--seed", type=int, default=7)
args = parser.parse_args()

conn = sqlite3.connect(backend_dir / "data" / "feeds.db")
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute("""
    SELECT a.id, a.title, a.content AS body, a.created_at, a.source_id,
           f.name AS feed_name
    FROM feed_articles a JOIN feed_sources f ON f.id = a.source_id
    WHERE a.parse_status = 'success' AND a.title != ''
""")]

# Age percentile within each feed - the freshness term is relative to the feed's
# own cadence, not wall clock.
by_feed = collections.defaultdict(list)
for r in rows:
    by_feed[r["source_id"]].append(r)
for group in by_feed.values():
    group.sort(key=lambda r: r["created_at"] or "", reverse=True)
    for i, r in enumerate(group):
        r["age_pct"] = i / max(len(group) - 1, 1)

rng = random.Random(args.seed)
sample = rng.sample(rows, min(args.n, len(rows)))
print(f"tagging {len(sample)} articles from {len({r['source_id'] for r in sample})} feeds...")

tags = tag_articles_batch(sample)
print(f"returned {len(tags)} of {len(sample)} "
      f"({100 * len(tags) / len(sample):.0f}% - anything missing was dropped by reconciliation)")

scored = []
for r in sample:
    t = tags.get(r["id"])
    if not t:
        continue
    s, reason = score_article(
        {"primary": t.get("primary"), "region": t.get("region"),
         "type": t.get("type"), "secondary": t.get("secondary") or []},
        r["age_pct"])
    scored.append({**r, **t, "score": s, "reason": reason})

scored.sort(key=lambda x: -x["score"])
ranked = calibrated_rerank(scored)


def show(label, items):
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    for i, a in enumerate(items, 1):
        print(f"{i:3d}. [{a['score']:+5.2f}] {a['title'][:52]}")
        print(f"      {a['reason'][:48]:48s} | {a['feed_name'][:24]}")


show("TOP 25 AFTER RE-RANK  (what you would see first)", ranked[:25])
show("BOTTOM 15  (what sinks)", ranked[-15:])

print(f"\n{'=' * 78}\nDISTRIBUTION\n{'=' * 78}")
h = collections.Counter(round(a["score"]) for a in scored)
for k in sorted(h, reverse=True):
    print(f"  ~{k:+3d}  {h[k]:3d}  {'#' * h[k]}")
top20 = ranked[:20]
print(f"\n  distinct feeds in top 20 : {len({a['feed_name'] for a in top20})}")
print(f"  distinct topics in top 20: {len({a['primary'] for a in top20})}")
print("  top-20 topic mix:", dict(collections.Counter(a["primary"] for a in top20).most_common(6)))

out = backend_dir.parent / "dry_run_ranked_feed.csv"
with open(out, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["rank", "score", "title", "feed_name", "primary",
                                      "region", "type", "secondary", "reason", "ai_summary"])
    w.writeheader()
    for i, a in enumerate(ranked, 1):
        w.writerow({"rank": i, "score": round(a["score"], 2), "title": a["title"],
                    "feed_name": a["feed_name"], "primary": a.get("primary"),
                    "region": a.get("region"), "type": a.get("type"),
                    "secondary": "; ".join(a.get("secondary") or []),
                    "reason": a["reason"], "ai_summary": a.get("ai_summary", "")})
print(f"\nwrote full ranked list to {out}")
