#!/usr/bin/env python3
"""
Generate the ranking rulings sheet: the decisions still needed before the
scoring model can be built. Sections:

  A. TOPIC SPLIT   - consolidated topics that merged distinctions the user named
  B. WEIGHT        - final -2..+2 weight per label on each axis
  C. INTERACTION   - (axis_a, axis_b) override cells implied by review comments
  D. SECONDARY     - sparse opt-in watchlist, scored at 30% of a primary weight

Reads: backend/labels.json, topic_tagging_review_all_replied.csv
Writes: ranking_rulings.csv (repo root)
"""
import csv
import json
import os
import sys
import collections

sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)

with open(os.path.join(backend_dir, "labels.json"), encoding="utf-8") as f:
    data = json.load(f)

cmap = data["topic_consolidation_mapping"]
default_arm = data["results_default_arm"]

reverse_map = collections.defaultdict(list)
for raw, consolidated in cmap.items():
    reverse_map[consolidated].append(raw)

secondaries_by_topic = collections.defaultdict(collections.Counter)
for record in default_arm.values():
    topic = cmap.get(record["primary"], record["primary"])
    for sub in record.get("secondary") or []:
        secondaries_by_topic[topic][sub] += 1

review_path = os.path.join(project_root, "topic_tagging_review_all_replied.csv")
with open(review_path, encoding="utf-8-sig") as f:
    review = [r for r in csv.DictReader(f) if r["Axis"]]


def share(row):
    try:
        return float(row["Est_Share_Pct"].strip("%"))
    except ValueError:
        return 0.0


# Proposals carry over the tiering already in generate_scoring_prototype.py rather than
# inventing a new one, so this sheet reads as a continuation of the prototype the user saw.
# `depends` rows are left blank on purpose - they are the whole point of the sheet.
PROTOTYPE_WEIGHTS = {
    "Topic": {
        "Travel": 2, "Consumer Tech": 2, "Science": 2, "Software & AI": 2,
        "Entertainment": 2, "Photography": 1, "Anime & Manga": 1, "Food & Dining": 1,
        "Design & Art": 1, "DIY & Hardware": 1, "Supernatural": 1,
        "Books & Literature": 1, "Home & Garden": 1, "Automotive": -1,
        "Personal Finance": -1, "Real Estate": -1, "Politics": -2,
    },
    "Region": {
        "Japan": 1, "United Kingdom": 1, "Global": 1, "unknown": 1, "Australia": -1,
    },
    "Type": {
        "Feature": 1, "News": 1, "Announcement": 1, "Product Launch": 1, "Review": 1,
        "Deal": -1, "Listing": -1,
    },
}


def proposed_weight(axis, name, verdict):
    if verdict == "depends":
        return ""
    w = PROTOTYPE_WEIGHTS.get(axis, {}).get(name)
    if w is None:
        return ""
    return f"+{w}" if w > 0 else str(w)

# Topics whose consolidation merged distinctions the user explicitly named.
# Each entry: consolidated -> (proposed splits, the comment that forces the split)
SPLITS = {
    "Fashion & Beauty": (
        ["Skincare & Beauty", "Sneakers & Streetwear", "Fashion & Accessories"],
        'Skincare and Sneakers were SEPARATE raw labels before consolidation merged them. '
        'Your comment "Not quite interested in skin care, but sneaker should show more" '
        "cannot be expressed while they share one weight.",
    ),
    "Local Life": (
        ["Crime & Policing", "Culture", "Education"],
        'Absorbed Crime, Culture, Education AND the literal label "unknown". '
        "A bucket containing unknown cannot carry a meaningful weight.",
    ),
    "Sports": (
        ["Football", "Other Sports"],
        "Absorbed Football, Fantasy Football and Horse Racing. Football dominates the "
        "sampled volume; a single Sports weight applies your football verdict to horse racing.",
    ),
    "Gaming": (
        ["Gaming", "Retro Gaming"],
        'Your comment "General gaming or retro gaming preferred, reduce [the rest]" needs '
        "Retro Gaming separable. It was a raw primary in earlier batches.",
    ),
}

# Interaction cells implied directly by the review comments.
INTERACTIONS = [
    (
        "Region=Hong Kong x Topic=Football/Other Sports",
        "-2",
        '"Local news in Hong Kong preferred but sports news are not needed" - Hong Kong is '
        "positive on the region axis, so without this cell HK sports scores positive.",
    ),
    (
        "Region=Hong Kong x Topic=Crime & Policing/Culture",
        "+1",
        '"Local news in Hong Kong preferred" - the local-news half of the same comment.',
    ),
    (
        "Type=Sponsored x Region=Japan",
        "+2",
        '"If it is Japan-related, happy to know" - Sponsored is otherwise neutral-to-negative.',
    ),
    (
        "Type=Buying Guide x (any topic)",
        "0",
        '"Depends on the subject of the buying guide" - proposal is NO override: set Buying '
        "Guide to 0 and let the topic weight decide. Confirm or give a cell.",
    ),
]

rows = []


def section(title):
    rows.append({})
    rows.append({"Section": f"### {title}"})


rows.append(
    {
        "Section": "A. TOPIC SPLIT",
        "Item": "Consolidated topic",
        "Est_Share_Pct": "Share",
        "Proposed": "Proposed split",
        "Your_Ruling": "split / keep",
        "Notes": "Why this is being asked",
    }
)
for consolidated, (splits, why) in SPLITS.items():
    match = next(
        (r for r in review if r["Axis"] == "Topic" and r["Name"] == consolidated), None
    )
    rows.append(
        {
            "Section": "A",
            "Item": consolidated,
            "Est_Share_Pct": match["Est_Share_Pct"] if match else "",
            "Proposed": " | ".join(splits),
            "Your_Ruling": "",
            "Notes": f"{why} Raw labels merged: {', '.join(sorted(reverse_map[consolidated]))}",
        }
    )

section("B. WEIGHTS  (-2 strongly demote .. +2 strongly promote, 0 neutral)")
rows.append(
    {
        "Section": "B",
        "Item": "Axis / Label",
        "Est_Share_Pct": "Share",
        "Proposed": "Proposed weight",
        "Your_Ruling": "your weight",
        "Notes": "Your earlier verdict + comment",
    }
)
for axis in ("Topic", "Region", "Type"):
    for r in sorted(
        (r for r in review if r["Axis"] == axis), key=share, reverse=True
    ):
        verdict = (r.get("Evaluation (more/less/depends)") or "").strip().lower()
        comment = (r.get("User_Comments") or "").strip()
        needs_ruling = verdict == "depends"
        rows.append(
            {
                "Section": "B",
                "Item": f"{axis}: {r['Name']}",
                "Est_Share_Pct": r["Est_Share_Pct"],
                "Proposed": proposed_weight(axis, r["Name"], verdict),
                "Your_Ruling": "",
                "Notes": ("NEEDS RULING - " if needs_ruling else "")
                + f"marked '{verdict}'"
                + (f" - \"{comment}\"" if comment else ""),
            }
        )

section("C. INTERACTION OVERRIDES  (added on top of the per-axis weights)")
rows.append(
    {
        "Section": "C",
        "Item": "Cell",
        "Est_Share_Pct": "",
        "Proposed": "Proposed",
        "Your_Ruling": "your value",
        "Notes": "Source comment",
    }
)
for cell, proposed, why in INTERACTIONS:
    rows.append(
        {
            "Section": "C",
            "Item": cell,
            "Est_Share_Pct": "",
            "Proposed": proposed,
            "Your_Ruling": "",
            "Notes": why,
        }
    )

section("D. SECONDARY WATCHLIST  (scored at 30% weight, capped, opt-in only)")
rows.append(
    {
        "Section": "D",
        "Item": "Secondary label",
        "Est_Share_Pct": "Seen",
        "Proposed": "Proposed",
        "Your_Ruling": "your weight",
        "Notes": "Observed under",
    }
)
# Seed only from topics the user commented on - everything unlisted scores 0.
SEED_TOPICS = ["Fashion & Beauty", "Gaming", "Sports"]
for topic in SEED_TOPICS:
    for sub, n in secondaries_by_topic[topic].most_common(12):
        rows.append(
            {
                "Section": "D",
                "Item": sub,
                "Est_Share_Pct": str(n),
                "Proposed": "",
                "Your_Ruling": "",
                "Notes": f"seen under {topic}",
            }
        )

out_path = os.path.join(project_root, "ranking_rulings.csv")
fields = ["Section", "Item", "Est_Share_Pct", "Proposed", "Your_Ruling", "Notes"]
with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fields})

print(f"wrote {out_path}  ({len(rows)} rows)")
print(f"  needs ruling in B: {sum(1 for r in rows if 'NEEDS RULING' in r.get('Notes',''))}")
