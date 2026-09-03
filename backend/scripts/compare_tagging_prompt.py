#!/usr/bin/env python3
"""Regression-test the tagging prompt against the existing labelled corpus in backend/labels.json."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.topic_classifier import tag_articles_batch

# Topics that were split after consolidation. An old parent label matching any of its
# children is agreement, not disagreement.
SPLIT_CHILDREN = {
    "Fashion & Beauty": {"Fashion & Accessories", "Skincare & Beauty", "Sneakers & Streetwear"},
    "Sports": {"Sports", "Football", "Other Sports"},
    "Local Life": {"Crime & Policing", "Culture", "Education", "Local Life"},
    "Gaming": {"Gaming", "Retro Gaming"},
}


def main():
    parser = argparse.ArgumentParser(description="Compare tagging prompt against labelled corpus.")
    parser.add_argument("--n", type=int, default=60, help="Number of articles to compare")
    args = parser.parse_args()

    labels_path = backend_dir / "labels.json"
    db_path = backend_dir / "data" / "feeds.db"

    if not labels_path.exists():
        print(f"Error: {labels_path} does not exist")
        sys.exit(1)
    if not db_path.exists():
        print(f"Error: {db_path} does not exist")
        sys.exit(1)

    with open(labels_path, "r", encoding="utf-8") as f:
        labels_data = json.load(f)

    raw_tags = labels_data.get("results_default_arm") or labels_data.get("raw_tags") or {}
    topic_map = labels_data.get("topic_consolidation_mapping") or labels_data.get("consolidated_topic_map") or {}

    if not raw_tags:
        print("No articles found in labels.json")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    all_raw_ids = [int(aid) for aid in raw_tags.keys()]
    print(f"Found {len(all_raw_ids)} tagged article IDs in labels.json")

    # Fetch matching articles from DB in chunks of 400
    db_articles = []
    for start in range(0, len(all_raw_ids), 400):
        chunk = all_raw_ids[start:start + 400]
        placeholders = ",".join("?" * len(chunk))
        query = f"""
            SELECT a.id, a.title, a.content AS body, a.source_id, f.name AS feed_name
            FROM feed_articles a
            JOIN feed_sources f ON f.id = a.source_id
            WHERE a.id IN ({placeholders})
        """
        rows = [dict(r) for r in conn.execute(query, chunk).fetchall()]
        db_articles.extend(rows)
        if len(db_articles) >= args.n:
            break
    conn.close()

    # Filter articles to those in raw_tags and take up to args.n
    articles_to_tag = [a for a in db_articles if str(a["id"]) in raw_tags][:args.n]

    if not articles_to_tag:
        print("Could not find matching articles in DB for comparison")
        sys.exit(1)

    print(f"Tagging {len(articles_to_tag)} articles with new prompt...")
    new_results = tag_articles_batch(articles_to_tag)

    exact_matches = 0
    consolidated_matches = 0
    region_matches = 0
    type_matches = 0
    total = len(articles_to_tag)
    disagreements = []
    not_returned = []

    for art in articles_to_tag:
        aid = art["id"]
        old_data = raw_tags[str(aid)]
        new_data = new_results.get(aid, {})

        old_primary = old_data.get("primary", "unknown")
        old_consolidated = topic_map.get(old_primary, old_primary)
        new_primary = new_data.get("primary", "unknown")

        # A consolidated parent legitimately maps to any of its post-split children.
        # Missing any of these understates agreement, because a correct re-label of a
        # split topic gets scored as a disagreement.
        returned = aid in new_results
        is_consolidated_match = returned and (
            old_consolidated == new_primary
            or old_primary == new_primary
            or new_primary in SPLIT_CHILDREN.get(old_consolidated, set())
        )

        if old_primary == new_primary:
            exact_matches += 1
        if is_consolidated_match:
            consolidated_matches += 1
        elif not returned:
            not_returned.append(art)
            print(f"NOT RETURNED [{aid}]: '{art.get('title','')[:50]}'")
        else:
            disagreements.append({
                "id": aid,
                "feed": art.get("feed_name", ""),
                "title": (art.get("title") or "")[:120],
                "old_primary": old_primary,
                "old_consolidated": old_consolidated,
                "new_primary": new_primary,
                "old_region": old_data.get("region"),
                "new_region": new_data.get("region"),
                "old_type": old_data.get("type"),
                "new_type": new_data.get("type"),
                "new_confidence": new_data.get("confidence"),
            })
            print(f"Mismatch [{aid}]: old='{old_primary}' (cons='{old_consolidated}') -> new='{new_primary}' | title='{art.get('title','')[:40]}'")

        if old_data.get("region") == new_data.get("region"):
            region_matches += 1

        if old_data.get("type") == new_data.get("type"):
            type_matches += 1

    consolidated_pct = (consolidated_matches / total) * 100 if total > 0 else 0
    print("\n--- Tagging Agreement Report ---")
    print(f"Total Compared: {total}")
    print(f"Exact Topic Match: {exact_matches}/{total} ({(exact_matches/total)*100:.1f}%)")
    print(f"Consolidated Topic Match: {consolidated_matches}/{total} ({consolidated_pct:.1f}%)")
    print(f"Region Match: {region_matches}/{total} ({(region_matches/total)*100:.1f}%)")
    print(f"Type Match: {type_matches}/{total} ({(type_matches/total)*100:.1f}%)")

    if not_returned:
        print(f"Not returned by the model: {len(not_returned)} "
              f"(counted as disagreement above; reconciliation gave up on these)")

    if consolidated_pct < 70.0:
        print(f"\nWARNING: Consolidated topic agreement ({consolidated_pct:.1f}%) is below 70%")

    out_path = backend_dir.parent / "tagging_disagreements.csv"
    import csv as _csv
    fields = ["id", "feed", "title", "old_primary", "old_consolidated", "new_primary",
              "old_region", "new_region", "old_type", "new_type", "new_confidence"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(disagreements)
    print(f"\nwrote {len(disagreements)} disagreements to {out_path}")

if __name__ == "__main__":
    main()
