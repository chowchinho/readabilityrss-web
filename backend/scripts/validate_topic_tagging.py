#!/usr/bin/env python3
"""
Topic Tagging Validation Experiment Script (Track B)
Spec: ANTIGRAVITY_TASK_topic_tagging_validation.md

Reads 5 articles per enabled feed (490 articles total) from backend/data/feeds.db,
cleans HTML/boilerplate, sends batched requests to DeepSeek (model: deepseek-v4-flash),
reconciles dropped items, runs A/B arm with feed names, consolidates primary topic labels,
computes daily volume share, and produces review data and labels.json.
"""

import os
import sys
import json
import random
import re
import sqlite3
import time
import csv
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# Ensure env vars are loaded from backend/.env
sys.stdout.reconfigure(encoding='utf-8')

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
TAGGING_MODEL = "deepseek-v4-flash"

# Log model discrepancy relative to translation.py (which uses deepseek-chat)
print(f"[INFO] Using explicit model for tagging: {TAGGING_MODEL}", flush=True)
print(f"[INFO] (Note: backend/app/services/translation.py pins 'deepseek-chat')", flush=True)

SYSTEM_PROMPT_TAGGING = """You are an expert news and article classifier. Analyze the provided articles and assign structured topic tags according to the following STRICT tagging contract.

JSON FIELD SPECIFICATION (PER ARTICLE):
1. "id" (integer): Mandatory. MUST exactly echo the input article id.
2. "primary" (string): 1-3 words in English, or "unknown".
   - THE AUDIENCE RULE: "primary" is the interest-community that would seek this article out (e.g. Gaming, Politics, Football, Retro Gaming, Advertising, Travel, Supernatural, Transport, Food & Dining, Photography). It is NOT the surface subject.
   - GEOGRAPHY RULE: Geography NEVER appears in "primary" (e.g. use Travel + region: Japan, NOT Domestic Travel; Transport + region: Hong Kong, NOT Hong Kong News).
   - NO GENERIC CONTAINERS: Never use "News", "Local News", "Current Affairs", "General", or "Products" as primary.
3. "secondary" (array of strings): 0-2 entries in English touching domain/technique/object acted on, or [].
4. "region" (string): Country-level (e.g. "Japan", "Hong Kong", "United States", "Taiwan", "China", "United Kingdom"), "Global", or "unknown".
   - AUDIENCE RULE FOR REGION: Region tracks the publication's target market/audience (e.g. HKEPC content -> "Hong Kong", LOVE WALKER -> "Japan").
5. "type" (enum): EXACTLY one of: "News", "Product Launch", "Review", "Buying Guide", "Listing", "Opinion", "Feature", "Deal", "Announcement", "Sponsored".
   - Product Launch = purchasable item available; Announcement = corporate/event info; Deal = sale/discount notice; Listing = catalog/job/classifieds post; Sponsored = advertorial.
6. "confidence" (enum): "high", "medium", or "low".

Output MUST be a JSON object with key "articles" containing an array of objects matching this schema.

Example Output:
{
  "articles": [
    {
      "id": 4821,
      "primary": "Travel",
      "secondary": ["Seasonal Events", "Night Views"],
      "region": "Japan",
      "type": "Feature",
      "confidence": "high"
    }
  ]
}
"""

def clean_body_text(content_html: str, feed_name: str = "") -> str:
    """Strips tags, badges, template junk, and leading navigation boilerplate, returning up to 600 chars."""
    if not content_html:
        return ""
    
    soup = BeautifulSoup(content_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
        
    text = soup.get_text(separator=" ", strip=True)
    
    # 2. Strip Translated by badge line
    text = re.sub(r'🌐\s*Translated by[^\n\r]*', '', text)
    text = re.sub(r'Translated by (DeepL|Google Translate|DeepSeek)[^\n\r]*', '', text)
    
    # 3. Strip leaked template junk
    text = re.sub(r'//=\s*get_post_meta[^\n\r]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'&\s*gt\s*;|&\s*lt\s*;', '', text)
    
    # 4. Leading navigation / boilerplate skip (§9.1 - e.g. The Verge)
    verge_nav_patterns = [
        r'^.*?(?:Posts from this topic will be added to your daily email digest|See All .*? Posts from this topic).*?digest\s*',
        r'^.*?See All\s+\w+\s+Posts\s*',
    ]
    for pattern in verge_nav_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)
        
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:600]

def sample_articles(db_path: str, seed: int = 42, articles_per_feed: int = 5):
    """Sample 5 articles per enabled feed from SQLite db."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, name FROM feed_sources WHERE enabled = 1 OR enabled IS NULL ORDER BY id")
    feeds = cursor.fetchall()
    
    print(f"[INFO] Found {len(feeds)} enabled feed sources.", flush=True)
    
    rng = random.Random(seed)
    sampled = []
    
    for feed in feeds:
        feed_id = feed["id"]
        feed_name = feed["name"]
        
        cursor.execute(
            """SELECT id, source_id, title, content, created_at
               FROM feed_articles
               WHERE source_id = ? AND parse_status = 'success'
                 AND (COALESCE(title, '') != '' OR COALESCE(content, '') != '')
               ORDER BY id""",
            (feed_id,)
        )
        articles = cursor.fetchall()
        
        if len(articles) < articles_per_feed:
            chosen = list(articles)
        else:
            chosen = rng.sample(articles, articles_per_feed)
            
        for art in chosen:
            cleaned = clean_body_text(art["content"] or "", feed_name)
            sampled.append({
                "id": art["id"],
                "source_id": art["source_id"],
                "feed_name": feed_name,
                "title": art["title"] or "",
                "body_snippet": cleaned,
                "created_at": art["created_at"]
            })
            
    conn.close()
    print(f"[INFO] Total articles sampled across feeds: {len(sampled)}")
    return sampled

def calculate_feed_rates(db_path: str):
    """Calculates daily article volume per feed: count / span_in_days."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, name FROM feed_sources WHERE enabled = 1 OR enabled IS NULL")
    feeds = cursor.fetchall()
    
    rates = {}
    total_calculated_rate = 0.0
    
    for feed in feeds:
        feed_id = feed["id"]
        cursor.execute(
            "SELECT created_at FROM feed_articles WHERE source_id = ? AND parse_status = 'success'",
            (feed_id,)
        )
        rows = cursor.fetchall()
        if not rows:
            rates[feed_id] = 1.0
            continue
            
        dates = []
        for r in rows:
            ca = r["created_at"]
            if not ca:
                continue
            try:
                dt = datetime.fromisoformat(ca.replace(" ", "T"))
                dates.append(dt)
            except Exception:
                pass
                
        if not dates or len(dates) < 2:
            rates[feed_id] = 1.0
        else:
            min_d = min(dates)
            max_d = max(dates)
            span_days = (max_d - min_d).total_seconds() / 86400.0
            if span_days < 0.05: # avoid division spike
                span_days = 0.05
            rate = len(dates) / span_days
            rates[feed_id] = rate
            
        total_calculated_rate += rates[feed_id]
        
    conn.close()
    print(f"[INFO] Estimated total daily volume across all feeds: {total_calculated_rate:.1f} articles/day", flush=True)
    return rates

TOTAL_USAGE = {
    "prompt_cache_miss": 0,
    "prompt_cache_hit": 0,
    "completion_tokens": 0,
    "total_cost_usd": 0.0
}

def call_deepseek_tagging_batch(items: list, include_feed_name: bool = False, max_retries: int = 3) -> dict:
    """Calls DeepSeek with a batch of articles and returns a dict mapping id -> tagging response."""
    global TOTAL_USAGE
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY environment variable is not set!")
        
    payload_articles = []
    for it in items:
        entry = {
            "id": it["id"],
            "title": it["title"],
            "body": it["body_snippet"]
        }
        if include_feed_name:
            entry["feed"] = it["feed_name"]
        payload_articles.append(entry)
        
    user_prompt = f"Articles to tag:\n{json.dumps({'articles': payload_articles}, ensure_ascii=False, indent=2)}"
    
    results = {}
    missing_items = list(items)
    
    for attempt in range(max_retries):
        if not missing_items:
            break
            
        batch_prompt = f"Articles to tag:\n{json.dumps({'articles': [ { 'id': x['id'], 'title': x['title'], 'body': x['body_snippet'], **({'feed': x['feed_name']} if include_feed_name else {})} for x in missing_items ]}, ensure_ascii=False, indent=2)}"
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": TAGGING_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_TAGGING},
                {"role": "user", "content": batch_prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 8192
        }
        
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=data, timeout=60)
            if resp.status_code != 200:
                print(f"[WARN] HTTP {resp.status_code} from DeepSeek: {resp.text[:200]}", flush=True)
                time.sleep(2 * (attempt + 1))
                continue
                
            res_json = resp.json()
            content = res_json["choices"][0]["message"]["content"]
            usage = res_json.get("usage", {}) or {}
            
            # Track usage metrics
            cache_hit = usage.get("prompt_cache_hit_tokens", usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
            prompt_tot = usage.get("prompt_tokens", 0)
            cache_miss = max(0, prompt_tot - cache_hit)
            completion_tot = usage.get("completion_tokens", 0)
            
            # Pricing: cache_miss $0.14/M, cache_hit $0.014/M, completion $0.28/M
            cost_usd = (cache_miss * 0.14 + cache_hit * 0.014 + completion_tot * 0.28) / 1000000.0
            
            TOTAL_USAGE["prompt_cache_miss"] += cache_miss
            TOTAL_USAGE["prompt_cache_hit"] += cache_hit
            TOTAL_USAGE["completion_tokens"] += completion_tot
            TOTAL_USAGE["total_cost_usd"] += cost_usd
            
            parsed = json.loads(content)
            
            art_list = parsed.get("articles", [])
            if not isinstance(art_list, list):
                # If model returned dict of dicts or single obj
                if isinstance(parsed, dict) and "id" in parsed:
                    art_list = [parsed]
                else:
                    art_list = []
                    
            for tagged in art_list:
                tid = tagged.get("id")
                if tid is not None:
                    results[tid] = tagged
                    
            # Reconcile missing
            sent_ids = {x["id"] for x in missing_items}
            got_ids = set(results.keys())
            still_missing_ids = sent_ids - got_ids
            missing_items = [x for x in missing_items if x["id"] in still_missing_ids]
            
            if still_missing_ids:
                print(f"[WARN] Batch returned {len(got_ids)}/{len(sent_ids)} items. Retrying {len(still_missing_ids)} missing items...", flush=True)
                time.sleep(1.5)
        except Exception as e:
            print(f"[WARN] Request error on attempt {attempt+1}: {e}", flush=True)
            time.sleep(2 * (attempt + 1))
            
    return results

def run_tagging(sampled_articles: list, include_feed_name: bool = False, batch_size: int = 20) -> dict:
    """Runs tagging across all sampled articles in batches."""
    all_results = {}
    total = len(sampled_articles)
    
    for i in range(0, total, batch_size):
        batch = sampled_articles[i:i+batch_size]
        arm_label = "With Feed Name" if include_feed_name else "Default (No Feed Name)"
        print(f"[TAGGING] Processing batch {i//batch_size + 1}/{(total + batch_size - 1)//batch_size} ({len(batch)} items) [{arm_label}]...", flush=True)
        
        batch_res = call_deepseek_tagging_batch(batch, include_feed_name=include_feed_name)
        all_results.update(batch_res)
        time.sleep(0.5) # Gentle pause between calls
        
    print(f"[TAGGING] Finished. Received {len(all_results)}/{total} responses.", flush=True)
    return all_results

def consolidate_topics(raw_primaries: list) -> dict:
    """Consolidates free-form primary labels into ~25 standard categories via DeepSeek."""
    distinct_labels = sorted(list(set(raw_primaries)))
    print(f"[CONSOLIDATION] Consolidating {len(distinct_labels)} distinct primary labels...", flush=True)
    
    system_prompt = """You are an expert taxonomy manager. Group free-form article topic labels into approximately 20-25 clean, canonical, high-level English interest categories (e.g., 'Politics', 'Gaming', 'Travel', 'Food & Dining', 'Consumer Tech', 'Entertainment', 'Automotive', 'Anime & Manga', 'Personal Finance', 'Sports', 'Photography', 'DIY & Hardware', 'Real Estate', 'Design & Art', 'Supernatural', 'Transport', 'Software & AI', 'Business', 'Fashion & Beauty', 'Science', 'Home & Garden', 'Books & Literature', 'Local Life', 'Health & Fitness').

Return a JSON object with key "mapping", where each key is an input label and each value is its single consolidated canonical topic name.
"""
    user_prompt = f"Labels to consolidate:\n{json.dumps(distinct_labels, ensure_ascii=False)}"
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": TAGGING_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    
    try:
        resp = requests.post(DEEPSEEK_URL, headers=headers, json=data, timeout=45)
        res_json = resp.json()
        content = res_json["choices"][0]["message"]["content"]
        mapping = json.loads(content).get("mapping", {})
        return mapping
    except Exception as e:
        print(f"[ERROR] Topic consolidation failed: {e}")
        # Fallback identity mapping
        return {lbl: lbl for lbl in distinct_labels}

def main():
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "feeds.db")
    if not os.path.exists(db_path):
        print(f"[ERROR] Database file not found at {db_path}")
        sys.exit(1)
        
    print(f"[START] Starting Topic Tagging Validation Experiment...")
    
    # 1. Sampling
    sampled = sample_articles(db_path, seed=42, articles_per_feed=5)
    feed_rates = calculate_feed_rates(db_path)
    
    # 2. Tagging (Arm A: Default - 490 articles)
    print("\n--- Arm A: Tagging 490 articles without feed names ---")
    results_default = run_tagging(sampled, include_feed_name=False, batch_size=20)
    
    # 3. Tagging (Arm B: A/B subset - 100 articles with feed names)
    print("\n--- Arm B: Tagging 100 article subset with feed names ---")
    ab_subset = sampled[:100]
    results_ab = run_tagging(ab_subset, include_feed_name=True, batch_size=20)
    
    # 4. Topic Consolidation Pass
    raw_primaries = [results_default[a["id"]].get("primary", "unknown") for a in sampled if a["id"] in results_default]
    consolidation_map = consolidate_topics(raw_primaries)
    
    # 5. Compute Volume Shares & Aggregate Statistics
    # Calculate total volume attributed across sampled articles
    # Each sampled article gets feed_rate[source_id] / 5.0
    total_sample_volume = 0.0
    article_volumes = {}
    for a in sampled:
        aid = a["id"]
        sid = a["source_id"]
        # Articles per feed sampled
        vol = feed_rates.get(sid, 1.0) / 5.0
        article_volumes[aid] = vol
        total_sample_volume += vol
        
    topic_stats = {}
    region_stats = {}
    type_stats = {}
    
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    secondary_carry_signal_count = 0
    
    for a in sampled:
        aid = a["id"]
        res = results_default.get(aid)
        if not res:
            continue
            
        vol = article_volumes[aid]
        raw_p = res.get("primary", "unknown")
        cons_topic = consolidation_map.get(raw_p, raw_p)
        region = res.get("region", "unknown")
        art_type = res.get("type", "News")
        conf = res.get("confidence", "high")
        
        confidence_counts[conf] = confidence_counts.get(conf, 0) + 1
        
        # Topic stats
        if cons_topic not in topic_stats:
            topic_stats[cons_topic] = {"count": 0, "volume": 0.0, "examples": []}
        topic_stats[cons_topic]["count"] += 1
        topic_stats[cons_topic]["volume"] += vol
        if len(topic_stats[cons_topic]["examples"]) < 5:
            topic_stats[cons_topic]["examples"].append(a["title"])
            
        # Region stats
        if region not in region_stats:
            region_stats[region] = {"count": 0, "volume": 0.0, "examples": []}
        region_stats[region]["count"] += 1
        region_stats[region]["volume"] += vol
        if len(region_stats[region]["examples"]) < 5:
            region_stats[region]["examples"].append(a["title"])
            
        # Type stats
        if art_type not in type_stats:
            type_stats[art_type] = {"count": 0, "volume": 0.0, "examples": []}
        type_stats[art_type]["count"] += 1
        type_stats[art_type]["volume"] += vol
        if len(type_stats[art_type]["examples"]) < 5:
            type_stats[art_type]["examples"].append(a["title"])
            
    # Calculate volume share %
    for t_dict in (topic_stats, region_stats, type_stats):
        for k, v in t_dict.items():
            v["volume_share_pct"] = (v["volume"] / total_sample_volume * 100.0) if total_sample_volume > 0 else 0.0
            
    # 6. A/B Arm Comparison
    ab_matches = {
        "primary_exact": 0,
        "consolidated_topic_match": 0,
        "region_match": 0,
        "type_match": 0,
        "total_compared": 0
    }
    
    for a in ab_subset:
        aid = a["id"]
        res_a = results_default.get(aid)
        res_b = results_ab.get(aid)
        if res_a and res_b:
            ab_matches["total_compared"] += 1
            p_a = res_a.get("primary", "")
            p_b = res_b.get("primary", "")
            if p_a == p_b:
                ab_matches["primary_exact"] += 1
            if consolidation_map.get(p_a, p_a) == consolidation_map.get(p_b, p_b):
                ab_matches["consolidated_topic_match"] += 1
            if res_a.get("region") == res_b.get("region"):
                ab_matches["region_match"] += 1
            if res_a.get("type") == res_b.get("type"):
                ab_matches["type_match"] += 1
                
    # 7. Primary Label Collision / Ambiguity Rate
    # Collision proxy: confidence == 'low' or 'medium'
    collision_count = confidence_counts.get("medium", 0) + confidence_counts.get("low", 0)
    collision_rate_pct = (collision_count / len(results_default) * 100.0) if results_default else 0.0
    
    # 8. Save labels.json
    output_labels = {
        "metadata": {
            "created_at": datetime.utcnow().isoformat() + "Z",
            "sampling_seed": 42,
            "total_sampled_articles": len(sampled),
            "tagging_model": TAGGING_MODEL,
            "estimated_daily_total_volume": total_sample_volume,
            "collision_rate_pct": collision_rate_pct,
            "confidence_distribution": confidence_counts,
            "ab_comparison_100_articles": ab_matches,
            "token_usage_and_cost": TOTAL_USAGE
        },
        "sampled_article_ids": [a["id"] for a in sampled],
        "topic_consolidation_mapping": consolidation_map,
        "topic_summary": topic_stats,
        "region_summary": region_stats,
        "type_summary": type_stats,
        "results_default_arm": results_default,
        "results_ab_feed_name_arm": results_ab
    }
    
    labels_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "labels.json")
    with open(labels_file_path, "w", encoding="utf-8") as f:
        json.dump(output_labels, f, ensure_ascii=False, indent=2)
        
    print(f"\n[DONE] Saved raw responses and analysis to {labels_file_path}")
    
    # 9. Generate CSV Review Files for Google Sheets Import
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    def export_summary_csv(filename, title, stats_dict):
        filepath = os.path.join(project_root, filename)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Axis", "Name", "Sample_Count", "Est_Daily_Flow_Articles", "Est_Share_Pct", "Example_Headlines", "Evaluation (more/less/depends)", "User_Comments"])
            
            # Sort by volume share descending
            sorted_items = sorted(stats_dict.items(), key=lambda x: x[1]["volume_share_pct"], reverse=True)
            for name, d in sorted_items:
                ex_str = " | ".join(d["examples"][:5])
                writer.writerow([title, name, d["count"], round(d["volume"], 1), f"{d['volume_share_pct']:.1f}%", ex_str, "", ""])
        print(f"[CSV] Created review CSV: {filepath}")

    export_summary_csv("topic_review.csv", "Topic", topic_stats)
    export_summary_csv("region_review.csv", "Region", region_stats)
    export_summary_csv("type_review.csv", "Type", type_stats)

    # Consolidated single CSV file
    all_csv_path = os.path.join(project_root, "topic_tagging_review_all.csv")
    with open(all_csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Axis", "Name", "Sample_Count", "Est_Daily_Flow_Articles", "Est_Share_Pct", "Example_Headlines", "Evaluation (more/less/depends)", "User_Comments"])
        
        for title, stats_dict in [("Topic", topic_stats), ("Region", region_stats), ("Type", type_stats)]:
            sorted_items = sorted(stats_dict.items(), key=lambda x: x[1]["volume_share_pct"], reverse=True)
            for name, d in sorted_items:
                ex_str = " | ".join(d["examples"][:5])
                writer.writerow([title, name, d["count"], round(d["volume"], 1), f"{d['volume_share_pct']:.1f}%", ex_str, "", ""])
            writer.writerow([]) # Empty row separator
    print(f"[CSV] Created combined review CSV: {all_csv_path}")

    # Print high-level summary to console
    print("\n================ TOPIC VALIDATION EXPERIMENT SUMMARY ================")
    print(f"Total Sampled Articles: {len(sampled)}")
    print(f"Estimated Daily Flow: {total_sample_volume:.1f} articles/day")
    print(f"Primary Label Ambiguity/Collision Rate: {collision_rate_pct:.1f}%")
    print(f"A/B Arm Agreement (n={ab_matches['total_compared']}): Topic={ab_matches['consolidated_topic_match']}% | Region={ab_matches['region_match']}% | Type={ab_matches['type_match']}%")
    print("=====================================================================\n")

if __name__ == "__main__":
    main()

