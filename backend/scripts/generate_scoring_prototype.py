#!/usr/bin/env python3
"""
Generate Prototype Batch 1 (20 Random Articles) with ML Scoring & User Feedback Boxes.
Outputs:
  - scoring_prototype_batch1.csv
  - scoring_prototype_batch1.html
"""
import os
import sys
import json
import random
import sqlite3

sys.stdout.reconfigure(encoding='utf-8')

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
labels_path = os.path.join(backend_dir, "labels.json")
db_path = os.path.join(backend_dir, "data", "feeds.db")

with open(labels_path, "r", encoding="utf-8") as f:
    data = json.load(f)

results_default = data.get("results_default_arm", {})
consolidation_map = data.get("topic_consolidation_mapping", {})

# Weight tables learned from user review
TOPIC_WEIGHTS = {
    # +2 (Top Wanted)
    "Travel": 2, "Consumer Tech": 2, "Science": 2, "Software & AI": 2, "Entertainment": 2,
    # +1 (Wanted)
    "Photography": 1, "Anime & Manga": 1, "Food & Dining": 1, "Design & Art": 1, 
    "DIY & Hardware": 1, "Supernatural": 1, "Books & Literature": 1, "Home & Garden": 1,
    # 0 (Neutral / Depends)
    "Gaming": 0, "Local Life": 0, "Business & Economy": 0, "Sports": 0, 
    "Transport": 0, "Fashion & Beauty": 0, "Health & Fitness": 0, "unknown": 0,
    # -1 (Demoted)
    "Automotive": -1, "Personal Finance": -1, "Real Estate": -1,
    # -2 (Strongly Demoted)
    "Politics": -2
}

REGION_WEIGHTS = {
    "Japan": 1, "United Kingdom": 1, "Global": 1, "unknown": 1,
    "Taiwan": 0, "United States": 0, "Hong Kong": 0, "Germany": 0, "China": 0,
    "Australia": -1
}

TYPE_WEIGHTS = {
    "Feature": 1, "News": 1, "Announcement": 1, "Product Launch": 1, "Review": 1,
    "Opinion": 0, "Buying Guide": 0, "Sponsored": 0,
    "Deal": -1, "Listing": -1
}

# Select 20 random articles from tagged results
rng = random.Random(101) # Seed 101 for Batch 1
tagged_ids = sorted(list(results_default.keys()))
batch1_ids = rng.sample(tagged_ids, 20)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get feed names & original titles/content for the 20 articles
placeholders = ",".join(["?"] * len(batch1_ids))
cursor.execute(f"""
    SELECT a.id, a.title, a.content, a.url, f.name as feed_name
    FROM feed_articles a
    JOIN feed_sources f ON a.source_id = f.id
    WHERE a.id IN ({placeholders})
""", [int(i) for i in batch1_ids])
rows = {str(r["id"]): dict(r) for r in cursor.fetchall()}
conn.close()

from bs4 import BeautifulSoup
import re

def get_snippet(html_text):
    if not html_text: return ""
    soup = BeautifulSoup(html_text, "html.parser")
    for t in soup(["script", "style", "iframe", "svg"]): t.decompose()
    txt = soup.get_text(" ", strip=True)
    txt = re.sub(r'🌐\s*Translated by[^\n\r]*', '', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt[:300] + ("..." if len(txt) > 300 else "")

prototype_items = []

for idx, aid in enumerate(batch1_ids, start=1):
    tag_info = results_default[aid]
    art_row = rows.get(aid, {})
    
    raw_primary = tag_info.get("primary", "unknown")
    cons_topic = consolidation_map.get(raw_primary, raw_primary)
    region = tag_info.get("region", "unknown")
    art_type = tag_info.get("type", "News")
    sec_tags = tag_info.get("secondary", [])
    
    t_w = TOPIC_WEIGHTS.get(cons_topic, 0)
    r_w = REGION_WEIGHTS.get(region, 0)
    ty_w = TYPE_WEIGHTS.get(art_type, 0)
    
    total_score = t_w + r_w + ty_w
    breakdown = f"Topic: {cons_topic} ({t_w:+d}) | Region: {region} ({r_w:+d}) | Type: {art_type} ({ty_w:+d})"
    
    item = {
        "item_num": idx,
        "article_id": aid,
        "feed_name": art_row.get("feed_name", "Unknown Feed"),
        "title": art_row.get("title", "No Title"),
        "url": art_row.get("url", "#"),
        "snippet": get_snippet(art_row.get("content", "")),
        "primary_topic": cons_topic,
        "secondary_tags": ", ".join(sec_tags),
        "region": region,
        "type": art_type,
        "predicted_score": total_score,
        "breakdown": breakdown,
        "t_w": t_w,
        "r_w": r_w,
        "ty_w": ty_w
    }
    prototype_items.append(item)

# 1. Export CSV
import csv
csv_path = os.path.join(project_root, "scoring_prototype_batch1.csv")
with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow([
        "Batch_Item_#", "Article_ID", "Feed_Name", "Article_Title", 
        "ML_Primary_Topic", "ML_Region", "ML_Type", "ML_Secondary_Tags",
        "Predicted_ML_Score", "Score_Breakdown", "Article_Snippet",
        "User_Actual_Score (-2 to +2)", "User_Rationale_and_Comments"
    ])
    for item in prototype_items:
        writer.writerow([
            item["item_num"], item["article_id"], item["feed_name"], item["title"],
            item["primary_topic"], item["region"], item["type"], item["secondary_tags"],
            f"{item['predicted_score']:+d}", item["breakdown"], item["snippet"],
            "", "" # Blank for user
        ])
print(f"[PROTOTYPE] CSV exported to: {csv_path}", flush=True)

# 2. Export Interactive HTML Prototype
html_path = os.path.join(project_root, "scoring_prototype_batch1.html")

items_json = json.dumps(prototype_items, ensure_ascii=False)

html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ReadabilityRSS — Article Scoring Prototype (Batch 1)</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Lora:ital,wght@0,500;0,600;1,400&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #f8f6f0;
            --card-bg: #ffffff;
            --text-main: #2b2521;
            --text-muted: #6b635b;
            --accent: #c97d2e;
            --border: #e6e0d4;
            --badge-pos: #2e7d32;
            --badge-neg: #c62828;
            --badge-neu: #555555;
        }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 24px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
        }}
        .header {{
            background: #1c1510;
            color: #f5efe6;
            padding: 24px 32px;
            border-radius: 12px;
            margin-bottom: 24px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        }}
        .header h1 {{
            font-family: 'Lora', serif;
            margin: 0 0 8px 0;
            font-size: 26px;
            color: #ffffff;
        }}
        .header p {{
            margin: 0;
            font-size: 14px;
            color: #d0c4b6;
        }}
        .action-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #ffffff;
            padding: 16px 24px;
            border-radius: 8px;
            border: 1px solid var(--border);
            margin-bottom: 24px;
            position: sticky;
            top: 16px;
            z-index: 100;
            box-shadow: 0 4px 16px rgba(0,0,0,0.06);
        }}
        .btn {{
            background: var(--accent);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: opacity 0.2s;
        }}
        .btn:hover {{ opacity: 0.9; }}
        .card {{
            background: var(--card-bg);
            border-radius: 10px;
            border: 1px solid var(--border);
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.03);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            margin-bottom: 12px;
        }}
        .feed-name {{
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--accent);
        }}
        .article-title {{
            font-family: 'Lora', serif;
            font-size: 18px;
            font-weight: 600;
            margin: 4px 0 0 0;
            color: var(--text-main);
        }}
        .article-title a {{
            color: inherit;
            text-decoration: none;
        }}
        .article-title a:hover {{
            text-decoration: underline;
        }}
        .score-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 6px 14px;
            border-radius: 20px;
            font-weight: 700;
            font-size: 15px;
            white-space: nowrap;
        }}
        .score-pos {{ background: #e8f5e9; color: var(--badge-pos); border: 1px solid #a5d6a7; }}
        .score-neg {{ background: #ffebee; color: var(--badge-neg); border: 1px solid #ef9a9a; }}
        .score-neu {{ background: #f5f5f5; color: var(--badge-neu); border: 1px solid #e0e0e0; }}

        .tag-pills {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
            margin-bottom: 16px;
        }}
        .pill {{
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 4px;
            background: #f2eee5;
            color: #4a423a;
            font-weight: 500;
        }}
        .pill-sec {{
            background: #eef2f6;
            color: #3b5998;
        }}
        .snippet {{
            font-size: 13.5px;
            color: var(--text-muted);
            background: #faf8f5;
            padding: 12px 16px;
            border-radius: 6px;
            border-left: 3px solid var(--border);
            margin-bottom: 16px;
        }}
        .scoring-breakdown {{
            font-size: 12.5px;
            color: #555;
            background: #f5f3ec;
            padding: 8px 12px;
            border-radius: 6px;
            margin-bottom: 16px;
            font-family: monospace;
        }}
        .user-feedback-box {{
            display: grid;
            grid-template-columns: 180px 1fr;
            gap: 16px;
            background: #fffdf9;
            border: 1.5px dashed #d8cebc;
            padding: 16px;
            border-radius: 8px;
        }}
        .user-feedback-box label {{
            display: block;
            font-size: 12px;
            font-weight: 700;
            margin-bottom: 6px;
            color: #4a423a;
        }}
        select, textarea {{
            width: 100%;
            padding: 8px 12px;
            border: 1px solid #ccc;
            border-radius: 6px;
            font-family: inherit;
            font-size: 13.5px;
            box-sizing: border-box;
        }}
        textarea {{
            resize: vertical;
            height: 40px;
        }}
        select:focus, textarea:focus {{
            outline: none;
            border-color: var(--accent);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ReadabilityRSS — Scoring Prototype (Batch 1)</h1>
            <p>Review predicted Machine Learning scores (+2 to -2), provide your actual rating, and record your rationale to calibrate future ranker rules.</p>
        </div>

        <div class="action-bar">
            <div>
                <strong>Batch 1:</strong> 20 Random Articles Sampled
            </div>
            <button class="btn" onclick="exportCSV()">Export My Ratings to CSV</button>
        </div>

        <div id="cards-container"></div>
    </div>

    <script>
        const articles = {items_json};

        function renderCards() {{
            const container = document.getElementById('cards-container');
            container.innerHTML = articles.map(art => {{
                let scoreClass = 'score-neu';
                if (art.predicted_score > 0) scoreClass = 'score-pos';
                if (art.predicted_score < 0) scoreClass = 'score-neg';
                const scoreStr = (art.predicted_score > 0 ? '+' : '') + art.predicted_score;

                return `
                    <div class="card" id="card-${{art.article_id}}">
                        <div class="card-header">
                            <div>
                                <div class="feed-name">#${{art.item_num}} — ${{art.feed_name}}</div>
                                <h3 class="article-title"><a href="${{art.url}}" target="_blank">${{art.title}}</a></h3>
                            </div>
                            <div class="score-badge ${{scoreClass}}">
                                ML Score: ${{scoreStr}}
                            </div>
                        </div>

                        <div class="tag-pills">
                            <span class="pill">Topic: <strong>${{art.primary_topic}}</strong></span>
                            <span class="pill">Region: <strong>${{art.region}}</strong></span>
                            <span class="pill">Type: <strong>${{art.type}}</strong></span>
                            ${{art.secondary_tags ? `<span class="pill pill-sec">Secondary: ${{art.secondary_tags}}</span>` : ''}}
                        </div>

                        <div class="scoring-breakdown">
                            🧠 ML Formula: ${{art.breakdown}}
                        </div>

                        <div class="snippet">
                            ${{art.snippet}}
                        </div>

                        <div class="user-feedback-box">
                            <div>
                                <label>Your Actual Rating:</label>
                                <select id="score-${{art.article_id}}">
                                    <option value="">-- Select Score --</option>
                                    <option value="+2">+2 (Highly Interested)</option>
                                    <option value="+1">+1 (Interested)</option>
                                    <option value="0">0 (Neutral / Depends)</option>
                                    <option value="-1">-1 (Demote Slightly)</option>
                                    <option value="-2">-2 (Demote Strongly)</option>
                                </select>
                            </div>
                            <div>
                                <label>Your Rationale / Notes:</label>
                                <textarea id="rationale-${{art.article_id}}" placeholder="Why did you choose this score? (e.g. 'Interesting tech review', 'Duplicate news topic')"></textarea>
                            </div>
                        </div>
                    </div>
                `;
            }}).join('');
        }}

        function exportCSV() {{
            let csvContent = "data:text/csv;charset=utf-8,\\uFEFF";
            csvContent += "Batch_Item_#,Article_ID,Feed_Name,Article_Title,ML_Primary_Topic,ML_Region,ML_Type,Predicted_ML_Score,User_Actual_Score,User_Rationale\\n";

            articles.forEach(art => {{
                const userScore = document.getElementById(`score-${{art.article_id}}`).value || "";
                const userRat = (document.getElementById(`rationale-${{art.article_id}}`).value || "").replace(/"/g, '""');
                
                const titleEsc = `"${{art.title.replace(/"/g, '""')}}"`;
                const feedEsc = `"${{art.feed_name.replace(/"/g, '""')}}"`;
                const scoreStr = (art.predicted_score > 0 ? '+' : '') + art.predicted_score;

                csvContent += `${{art.item_num}},${{art.article_id}},${{feedEsc}},${{titleEsc}},${{art.primary_topic}},${{art.region}},${{art.type}},${{scoreStr}},"${{userScore}}","${{userRat}}"\\n`;
            }});

            const encodedUri = encodeURI(csvContent);
            const link = document.createElement("a");
            link.setAttribute("href", encodedUri);
            link.setAttribute("download", "scoring_prototype_batch1_rated.csv");
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }}

        renderCards();
    </script>
</body>
</html>
"""

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"[PROTOTYPE] Interactive HTML exported to: {html_path}", flush=True)
