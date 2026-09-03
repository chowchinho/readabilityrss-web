"""Compare the server focal algorithm against the live client baseline.

Usage, from backend/:
    python scripts/focal_ab_report.py

Exits 0 if the acceptance bar is met, 1 if not.
"""

import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.focal_point import compute_focal_point  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(HERE, "tests", "fixtures")
IMAGE_DIR = os.path.join(FIXTURES, "focal_images")


def main():
    with open(os.path.join(FIXTURES, "focal_baseline.json"), encoding="utf-8") as f:
        entries = json.load(f)["entries"]

    rows = []
    excluded = []
    for entry in entries:
        path = os.path.join(IMAGE_DIR, entry["hash"] + ".jpg")
        if not os.path.exists(path):
            continue
        if entry.get("baseline_false_positive"):
            excluded.append(entry["hash"])
            continue
        with Image.open(path) as image:
            image.load()
            new = compute_focal_point(image)
        rows.append({
            "hash": entry["hash"],
            "mode": entry["mode"],
            "old": (entry["x"], entry["y"]),
            "new": new,
            "dx": abs(new[0] - entry["x"]),
            "dy": abs(new[1] - entry["y"]),
        })

    passed = True
    for mode, limit in (("face", 8.0), ("saliency", 8.0)):
        subset = [r for r in rows if r["mode"] == mode]
        if not subset:
            continue
        mean_dy = sum(r["dy"] for r in subset) / len(subset)
        mean_dx = sum(r["dx"] for r in subset) / len(subset)
        worst = max(subset, key=lambda r: r["dy"])
        over = [r for r in subset if r["dy"] > 15]
        ok = mean_dy <= limit and (mode != "face" or not over)
        passed = passed and ok
        print(f"\n{mode.upper()}  n={len(subset)}   {'PASS' if ok else 'FAIL'}")
        print(f"  mean |dx| {mean_dx:5.1f}   mean |dy| {mean_dy:5.1f}   (bar: mean |dy| <= {limit})")
        print(f"  worst dy  {worst['dy']:3d}  old {worst['old']} -> new {worst['new']}  {worst['hash']}")
        print(f"  images with dy > 15: {len(over)}" + ("  (must be 0 for face)" if mode == "face" else ""))

    if excluded:
        print(f"\nExcluded {len(excluded)} baseline entries verified as false positives:")
        for h in excluded:
            note = next(e["note"] for e in entries if e["hash"] == h)
            print(f"  {h[:8]}  {note}")

    print("\n" + ("=" * 50))
    print("ACCEPTANCE: " + ("PASS — Task 6 unblocked" if passed else "FAIL — Task 6 stays blocked"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
