#!/usr/bin/env python3
"""
Consolidates free-form primary labels into ~25 standard categories via DeepSeek,
updates backend/labels.json, and generates CSV review files with User_Comments column.
"""
import os
import sys
import json
import csv
import time
import requests

sys.stdout.reconfigure(encoding='utf-8')

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
labels_path = os.path.join(backend_dir, "labels.json")

with open(labels_path, "r", encoding="utf-8") as f:
    data = json.load(f)

results_default = data.get("results_default_arm", {})
results_ab = data.get("results_ab_feed_name_arm", {})
sampled_ids = data.get("sampled_article_ids", [])

raw_primaries = [v.get("primary", "unknown") for v in results_default.values() if v.get("primary")]
distinct_labels = sorted(list(set(raw_primaries)))
print(f"[CONSOLIDATION] Found {len(distinct_labels)} distinct primary labels. Calling DeepSeek for consolidation...", flush=True)

system_prompt = """You are an expert taxonomy manager. Group free-form article topic labels into approximately 20-25 clean, canonical, high-level English interest categories (e.g., 'Politics', 'Gaming', 'Travel', 'Food & Dining', 'Consumer Tech', 'Entertainment', 'Automotive', 'Anime & Manga', 'Personal Finance', 'Sports', 'Photography', 'DIY & Hardware', 'Real Estate', 'Design & Art', 'Supernatural', 'Transport', 'Software & AI', 'Business & Economy', 'Fashion & Beauty', 'Science', 'Home & Garden', 'Books & Literature', 'Local Life', 'Health & Fitness').

Return a JSON object with key "mapping", where each key is an input label and each value is its single consolidated canonical topic name.
"""

user_prompt = f"Labels to consolidate:\n{json.dumps(distinct_labels, ensure_ascii=False)}"

headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
req_data = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    "response_format": {"type": "json_object"},
    "temperature": 0.1,
    "max_tokens": 4096
}

consolidation_map = {}
try:
    resp = requests.post(DEEPSEEK_URL, headers=headers, json=req_data, timeout=60)
    res_j = resp.json()
    content = res_j["choices"][0]["message"]["content"]
    consolidation_map = json.loads(content).get("mapping", {})
    print(f"[CONSOLIDATION] Successfully mapped {len(consolidation_map)} labels into {len(set(consolidation_map.values()))} consolidated categories!", flush=True)
except Exception as e:
    print(f"[ERROR] Consolidation request failed: {e}", flush=True)
    consolidation_map = {lbl: lbl for lbl in distinct_labels}

# Re-query feed rates & sampled article info for volume shares
import sqlite3
db_path = os.path.join(backend_dir, "data", "feeds.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT id, name FROM feed_sources WHERE enabled = 1 OR enabled IS NULL")
feeds = cursor.fetchall()
feed_rates = {}

for feed in feeds:
    fid = feed["id"]
    cursor.execute("SELECT created_at FROM feed_articles WHERE source_id = ? AND parse_status = 'success'", (fid,))
    rows = cursor.fetchall()
    if not rows or len(rows) < 2:
        feed_rates[fid] = 1.0
    else:
        from datetime import datetime
        dts = []
        for r in rows:
            if r["created_at"]:
                try: dts.append(datetime.fromisoformat(r["created_at"].replace(" ", "T")))
                except: pass
        if len(dts) >= 2:
            span_days = max(0.05, (max(dts) - min(dts)).total_seconds() / 86400.0)
            feed_rates[fid] = len(rows) / span_days
        else:
            feed_rates[fid] = 1.0

# Fetch titles and feed ids for sampled articles
article_info = {}
placeholders = ",".join(["?"] * len(sampled_ids))
cursor.execute(f"SELECT id, source_id, title FROM feed_articles WHERE id IN ({placeholders})", sampled_ids)
for r in cursor.fetchall():
    article_info[r["id"]] = {"source_id": r["source_id"], "title": r["title"] or ""}
conn.close()

# Recompute statistics
total_sample_volume = 0.0
article_volumes = {}
for aid in sampled_ids:
    if str(aid) in results_default:
        sid = article_info.get(aid, {}).get("source_id")
        vol = feed_rates.get(sid, 1.0) / 5.0
        article_volumes[aid] = vol
        total_sample_volume += vol

topic_stats = {}
region_stats = {}
type_stats = {}

for aid, res in results_default.items():
    vol = article_volumes.get(int(aid), 1.0)
    title = article_info.get(int(aid), {}).get("title", "")
    
    raw_p = res.get("primary", "unknown")
    cons_topic = consolidation_map.get(raw_p, raw_p)
    region = res.get("region", "unknown")
    art_type = res.get("type", "News")
    
    # Topic
    if cons_topic not in topic_stats:
        topic_stats[cons_topic] = {"count": 0, "volume": 0.0, "examples": []}
    topic_stats[cons_topic]["count"] += 1
    topic_stats[cons_topic]["volume"] += vol
    if title and len(topic_stats[cons_topic]["examples"]) < 5 and title not in topic_stats[cons_topic]["examples"]:
        topic_stats[cons_topic]["examples"].append(title)
        
    # Region
    if region not in region_stats:
        region_stats[region] = {"count": 0, "volume": 0.0, "examples": []}
    region_stats[region]["count"] += 1
    region_stats[region]["volume"] += vol
    if title and len(region_stats[region]["examples"]) < 5 and title not in region_stats[region]["examples"]:
        region_stats[region]["examples"].append(title)

    # Type
    if art_type not in type_stats:
        type_stats[art_type] = {"count": 0, "volume": 0.0, "examples": []}
    type_stats[art_type]["count"] += 1
    type_stats[art_type]["volume"] += vol
    if title and len(type_stats[art_type]["examples"]) < 5 and title not in type_stats[art_type]["examples"]:
        type_stats[art_type]["examples"].append(title)

for t_dict in (topic_stats, region_stats, type_stats):
    for k, v in t_dict.items():
        v["volume_share_pct"] = (v["volume"] / total_sample_volume * 100.0) if total_sample_volume > 0 else 0.0

# Re-calculate A/B agreement on consolidated topics
ab_subset_ids = data["sampled_article_ids"][:100]
ab_matches = {
    "primary_exact": 0,
    "consolidated_topic_match": 0,
    "region_match": 0,
    "type_match": 0,
    "total_compared": 0
}
for aid in ab_subset_ids:
    sa = str(aid)
    r_a = results_default.get(sa)
    r_b = results_ab.get(sa)
    if r_a and r_b:
        ab_matches["total_compared"] += 1
        pa = r_a.get("primary", "")
        pb = r_b.get("primary", "")
        if pa == pb:
            ab_matches["primary_exact"] += 1
        if consolidation_map.get(pa, pa) == consolidation_map.get(pb, pb):
            ab_matches["consolidated_topic_match"] += 1
        if r_a.get("region") == r_b.get("region"):
            ab_matches["region_match"] += 1
        if r_a.get("type") == r_b.get("type"):
            ab_matches["type_match"] += 1

# Update data JSON
data["metadata"]["ab_comparison_100_articles"] = ab_matches
data["topic_consolidation_mapping"] = consolidation_map
data["topic_summary"] = topic_stats
data["region_summary"] = region_stats
data["type_summary"] = type_stats

with open(labels_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"[DONE] Updated {labels_path}", flush=True)

# Export CSVs to root and backend
for dest_dir in [project_root, backend_dir]:
    def export_csv(filename, title, stats_dict):
        filepath = os.path.join(dest_dir, filename)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Axis", "Name", "Sample_Count", "Est_Daily_Flow_Articles", "Est_Share_Pct", "Example_Headlines", "Evaluation (more/less/depends)", "User_Comments"])
            sorted_items = sorted(stats_dict.items(), key=lambda x: x[1]["volume_share_pct"], reverse=True)
            for name, d in sorted_items:
                ex_str = " | ".join(d["examples"][:5])
                writer.writerow([title, name, d["count"], round(d["volume"], 1), f"{d['volume_share_pct']:.1f}%", ex_str, "", ""])
        print(f"[CSV] Created: {filepath}", flush=True)

    export_csv("topic_review.csv", "Topic", topic_stats)
    export_csv("region_review.csv", "Region", region_stats)
    export_csv("type_review.csv", "Type", type_stats)

    all_csv_path = os.path.join(dest_dir, "topic_tagging_review_all.csv")
    with open(all_csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Axis", "Name", "Sample_Count", "Est_Daily_Flow_Articles", "Est_Share_Pct", "Example_Headlines", "Evaluation (more/less/depends)", "User_Comments"])
        for title, stats_dict in [("Topic", topic_stats), ("Region", region_stats), ("Type", type_stats)]:
            sorted_items = sorted(stats_dict.items(), key=lambda x: x[1]["volume_share_pct"], reverse=True)
            for name, d in sorted_items:
                ex_str = " | ".join(d["examples"][:5])
                writer.writerow([title, name, d["count"], round(d["volume"], 1), f"{d['volume_share_pct']:.1f}%", ex_str, "", ""])
            writer.writerow([])
    print(f"[CSV] Created: {all_csv_path}", flush=True)
