#!/usr/bin/env python3
"""
Sanity-check the user's filled weights against the 340 labelled articles before
any of it becomes code. Answers one question: does this score actually separate
articles, or is it a near-constant offset?
"""
import collections
import csv
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)

with open(os.path.join(backend_dir, "labels.json"), encoding="utf-8") as f:
    data = json.load(f)
cmap = data["topic_consolidation_mapping"]
arm = data["results_default_arm"]

rows = list(
    csv.reader(open(os.path.join(project_root, "FILLED - ranking_rulings.csv"), encoding="utf-8-sig"))
)

W = {"Topic": {}, "Region": {}, "Type": {}}
for r in rows:
    if len(r) < 5 or r[0] != "B" or ":" not in r[1]:
        continue
    axis, name = (p.strip() for p in r[1].split(":", 1))
    raw = r[4].strip()
    if not raw:
        continue
    try:
        W[axis][name] = int(raw.split()[0])
    except ValueError:
        continue

# Volume shares, for weighting the axis-informativeness check.
share = collections.defaultdict(dict)
with open(os.path.join(project_root, "topic_tagging_review_all_replied.csv"), encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r["Axis"] in ("Topic", "Region", "Type"):
            try:
                share[r["Axis"]][r["Name"]] = float(r["Est_Share_Pct"].strip("%"))
            except ValueError:
                pass


def axis_report(axis):
    """An axis only ranks if its values differ. Measure how much they do, by volume."""
    weights = W[axis]
    total = sum(share[axis].values()) or 1.0
    by_weight = collections.defaultdict(float)
    for name, pct in share[axis].items():
        by_weight[weights.get(name, 0)] += pct
    modal_w, modal_pct = max(by_weight.items(), key=lambda kv: kv[1])
    mean = sum(w * p for w, p in by_weight.items()) / total
    var = sum(p * (w - mean) ** 2 for w, p in by_weight.items()) / total
    print(f"\n{axis}:")
    for w in sorted(by_weight, reverse=True):
        bar = "#" * int(by_weight[w] / 2)
        print(f"   {w:+d}  {by_weight[w]:5.1f}% of flow  {bar}")
    print(f"   -> modal weight {modal_w:+d} covers {modal_pct:.1f}% of flow;"
          f" volume-weighted sd {var ** 0.5:.2f}")
    return modal_pct


print("=" * 72)
print("AXIS INFORMATIVENESS  (an axis that is near-constant cannot rank anything)")
print("=" * 72)
for axis in ("Topic", "Region", "Type"):
    axis_report(axis)

print()
print("=" * 72)
print("SCORE DISTRIBUTION over the 340 labelled articles")
print("=" * 72)

scores = []
for rec in arm.values():
    topic = cmap.get(rec["primary"], rec["primary"])
    s = (
        W["Topic"].get(topic, 0)
        + W["Region"].get(rec.get("region", "unknown"), 0)
        + W["Type"].get(rec.get("type", "News"), 0)
    )
    scores.append((s, topic, rec.get("region"), rec.get("type")))

hist = collections.Counter(s for s, *_ in scores)
n = len(scores)
for s in sorted(hist, reverse=True):
    pct = 100 * hist[s] / n
    print(f"   {s:+3d}  {hist[s]:4d} articles ({pct:5.1f}%)  {'#' * int(pct)}")

vals = sorted(s for s, *_ in scores)
print(f"\n   n={n}  min={vals[0]}  max={vals[-1]}  median={vals[n // 2]}")
top_band = max(hist.values()) / n
print(f"   largest single score band holds {100 * top_band:.1f}% of articles")

distinct_in_top = len({s for s, *_ in scores if s >= vals[int(n * 0.9)]})
print(f"   distinct scores in the top decile: {distinct_in_top}")

print()
print("=" * 72)
print("WHAT LANDS AT THE BOTTOM  (these are what demotion actually buries)")
print("=" * 72)
worst = sorted(scores)[:12]
for s, t, rg, ty in worst:
    print(f"   {s:+3d}   {t:22s} {str(rg):16s} {ty}")

print()
print("=" * 72)
print("MISSING WEIGHTS  (labels produced by the splits, not yet weighted)")
print("=" * 72)
SPLIT_CHILDREN = [
    "Skincare & Beauty", "Sneakers & Streetwear", "Fashion & Accessories",
    "Crime & Policing", "Culture", "Education",
    "Football", "Other Sports", "Retro Gaming",
]
for name in SPLIT_CHILDREN:
    print(f"   Topic: {name:26s} -> no weight")
