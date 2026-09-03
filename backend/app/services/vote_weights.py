"""Turns votes and behavioural events into per-label weight adjustments.

Pure functions only - no database access. The caller supplies rows and the
current time, which is what makes decay testable without freezing the clock.
"""
import json
import math
from datetime import datetime

from .labels import canonical_label
from ..utils.timeutil import utcnow

VOTE_DEFAULTS = {
    "vote_explicit_cap": 4.0,
    "vote_behavioural_cap": 1.0,
    "vote_curve_k": 17,
    "vote_curve_k_secondary": 6,
    "vote_half_life_days": 90,
    # Sized for declared weights alone (+/-2) and never resized when votes added
    # +/-4 on top, so declared + explicit could reach 6.0 against a ceiling of 3.0.
    # Measured on live data at 3.0: eight topics, five types and three regions all
    # pinned at exactly +3.00 - Japan with 60 votes scored identically to the United
    # Kingdom with one - and 185 of 400 unread articles tied on the axis maximum.
    # 5.5 keeps the clamp meaningful as a backstop while letting vote counts separate.
    "vote_effective_clamp": 5.5,
    "vote_article_penalty": -4.0,
    "signal_open": 1.0,
    "signal_hover": 0.6,
    "signal_skip": -0.4,
    "signal_skip_min_dwell": 1,
    "signal_read_no_vote": -0.1,
    "rerank_target_floor": 0.02,
    # A secondary label with no votes of its own inherits a fraction of the taste
    # already expressed for the primary topics it usually appears alongside. See
    # _secondary_priors.
    "secondary_prior_factor": 0.35,
    "secondary_prior_k": 3,
}


def decay_factor(age_days: float, half_life_days: float) -> float:
    """Exponential decay, clamped at 1.0 so clock skew cannot amplify a signal."""
    if half_life_days <= 0:
        return 1.0
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def saturate(tally: float, cap: float, k: float) -> float:
    """Smooth approach to +/- cap. Early votes move a label a lot, later ones less.

    tanh rather than a linear ramp with a hard stop: a cliff at the cap would make
    the last vote before it worth a lot and the next worth nothing.
    """
    if k <= 0:
        return 0.0
    return cap * math.tanh(tally / k)


def resolve_settings(raw: dict | None) -> dict:
    """VOTE_DEFAULTS overlaid with any overrides, coerced to numbers.

    system_settings stores everything as TEXT, so an override written by hand arrives
    as "20" and would make tanh(n / "20") raise. Anything uncoercible falls back to
    the default rather than taking the process down.
    """
    merged = dict(VOTE_DEFAULTS)
    for key, default in VOTE_DEFAULTS.items():
        if not raw or key not in raw:
            continue
        try:
            merged[key] = float(raw[key])
        except (TypeError, ValueError):
            merged[key] = default
    return merged


BEHAVIOURAL_EVENTS = ("open", "hover", "skip", "read_no_vote")


def signal_value(event: dict, settings: dict) -> float:
    """Signed contribution of one behavioural event. 0.0 means "must not count"."""
    etype = event.get("event_type")
    if etype not in BEHAVIOURAL_EVENTS:
        return 0.0

    if etype == "skip":
        floor = settings.get("signal_skip_min_dwell", VOTE_DEFAULTS["signal_skip_min_dwell"])
        dwell = event.get("dwell_seconds")
        if dwell is None or dwell < floor:
            return 0.0
        return float(settings.get("signal_skip", VOTE_DEFAULTS["signal_skip"]))

    key = {
        "open": "signal_open",
        "hover": "signal_hover",
        "read_no_vote": "signal_read_no_vote",
    }[etype]
    return float(settings.get(key, VOTE_DEFAULTS[key]))


AXES = ("topic", "type", "region", "secondary")

_SINGLE_AXIS_COLUMNS = {
    "topic": "primary_topic",
    "type": "article_type",
    "region": "region",
}

CONTESTED_MIN_VOTES = 8
CONTESTED_NET_BAND = 0.25


def _parse_ts(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _age_days(value, now: datetime) -> float:
    ts = _parse_ts(value)
    if ts is None:
        return 0.0
    return (now - ts).total_seconds() / 86400.0


def _secondaries(row) -> list[str]:
    """Canonical secondary labels for a row, so the two spellings of one label share
    their votes instead of accumulating separate ones."""
    raw = row.get("secondary_topics")
    if isinstance(raw, list):
        values = [s for s in raw if isinstance(s, str)]
    elif isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            values = [raw]
        else:
            values = ([s for s in parsed if isinstance(s, str)]
                      if isinstance(parsed, list) else [])
    else:
        return []
    seen, out = set(), []
    for value in values:
        key = canonical_label(value)
        # One article tagged both "Figure" and "Figures" must not count twice.
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _labels_of(row) -> dict[str, list[str]]:
    out = {}
    for axis, column in _SINGLE_AXIS_COLUMNS.items():
        value = row.get(column)
        out[axis] = [value] if value and value != "unknown" else []
    out["secondary"] = _secondaries(row)
    return out


def _secondary_priors(cooccurrence: dict | None, topic_weights: dict,
                      factor: float) -> dict[str, float]:
    """What a secondary label is worth before it has any votes of its own.

    There are thousands of secondary labels and roughly a hundred votes, so estimating
    each label independently leaves most of them at zero - measured on the live pool,
    59% of unread articles had no secondary label carrying any vote evidence, which
    means Also could not order them at all however heavily it was weighted.

    Rather than shrink an unsupported label to zero, shrink it toward a value predicted
    from what the label co-occurs with (Agarwal et al., CIKM 2012, on post-read actions:
    "when features are available, we can achieve better performance by shrinking the
    factors toward values predicted by features, instead of zero"). Here the feature is
    the distribution of primary topics the label appears under across the archive, so
    "Gundam" inherits from Anime & Manga even on a Gaming article - which is what makes
    the prior informative rather than a restatement of the article's own topic term.
    """
    priors = {}
    for label, topics in (cooccurrence or {}).items():
        total = sum(topics.values())
        if total <= 0:
            continue
        acc = 0.0
        for topic, count in topics.items():
            entry = topic_weights.get(topic)
            if entry:
                acc += (count / total) * entry["effective"]
        if acc:
            priors[label] = factor * acc
    return priors


def compute_label_weights(votes: list[dict], signals: list[dict],
                          declared: dict, settings: dict,
                          now: datetime,
                          cooccurrence: dict | None = None) -> dict:
    """Per-label declared weight, vote count, both channel adjustments, and effective.

    cooccurrence maps a canonical secondary label to {primary_topic: article_count} over
    the archive; it is what lets a label with no votes inherit a prior. Omitting it
    reproduces the previous behaviour exactly.
    """
    s = resolve_settings(settings)
    half_life = s["vote_half_life_days"]

    explicit_tally = {axis: {} for axis in AXES}
    explicit_count = {axis: {} for axis in AXES}
    behavioural_tally = {axis: {} for axis in AXES}

    for row in votes:
        direction = 1.0 if row.get("vote") == "show_more" else -1.0
        weight = direction * decay_factor(_age_days(row.get("updated_at"), now), half_life)
        for axis, labels in _labels_of(row).items():
            for label in labels:
                explicit_tally[axis][label] = explicit_tally[axis].get(label, 0.0) + weight
                explicit_count[axis][label] = explicit_count[axis].get(label, 0) + 1

    for row in signals:
        value = signal_value(row, s)
        if value == 0.0:
            continue
        weight = value * decay_factor(_age_days(row.get("created_at"), now), half_life)
        for axis, labels in _labels_of(row).items():
            for label in labels:
                behavioural_tally[axis][label] = behavioural_tally[axis].get(label, 0.0) + weight

    result = {axis: {} for axis in AXES}
    # secondary is computed last: its prior reads the finished topic weights.
    for axis in sorted(AXES, key=lambda a: a == "secondary"):
        is_secondary = axis == "secondary"
        explicit_cap = (s["vote_explicit_cap"] * 0.3) if is_secondary else s["vote_explicit_cap"]
        explicit_k = s["vote_curve_k_secondary"] if is_secondary else s["vote_curve_k"]
        behav_cap = (s["vote_behavioural_cap"] * 0.3) if is_secondary else s["vote_behavioural_cap"]

        priors = {}
        prior_k = max(0.0, s["secondary_prior_k"])
        if is_secondary:
            priors = _secondary_priors(cooccurrence, result["topic"],
                                       s["secondary_prior_factor"])

        labels = set(declared.get(axis, {})) \
            | set(explicit_tally[axis]) | set(behavioural_tally[axis]) | set(priors)
        for label in labels:
            declared_value = float(declared.get(axis, {}).get(label, 0.0))
            tally = explicit_tally[axis].get(label, 0.0)
            count = explicit_count[axis].get(label, 0)
            explicit_adj = saturate(tally, explicit_cap, explicit_k)
            behav_adj = saturate(
                behavioural_tally[axis].get(label, 0.0), behav_cap, explicit_k)
            # The prior fades as a label earns evidence of its own, so a well-voted
            # label lands where it would have without any of this.
            prior = 0.0
            if is_secondary and prior_k and label in priors:
                prior = priors[label] * (prior_k / (count + prior_k))
            effective = declared_value + prior + explicit_adj + behav_adj
            clamp = s["vote_effective_clamp"]
            effective = max(-clamp, min(clamp, effective))
            result[axis][label] = {
                "votes": count,
                "declared": declared_value,
                "prior": prior,
                "explicit": explicit_adj,
                "behavioural": behav_adj,
                "effective": effective,
                "contested": bool(
                    count >= CONTESTED_MIN_VOTES
                    and abs(tally) / count < CONTESTED_NET_BAND
                ),
            }
    return result


import time as _time

_CACHE_TTL_SECONDS = 3600
_cache: dict = {"at": 0.0, "value": None}


def invalidate_weights_cache() -> None:
    _cache["at"] = 0.0
    _cache["value"] = None


async def get_effective_weights(force: bool = False, db_instance=None) -> dict:
    """Cached per-label weights. Call invalidate_weights_cache() after any vote."""
    from ..database import db as global_db
    from .ranking import DECLARED_WEIGHTS
    database = db_instance or global_db

    now_mono = _time.monotonic()
    if not force and _cache["value"] is not None \
            and (now_mono - _cache["at"]) < _CACHE_TTL_SECONDS:
        return _cache["value"]

    settings = database.get_system_settings_sync()
    votes = await database.get_article_votes()
    signals = await database.get_behavioural_signals()
    cooccurrence = await database.get_secondary_cooccurrence()
    value = compute_label_weights(
        votes, signals, DECLARED_WEIGHTS, settings, utcnow(),
        cooccurrence=cooccurrence)
    _cache["at"] = now_mono
    _cache["value"] = value
    return value
