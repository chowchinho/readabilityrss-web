#!/usr/bin/env python3
"""
Utility to export CSV review files with User_Comments column from labels.json
"""
import os
import json
import csv

def generate_csvs():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    labels_path = os.path.join(root, "labels.json")
    
    if not os.path.exists(labels_path):
        print(f"[ERROR] labels.json not found at {labels_path}")
        return

    with open(labels_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    topic_stats = data.get("topic_summary", {})
    region_stats = data.get("region_summary", {})
    type_stats = data.get("type_summary", {})
    total_vol = data.get("metadata", {}).get("estimated_daily_total_volume", 1.0)

    def export_csv(filename, title, stats_dict):
        filepath = os.path.join(root, filename)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Axis", "Name", "Sample_Count", "Est_Daily_Flow_Articles", "Est_Share_Pct", "Example_Headlines", "Evaluation (more/less/depends)", "User_Comments"])
            
            sorted_items = sorted(stats_dict.items(), key=lambda x: x[1].get("volume_share_pct", 0), reverse=True)
            for name, d in sorted_items:
                ex_str = " | ".join(d.get("examples", [])[:5])
                writer.writerow([
                    title,
                    name,
                    d.get("count", 0),
                    round(d.get("volume", 0), 1),
                    f"{d.get('volume_share_pct', 0):.1f}%",
                    ex_str,
                    "",
                    ""
                ])
        print(f"[CSV] Created: {filepath}")

    export_csv("topic_review.csv", "Topic", topic_stats)
    export_csv("region_review.csv", "Region", region_stats)
    export_csv("type_review.csv", "Type", type_stats)

    all_csv_path = os.path.join(root, "topic_tagging_review_all.csv")
    with open(all_csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Axis", "Name", "Sample_Count", "Est_Daily_Flow_Articles", "Est_Share_Pct", "Example_Headlines", "Evaluation (more/less/depends)", "User_Comments"])
        
        for title, stats_dict in [("Topic", topic_stats), ("Region", region_stats), ("Type", type_stats)]:
            sorted_items = sorted(stats_dict.items(), key=lambda x: x[1].get("volume_share_pct", 0), reverse=True)
            for name, d in sorted_items:
                ex_str = " | ".join(d.get("examples", [])[:5])
                writer.writerow([
                    title,
                    name,
                    d.get("count", 0),
                    round(d.get("volume", 0), 1),
                    f"{d.get('volume_share_pct', 0):.1f}%",
                    ex_str,
                    "",
                    ""
                ])
            writer.writerow([])
    print(f"[CSV] Created combined review file: {all_csv_path}")

if __name__ == "__main__":
    generate_csvs()
