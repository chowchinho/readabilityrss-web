"""Pure article scoring function and human-readable explanation builder."""
from datetime import datetime
import math
import json

from .labels import canonical_label
from .vote_weights import _parse_ts
from ..utils.timeutil import utcnow

# Shipped neutral: every label starts at 0 so a new instance has no opinion of its
# own and learns entirely from your votes. The keys define the vocabulary the
# Options screen lists; edit the values to seed a starting preference.
TOPIC_WEIGHTS = {
    "Travel": 0, "Gaming": 0, "Consumer Tech": 0, "Science": 0, "Software & AI": 0,
    "Entertainment": 0, "Photography": 0, "Anime & Manga": 0, "Design & Art": 0,
    "DIY & Hardware": 0, "Supernatural": 0, "Real Estate": 0, "Retro Gaming": 0,
    "Local Life": 0, "Business & Economy": 0, "Sports": 0, "Transport": 0,
    "Food & Dining": 0, "Books & Literature": 0,
    "Skincare & Beauty": 0, "Sneakers & Streetwear": 0, "Fashion & Accessories": 0,
    "Crime & Policing": 0, "Culture": 0, "Education": 0, "Football": 0, "Other Sports": 0,
    "Health & Fitness": 0, "Home & Garden": 0,
    "Politics": 0, "Automotive": 0, "Personal Finance": 0,
    "unknown": 0,
}

REGION_WEIGHTS = {
    "Japan": 0, "United Kingdom": 0,
    "Taiwan": 0, "Global": 0, "Hong Kong": 0,
    "United States": 0,
    "Australia": 0, "Germany": 0, "China": 0,
    "unknown": 0,
}

TYPE_WEIGHTS = {
    "Feature": 0, "News": 0, "Announcement": 0, "Product Launch": 0, "Review": 0,
    "Opinion": 0, "Sponsored": 0,
    "Buying Guide": 0,
    "Deal": 0, "Listing": 0,
    "unknown": 0,
}

SECONDARY_WEIGHTS = {}  # sparse opt-in; anything absent scores 0

SECONDARY_FACTOR = 0.3
# Floor for the secondary clamp below. Without it, a primary topic whose effective
# weight is exactly 0 - the pre-vote state of `unknown`, the default for every
# untagged article - zeroes out all secondary vote signal for that article.
MIN_SECONDARY_CAP = 1.0

EXPOSURE_K = 1.0              # maximum penalty magnitude
EXPOSURE_TAU = 2.0            # visits to approach saturation
EXPOSURE_HALF_LIFE_HOURS = 48.0


def exposure_penalty(count: int, last_seen_at: str | datetime | None,
                     now: datetime | None = None) -> float:
    """Negative, saturating, time-decaying penalty for repeated impressions."""
    if count <= 0:
        return 0.0
    ts = _parse_ts(last_seen_at)
    if ts is None:
        return 0.0
    if now is None:
        now = utcnow()
    hours_since_last_seen = (now - ts).total_seconds() / 3600.0
    count_factor = 1.0 - math.exp(-count / EXPOSURE_TAU)
    if hours_since_last_seen <= 0.0:
        recency_factor = 1.0
    else:
        recency_factor = math.exp(-hours_since_last_seen / EXPOSURE_HALF_LIFE_HOURS)
    return -EXPOSURE_K * count_factor * recency_factor

# Pairwise adjustments, e.g. ("region:Japan", "topic:Travel"): 2 to boost Japanese
# travel writing above either signal alone. Empty by default.
INTERACTIONS = {}

# Secondary labels are matched canonically, so the declared table must be keyed the same
# way or a hand-written entry like "Running Shoes" would never match "running shoe".
SECONDARY_WEIGHTS_CANONICAL = {canonical_label(k): v for k, v in SECONDARY_WEIGHTS.items()}

DECLARED_WEIGHTS = {
    "topic": TOPIC_WEIGHTS,
    "type": TYPE_WEIGHTS,
    "region": REGION_WEIGHTS,
    "secondary": SECONDARY_WEIGHTS_CANONICAL,
}

_AXIS_TABLES = {
    "topic": TOPIC_WEIGHTS,
    "type": TYPE_WEIGHTS,
    "region": REGION_WEIGHTS,
}


def _axis_entry(axis: str, label: str, weights: dict | None) -> dict:
    """Effective weight for one label, plus the provenance the tooltip shows."""
    if axis in _AXIS_TABLES:
        key = label
        declared = float(_AXIS_TABLES[axis].get(label, 0))
    else:
        key = canonical_label(label)
        declared = float(SECONDARY_WEIGHTS_CANONICAL.get(key, 0))
    entry = ((weights or {}).get(axis) or {}).get(key)
    if not entry:
        return {"declared": declared, "votes": 0, "prior": 0.0,
                "behavioural": 0.0, "effective": declared}
    return {
        "declared": float(entry.get("declared", declared)),
        "votes": int(entry.get("votes", 0)),
        "prior": float(entry.get("prior", 0.0)),
        "behavioural": float(entry.get("behavioural", 0.0)),
        "effective": float(entry.get("effective", declared)),
    }


def score_article_breakdown(tags: dict | None, feed_age_percentile: float,
                            weights: dict | None = None,
                            vote: str | None = None,
                            settings: dict | None = None,
                            exposure: tuple[int, str] | None = None,
                            is_saved: bool = False,
                            now: datetime | None = None) -> dict:
    """Full arithmetic for one article. Every term the total is made of appears in
    `terms`, so the tooltip can be verified by adding the rows up."""
    tags = tags or {}
    primary = tags.get("primary") or "unknown"
    region = tags.get("region") or "unknown"
    art_type = tags.get("type") or "unknown"
    secondary = tags.get("secondary") or []
    if isinstance(secondary, str):
        try:
            secondary = json.loads(secondary)
        except Exception:
            secondary = [secondary]
    if not isinstance(secondary, list):
        secondary = []

    terms = []

    axis_values = {}
    for axis, label in (("topic", primary), ("type", art_type), ("region", region)):
        entry = _axis_entry(axis, label, weights)
        axis_values[axis] = entry["effective"]
        terms.append({
            "kind": "axis", "axis": axis, "multiplier": 1.0, "label": label,
            "declared": entry["declared"], "votes": entry["votes"],
            "prior": entry["prior"],
            "behavioural": entry["behavioural"],
            "contribution": entry["effective"],
        })

    raw_sec_sum = 0.0
    for label in secondary:
        if not isinstance(label, str):
            continue
        entry = _axis_entry("secondary", label, weights)
        raw_sec_sum += entry["effective"]
        terms.append({
            "kind": "axis", "axis": "secondary", "multiplier": SECONDARY_FACTOR,
            "label": label, "declared": entry["declared"], "votes": entry["votes"],
            "prior": entry["prior"],
            "behavioural": entry["behavioural"],
            # Reported for display only; the axis total is applied by the subtotal row
            # below, so this must not be summed into the score.
            "contribution": 0.0,
            "raw": entry["effective"],
        })

    sec_score = SECONDARY_FACTOR * raw_sec_sum
    max_sec = max(MIN_SECONDARY_CAP, abs(axis_values["topic"]))
    clamped = False
    if sec_score > max_sec:
        sec_score, clamped = float(max_sec), True
    elif sec_score < -max_sec:
        sec_score, clamped = float(-max_sec), True
    terms.append({
        "kind": "secondary_subtotal", "label": "Also", "raw": raw_sec_sum,
        "multiplier": SECONDARY_FACTOR, "clamped": clamped,
        "contribution": sec_score,
    })

    active_keys = set()
    if region and region != "unknown":
        active_keys.add(f"region:{region}")
    if primary and primary != "unknown":
        active_keys.add(f"topic:{primary}")
    if art_type and art_type != "unknown":
        active_keys.add(f"type:{art_type}")
    for (k1, k2), boost in INTERACTIONS.items():
        if k1 in active_keys and k2 in active_keys:
            terms.append({
                "kind": "pair",
                "label": f"{k1.split(':', 1)[1]} x {k2.split(':', 1)[1]}",
                "contribution": float(boost),
            })

    perc = max(0.0, min(1.0, float(feed_age_percentile)))
    recency = 1.0 * math.exp(-3.0 * perc)
    terms.append({"kind": "freshness", "label": "Freshness",
                  "contribution": recency})

    if vote == "show_less":
        penalty = float((settings or {}).get("vote_article_penalty", -4.0))
        terms.append({"kind": "vote_penalty", "label": "You said show less",
                      "contribution": penalty})

    if not is_saved and exposure:
        count, last_seen_at = exposure
        exp_penalty = exposure_penalty(count, last_seen_at, now=now)
        if exp_penalty != 0.0:
            terms.append({
                "kind": "exposure",
                "label": "Already shown",
                "contribution": exp_penalty,
            })

    total = sum(t["contribution"] for t in terms)

    parts = [v for v in (primary, region, art_type) if v and v != "unknown"]
    reason = ", ".join(parts) if parts else "General article"

    return {"total": total, "reason": reason, "terms": terms}


def score_article(tags: dict | None, feed_age_percentile: float,
                  weights: dict | None = None, vote: str | None = None,
                  settings: dict | None = None,
                  exposure: tuple[int, str] | None = None,
                  is_saved: bool = False,
                  now: datetime | None = None) -> tuple[float, str]:
    bd = score_article_breakdown(tags, feed_age_percentile, weights, vote, settings,
                                 exposure=exposure, is_saved=is_saved, now=now)
    return (bd["total"], bd["reason"])
