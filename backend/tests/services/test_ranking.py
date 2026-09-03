from datetime import datetime, timedelta
import pytest
from app.services.labels import canonical_label
from app.services.ranking import (
    score_article, score_article_breakdown, exposure_penalty,
    EXPOSURE_K, DECLARED_WEIGHTS, INTERACTIONS,
    TOPIC_WEIGHTS, TYPE_WEIGHTS, REGION_WEIGHTS, SECONDARY_WEIGHTS,
    SECONDARY_WEIGHTS_CANONICAL,
)

TAGS = {"primary": "Consumer Tech", "region": "Global",
        "type": "News", "secondary": ["Samsung"]}


def test_an_interaction_pair_shifts_the_score(monkeypatch):
    """Declared pairs adjust beyond what either axis contributes alone.

    Injects its own pair rather than relying on shipped ones: the defaults are
    neutral so that a new instance starts with no opinion of its own.
    """
    monkeypatch.setitem(INTERACTIONS, ("region:Hong Kong", "topic:Football"), -2)
    paired, _ = score_article({"primary": "Football", "region": "Hong Kong",
                               "type": "News", "secondary": []}, 0.5)
    unpaired, _ = score_article({"primary": "Crime & Policing", "region": "Hong Kong",
                                 "type": "News", "secondary": []}, 0.5)
    assert paired < unpaired

def test_unknown_and_missing_labels_score_zero_not_negative():
    s, _ = score_article({"primary": None, "region": "unknown",
                          "type": None, "secondary": []}, 0.5)
    assert s == pytest.approx(0.0, abs=1.0)  # recency only

def test_secondary_cannot_flip_a_primary(monkeypatch):
    monkeypatch.setitem(TOPIC_WEIGHTS, "Travel", 2)
    for _label in ("Lotion", "Sensitive Skin", "Sales"):
        monkeypatch.setitem(SECONDARY_WEIGHTS_CANONICAL, canonical_label(_label), -2)
    base, _ = score_article({"primary": "Travel", "region": "Japan",
                             "type": "Feature", "secondary": []}, 0.5)
    dragged, _ = score_article({"primary": "Travel", "region": "Japan", "type": "Feature",
                                "secondary": ["Lotion", "Sensitive Skin", "Sales"]}, 0.5)
    assert dragged < base and dragged > 0

def test_freshness_is_bounded_at_one():
    old, _ = score_article({"primary": "Travel", "region": "Japan",
                            "type": "Feature", "secondary": []}, 1.0)
    new, _ = score_article({"primary": "Travel", "region": "Japan",
                            "type": "Feature", "secondary": []}, 0.0)
    assert 0 < new - old <= 1.0

def test_reason_string_is_human_readable():
    _, reason = score_article({"primary": "Travel", "region": "Japan",
                               "type": "Feature", "secondary": []}, 0.1)
    assert "Travel" in reason and "Japan" in reason
    assert "+" not in reason and "score" not in reason.lower()


def test_declared_weights_exposes_the_four_tables():
    assert DECLARED_WEIGHTS["topic"] is TOPIC_WEIGHTS
    assert DECLARED_WEIGHTS["type"] is TYPE_WEIGHTS
    assert DECLARED_WEIGHTS["region"] is REGION_WEIGHTS
    # Secondary is the canonicalised view of SECONDARY_WEIGHTS, not the table itself:
    # labels are matched by canonical form, so a declared "Running Shoes" has to be
    # reachable as "running shoe" or it would never apply.
    assert DECLARED_WEIGHTS["secondary"] is SECONDARY_WEIGHTS_CANONICAL
    assert set(DECLARED_WEIGHTS["secondary"]) == {
        canonical_label(k) for k in SECONDARY_WEIGHTS}
    # Canonicalisation is the contract: a declared "Running Shoes" must be reachable
    # as "running shoe" or it would never match a tagged article.
    assert canonical_label("Running Shoes") == "running shoe"


def test_no_weights_argument_scores_exactly_as_before():
    old, _ = score_article(TAGS, 0.5)
    new = score_article_breakdown(TAGS, 0.5)
    assert new["total"] == pytest.approx(old)


def test_breakdown_rows_sum_to_the_total():
    bd = score_article_breakdown(TAGS, 0.5)
    assert sum(t["contribution"] for t in bd["terms"]) == pytest.approx(bd["total"])


def test_breakdown_includes_every_axis_even_when_unknown():
    bd = score_article_breakdown({}, 0.5)
    axes = [t["axis"] for t in bd["terms"] if t["kind"] == "axis"]
    assert axes.count("topic") == 1
    assert axes.count("type") == 1
    assert axes.count("region") == 1


def test_breakdown_always_includes_a_freshness_row():
    bd = score_article_breakdown(TAGS, 0.99)
    assert any(t["kind"] == "freshness" for t in bd["terms"])


def test_effective_weights_override_the_declared_table(monkeypatch):
    monkeypatch.setitem(TYPE_WEIGHTS, "News", 2)
    weights = {"type": {"News": {"effective": -2.0, "declared": 2.0,
                                 "votes": 5, "behavioural": 0.0}}}
    base = score_article_breakdown(TAGS, 0.5)
    voted = score_article_breakdown(TAGS, 0.5, weights=weights)
    assert voted["total"] == pytest.approx(base["total"] - 4.0)


def test_axis_row_reports_declared_and_vote_count():
    weights = {"topic": {"Consumer Tech": {"effective": 3.0, "declared": 2.0,
                                           "votes": 23, "behavioural": 0.4}}}
    bd = score_article_breakdown(TAGS, 0.5, weights=weights)
    row = next(t for t in bd["terms"] if t["kind"] == "axis" and t["axis"] == "topic")
    assert row["declared"] == 2.0
    assert row["votes"] == 23
    assert row["behavioural"] == pytest.approx(0.4)
    assert row["contribution"] == pytest.approx(3.0)


def test_disliked_article_takes_the_penalty():
    plain = score_article_breakdown(TAGS, 0.5)
    disliked = score_article_breakdown(TAGS, 0.5, vote="show_less")
    assert disliked["total"] == pytest.approx(plain["total"] - 4.0)
    assert any(t["kind"] == "vote_penalty" for t in disliked["terms"])


def test_liked_article_gets_no_bonus():
    # The card is marked read and gone; boosting it would park something the user has
    # finished with at the top of the feed for the three days reads are still returned.
    plain = score_article_breakdown(TAGS, 0.5)
    liked = score_article_breakdown(TAGS, 0.5, vote="show_more")
    assert liked["total"] == pytest.approx(plain["total"])
    assert not any(t["kind"] == "vote_penalty" for t in liked["terms"])


def test_secondary_subtotal_row_shows_the_multiplier_and_clamp():
    tags = dict(TAGS, secondary=["Running Shoes", "Card Games"])
    bd = score_article_breakdown(tags, 0.5)
    sub = next(t for t in bd["terms"] if t["kind"] == "secondary_subtotal")
    assert sub["multiplier"] == 0.3
    assert sum(t["contribution"] for t in bd["terms"]) == pytest.approx(bd["total"])


def test_score_article_still_returns_a_two_tuple():
    result = score_article(TAGS, 0.5)
    assert isinstance(result, tuple) and len(result) == 2


FIXED_NOW = datetime(2026, 8, 14, 12, 0, 0)


def test_exposure_count_zero_produces_no_term():
    # 1. count = 0 produces no exposure term at all
    bd = score_article_breakdown(TAGS, 0.5, exposure=(0, "2026-08-14 11:00:00"), now=FIXED_NOW)
    assert not any(t["kind"] == "exposure" for t in bd["terms"])
    assert exposure_penalty(0, "2026-08-14 11:00:00", now=FIXED_NOW) == 0.0


def test_exposure_penalty_increases_monotonically_with_count_and_bounded():
    # 2. Penalty magnitude increases monotonically with count and never exceeds EXPOSURE_K
    seen_at = (FIXED_NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    prev_mag = 0.0
    for count in range(1, 20):
        pen = exposure_penalty(count, seen_at, now=FIXED_NOW)
        assert pen < 0.0
        mag = abs(pen)
        assert mag > prev_mag
        assert mag <= EXPOSURE_K
        prev_mag = mag


def test_exposure_penalty_decreases_monotonically_with_hours_since_seen():
    # 3. Penalty magnitude decreases monotonically with hours_since_last_seen
    prev_mag = float("inf")
    for hours in [0, 1, 6, 24, 48, 96, 168]:
        seen_at = (FIXED_NOW - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        pen = exposure_penalty(3, seen_at, now=FIXED_NOW)
        mag = abs(pen)
        assert mag <= prev_mag
        prev_mag = mag


def test_exposure_penalty_saturation():
    # 4. Saturation: the magnitude delta from 5->8 visits is smaller than from 1->2
    seen_at = (FIXED_NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    p1 = abs(exposure_penalty(1, seen_at, now=FIXED_NOW))
    p2 = abs(exposure_penalty(2, seen_at, now=FIXED_NOW))
    p5 = abs(exposure_penalty(5, seen_at, now=FIXED_NOW))
    p8 = abs(exposure_penalty(8, seen_at, now=FIXED_NOW))
    delta_1_2 = p2 - p1
    delta_5_8 = p8 - p5
    assert delta_5_8 < delta_1_2


def test_exposure_penalty_future_timestamp_clamps():
    # 5. A last_seen_at in the future clamps rather than amplifying
    future_seen_at = (FIXED_NOW + timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
    now_seen_at = FIXED_NOW.strftime("%Y-%m-%d %H:%M:%S")
    p_future = exposure_penalty(3, future_seen_at, now=FIXED_NOW)
    p_now = exposure_penalty(3, now_seen_at, now=FIXED_NOW)
    assert p_future == pytest.approx(p_now)
    assert abs(p_future) <= EXPOSURE_K


def test_exposure_penalty_saved_article_gets_no_exposure_term():
    # 6. is_saved=True produces no exposure term
    seen_at = (FIXED_NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    bd = score_article_breakdown(TAGS, 0.5, exposure=(5, seen_at), is_saved=True, now=FIXED_NOW)
    assert not any(t["kind"] == "exposure" for t in bd["terms"])


def test_exposure_penalty_breakdown_sum_invariant():
    # 7. total == sum(t["contribution"] for t in terms) still holds with the new row present
    seen_at = (FIXED_NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    bd = score_article_breakdown(TAGS, 0.5, exposure=(3, seen_at), is_saved=False, now=FIXED_NOW)
    exp_term = next(t for t in bd["terms"] if t["kind"] == "exposure")
    assert exp_term["label"] == "Already shown"
    assert exp_term["contribution"] < 0.0
    assert bd["total"] == pytest.approx(sum(t["contribution"] for t in bd["terms"]))


def test_zero_weight_primary_topic_allows_secondary_vote_scoring(monkeypatch):
    # "unknown", "Health & Fitness", "Home & Garden" all have declared topic weight 0.
    # MIN_SECONDARY_CAP keeps the secondary subtotal from being clamped to zero there.
    monkeypatch.setitem(TOPIC_WEIGHTS, "Health & Fitness", 0)
    monkeypatch.setitem(SECONDARY_WEIGHTS_CANONICAL, canonical_label("GTA"), 2)
    monkeypatch.setitem(SECONDARY_WEIGHTS_CANONICAL, canonical_label("Running Shoes"), 2)
    tags_unknown = {"primary": "unknown", "region": "unknown", "type": "unknown", "secondary": ["GTA"]}
    bd_unknown = score_article_breakdown(tags_unknown, 0.5)
    sub_unknown = next(t for t in bd_unknown["terms"] if t["kind"] == "secondary_subtotal")
    assert sub_unknown["raw"] == 2.0
    assert sub_unknown["contribution"] == pytest.approx(0.6)
    assert sub_unknown["clamped"] is False

    tags_hf = {"primary": "Health & Fitness", "region": "unknown", "type": "unknown", "secondary": ["Running Shoes"]}
    bd_hf = score_article_breakdown(tags_hf, 0.5)
    sub_hf = next(t for t in bd_hf["terms"] if t["kind"] == "secondary_subtotal")
    assert sub_hf["contribution"] == pytest.approx(0.6)
    assert sub_hf["clamped"] is False
