import math
import pytest
from app.services.vote_weights import (
    VOTE_DEFAULTS, decay_factor, saturate, resolve_settings,
)


def test_fresh_signal_counts_fully():
    assert decay_factor(0, 90) == pytest.approx(1.0)


def test_one_half_life_counts_half():
    assert decay_factor(90, 90) == pytest.approx(0.5)


def test_decay_never_goes_negative_for_future_dates():
    # Clock skew between devices can produce a negative age; it must not amplify.
    assert decay_factor(-5, 90) == pytest.approx(1.0)


def test_saturation_curve_matches_spec_table():
    # Spec section 3.5, K=17, cap 4.0
    expected = {1: 0.24, 3: 0.70, 5: 1.14, 10: 2.11, 20: 3.31}
    for votes, adj in expected.items():
        assert saturate(votes, 4.0, 17) == pytest.approx(adj, abs=0.01)


def test_saturation_never_exceeds_cap():
    assert saturate(1000, 4.0, 17) <= 4.0
    assert saturate(1000, 4.0, 17) == pytest.approx(4.0, abs=0.001)


def test_saturation_is_symmetric():
    assert saturate(-10, 4.0, 17) == pytest.approx(-saturate(10, 4.0, 17))


def test_zero_tally_is_zero():
    assert saturate(0, 4.0, 17) == 0.0


def test_secondary_curve_saturates_faster():
    # Same tally must move a secondary label proportionally further along its curve
    assert saturate(5, 1.2, 6) / 1.2 > saturate(5, 4.0, 17) / 4.0


def test_resolve_settings_coerces_text_overrides():
    # system_settings stores TEXT, so a hand-edited override arrives as a string.
    resolved = resolve_settings({"vote_curve_k": "20", "vote_half_life_days": "45"})
    assert resolved["vote_curve_k"] == 20.0
    assert resolved["vote_half_life_days"] == 45.0
    assert resolved["vote_explicit_cap"] == 4.0


def test_resolve_settings_ignores_junk_and_unknown_keys():
    resolved = resolve_settings({"vote_curve_k": "banana", "max_articles_per_feed": 50})
    assert resolved["vote_curve_k"] == 17
    assert "max_articles_per_feed" not in resolved


def test_resolve_settings_handles_none():
    assert resolve_settings(None) == VOTE_DEFAULTS


def test_defaults_match_spec():
    assert VOTE_DEFAULTS["vote_explicit_cap"] == 4.0
    assert VOTE_DEFAULTS["vote_behavioural_cap"] == 1.0
    assert VOTE_DEFAULTS["vote_curve_k"] == 17
    assert VOTE_DEFAULTS["vote_curve_k_secondary"] == 6
    assert VOTE_DEFAULTS["vote_half_life_days"] == 90
    assert VOTE_DEFAULTS["vote_effective_clamp"] == 5.5
    assert VOTE_DEFAULTS["vote_article_penalty"] == -4.0
from app.services.vote_weights import (
    VOTE_DEFAULTS, decay_factor, saturate, resolve_settings, signal_value,
)

S = dict(VOTE_DEFAULTS)


def test_open_is_positive():
    assert signal_value({"event_type": "open", "dwell_seconds": 30}, S) == 1.0


def test_hover_is_positive():
    assert signal_value({"event_type": "hover", "dwell_seconds": 5}, S) == 0.6


def test_deliberate_skip_is_negative():
    assert signal_value({"event_type": "skip", "dwell_seconds": 3}, S) == -0.4


def test_arrow_key_traversal_contributes_nothing():
    # Holding the arrow key fires one skip per article stepped past, in well under
    # a second each. Twelve of those is navigation, not twelve opinions.
    for _ in range(12):
        assert signal_value({"event_type": "skip", "dwell_seconds": 0}, S) == 0.0


def test_skip_exactly_at_the_dwell_floor_counts():
    assert signal_value({"event_type": "skip", "dwell_seconds": 1}, S) == -0.4


def test_skip_with_missing_dwell_contributes_nothing():
    assert signal_value({"event_type": "skip"}, S) == 0.0


def test_read_without_vote_is_weakly_negative():
    assert signal_value({"event_type": "read_no_vote"}, S) == -0.1


def test_impressions_are_not_a_weight_signal():
    assert signal_value({"event_type": "impression"}, S) == 0.0


from datetime import datetime, timedelta
from app.services.vote_weights import (
    VOTE_DEFAULTS, decay_factor, saturate, resolve_settings, signal_value,
    compute_label_weights,
)

NOW = datetime(2026, 8, 12, 12, 0, 0)
DECLARED = {
    "topic": {"Consumer Tech": 2, "Politics": -1},
    "type": {"News": 2, "Deal": -2},
    "region": {"Global": 1, "Japan": 2},
    "secondary": {"Samsung": 0},
}


def _vote(article_id, vote, days_ago=0, topic="Consumer Tech",
          art_type="News", region="Global", secondary=None):
    return {
        "article_id": article_id,
        "vote": vote,
        "primary_topic": topic,
        "article_type": art_type,
        "region": region,
        "secondary_topics": secondary if secondary is not None else [],
        "updated_at": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S"),
    }


def test_no_votes_leaves_declared_weights_untouched():
    out = compute_label_weights([], [], DECLARED, S, NOW)
    assert out["topic"]["Consumer Tech"]["effective"] == 2
    assert out["topic"]["Consumer Tech"]["explicit"] == 0.0
    assert out["type"]["Deal"]["effective"] == -2


def test_ten_upvotes_carry_deal_from_penalised_to_neutral():
    votes = [_vote(i, "show_more", art_type="Deal") for i in range(10)]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    assert out["type"]["Deal"]["explicit"] == pytest.approx(2.11, abs=0.01)
    assert out["type"]["Deal"]["effective"] == pytest.approx(0.11, abs=0.01)
    assert out["type"]["Deal"]["votes"] == 10


def test_twenty_upvotes_make_deal_clearly_positive():
    votes = [_vote(i, "show_more", art_type="Deal") for i in range(20)]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    assert out["type"]["Deal"]["effective"] == pytest.approx(1.31, abs=0.01)


def test_effective_weight_is_clamped():
    # declared 2.0 + a saturated explicit channel 4.0 = 6.0, above the 5.5 ceiling.
    votes = [_vote(i, "show_more") for i in range(500)]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    assert out["topic"]["Consumer Tech"]["effective"] == 5.5


def test_mixed_votes_net_out_and_leave_declared_standing():
    votes = [_vote(1, "show_more"), _vote(2, "show_less")]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    assert out["topic"]["Consumer Tech"]["explicit"] == pytest.approx(0.0)
    assert out["topic"]["Consumer Tech"]["effective"] == 2


def test_the_distinction_migrates_to_the_axis_that_separates_them():
    # Same topic on both, but a liked Japanese piece and a disliked global one.
    votes = [
        _vote(1, "show_more", region="Japan"),
        _vote(2, "show_less", region="Global"),
    ]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    assert out["topic"]["Consumer Tech"]["explicit"] == pytest.approx(0.0)
    assert out["region"]["Japan"]["explicit"] > 0
    assert out["region"]["Global"]["explicit"] < 0


def test_contested_flags_many_votes_netting_near_zero():
    votes = [_vote(i, "show_more") for i in range(10)]
    votes += [_vote(100 + i, "show_less") for i in range(10)]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    assert out["topic"]["Consumer Tech"]["contested"] is True


def test_a_handful_of_cancelling_votes_is_not_contested():
    votes = [_vote(1, "show_more"), _vote(2, "show_less")]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    assert out["topic"]["Consumer Tech"]["contested"] is False


def test_old_votes_count_half():
    fresh = compute_label_weights(
        [_vote(1, "show_more")], [], DECLARED, S, NOW)
    stale = compute_label_weights(
        [_vote(1, "show_more", days_ago=90)], [], DECLARED, S, NOW)
    assert stale["topic"]["Consumer Tech"]["explicit"] == pytest.approx(
        fresh["topic"]["Consumer Tech"]["explicit"] * 0.5, abs=0.02)


def test_secondary_labels_are_tallied_and_capped():
    votes = [_vote(i, "show_more", secondary=["Samsung"]) for i in range(500)]
    out = compute_label_weights(votes, [], DECLARED, S, NOW)
    # Secondary labels are keyed by canonical form so that spelling variants of one
    # label share their votes; see test_labels.py.
    assert out["secondary"]["samsung"]["explicit"] == pytest.approx(1.2, abs=0.001)


def test_behavioural_channel_is_reported_separately_and_capped():
    signals = [
        {"event_type": "open", "dwell_seconds": 30, "primary_topic": "Consumer Tech",
         "article_type": "News", "region": "Global", "secondary_topics": None,
         "created_at": NOW.strftime("%Y-%m-%d %H:%M:%S")}
        for _ in range(500)
    ]
    out = compute_label_weights([], signals, DECLARED, S, NOW)
    assert out["topic"]["Consumer Tech"]["explicit"] == 0.0
    assert out["topic"]["Consumer Tech"]["behavioural"] == pytest.approx(1.0, abs=0.001)


def test_browsing_cannot_outweigh_one_downvote():
    # 500 opens against a single thumbs-down: the vote must still win.
    signals = [
        {"event_type": "open", "dwell_seconds": 30, "primary_topic": "Politics",
         "article_type": "News", "region": "Global", "secondary_topics": None,
         "created_at": NOW.strftime("%Y-%m-%d %H:%M:%S")}
        for _ in range(500)
    ]
    votes = [_vote(i, "show_less", topic="Politics") for i in range(20)]
    out = compute_label_weights(votes, signals, DECLARED, S, NOW)
    assert out["topic"]["Politics"]["behavioural"] == pytest.approx(1.0, abs=0.001)
    assert out["topic"]["Politics"]["explicit"] == pytest.approx(-3.31, abs=0.01)
    assert out["topic"]["Politics"]["effective"] < -1.0


def test_untagged_votes_contribute_nothing():
    vote = _vote(1, "show_more", topic=None, art_type=None, region=None)
    out = compute_label_weights([vote], [], DECLARED, S, NOW)
    assert None not in out["topic"]
    assert out["topic"]["Consumer Tech"]["explicit"] == 0.0


# --- secondary label canonicalisation and the co-occurrence prior -------------------

from datetime import datetime

from app.services.vote_weights import compute_label_weights, _secondary_priors

PRIOR_NOW = datetime(2026, 8, 13)
PRIOR_DECLARED = {"topic": {"Anime & Manga": 2, "Politics": -1}, "type": {}, "region": {},
            "secondary": {}}


def _prior_vote(article_id, vote, topic, secondary):
    return {"article_id": article_id, "vote": vote, "primary_topic": topic,
            "secondary_topics": secondary, "region": "Japan",
            "article_type": "News", "updated_at": "2026-08-13 00:00:00"}


def test_two_spellings_of_one_secondary_label_share_their_votes():
    votes = [_prior_vote(1, "show_more", "Anime & Manga", '["Figures"]'),
             _prior_vote(2, "show_more", "Anime & Manga", '["Figure"]')]
    result = compute_label_weights(votes, [], PRIOR_DECLARED, {}, PRIOR_NOW)
    assert list(result["secondary"]) == ["figure"]
    assert result["secondary"]["figure"]["votes"] == 2


def test_one_article_tagged_with_both_spellings_counts_once():
    votes = [_prior_vote(1, "show_more", "Anime & Manga", '["Figures", "Figure"]')]
    result = compute_label_weights(votes, [], PRIOR_DECLARED, {}, PRIOR_NOW)
    assert result["secondary"]["figure"]["votes"] == 1


def test_unvoted_secondary_label_inherits_from_the_topics_it_appears_under():
    # "gundam" has no votes; it appears only on Anime & Manga articles, which the user
    # has voted up. Without the prior it would score zero and could not rank anything.
    votes = [_prior_vote(i, "show_more", "Anime & Manga", '["Model Kits"]') for i in range(6)]
    cooc = {"gundam": {"Anime & Manga": 20}}
    result = compute_label_weights(votes, [], PRIOR_DECLARED, {}, PRIOR_NOW, cooccurrence=cooc)
    topic = result["topic"]["Anime & Manga"]["effective"]
    assert topic > 2.0
    gundam = result["secondary"]["gundam"]
    assert gundam["votes"] == 0
    assert gundam["prior"] == pytest.approx(0.35 * topic, abs=0.01)
    assert gundam["effective"] == pytest.approx(gundam["prior"], abs=0.01)


def test_the_prior_carries_the_sign_of_a_disliked_topic():
    votes = [_prior_vote(i, "show_less", "Politics", '["Elections"]') for i in range(6)]
    declared = dict(PRIOR_DECLARED, topic={"Politics": -1})
    cooc = {"filibuster": {"Politics": 10}}
    result = compute_label_weights(votes, [], declared, {}, PRIOR_NOW, cooccurrence=cooc)
    assert result["secondary"]["filibuster"]["prior"] < 0


def test_a_mixed_context_label_gets_a_blended_prior():
    votes = [_prior_vote(i, "show_more", "Anime & Manga", '["x"]') for i in range(6)]
    declared = dict(PRIOR_DECLARED, topic={"Anime & Manga": 2, "Politics": -1})
    liked = compute_label_weights(votes, [], declared, {}, PRIOR_NOW,
                                  cooccurrence={"a": {"Anime & Manga": 10}})
    mixed = compute_label_weights(votes, [], declared, {}, PRIOR_NOW,
                                  cooccurrence={"a": {"Anime & Manga": 5, "Politics": 5}})
    assert mixed["secondary"]["a"]["prior"] < liked["secondary"]["a"]["prior"]


def test_the_prior_fades_as_a_label_earns_its_own_votes():
    # Both cases carry the same 30 topic votes, so the topic weight the prior is drawn
    # from is identical and only the label's own vote count varies. An earlier version
    # varied both at once and measured their product, not the fade.
    cooc = {"model kit": {"Anime & Manga": 30}}

    def weights(labelled):
        votes = [
            _prior_vote(i, "show_more", "Anime & Manga",
                        '["Model Kits"]' if i < labelled else '["Something Else"]')
            for i in range(30)
        ]
        return compute_label_weights(votes, [], PRIOR_DECLARED, {}, PRIOR_NOW,
                                     cooccurrence=cooc)

    few, many = weights(1), weights(30)
    assert few["topic"]["Anime & Manga"]["effective"] \
        == many["topic"]["Anime & Manga"]["effective"]

    few_prior = few["secondary"]["model kit"]["prior"]
    many_prior = many["secondary"]["model kit"]["prior"]
    assert few_prior > many_prior
    # k/(n+k) with k=3: 0.75 at one vote, 0.09 at thirty.
    assert many_prior == pytest.approx(few_prior * (3 / 33) / 0.75, rel=0.01)
    # With plenty of its own evidence the label is judged on that evidence.
    assert many["secondary"]["model kit"]["explicit"] > many_prior * 2


def test_omitting_cooccurrence_reproduces_the_old_behaviour():
    votes = [_prior_vote(1, "show_more", "Anime & Manga", '["Model Kits"]')]
    result = compute_label_weights(votes, [], PRIOR_DECLARED, {}, PRIOR_NOW)
    assert result["secondary"]["model kit"]["prior"] == 0.0


def test_prior_factor_zero_disables_inheritance():
    votes = [_prior_vote(i, "show_more", "Anime & Manga", '["x"]') for i in range(6)]
    result = compute_label_weights(votes, [], PRIOR_DECLARED, {"secondary_prior_factor": 0},
                                   PRIOR_NOW, cooccurrence={"gundam": {"Anime & Manga": 20}})
    assert result["secondary"]["gundam"]["prior"] == 0.0


def test_priors_ignore_topics_with_no_weight_entry():
    # A label seen only under a topic the weight table has never heard of.
    priors = _secondary_priors({"mystery": {"Nonexistent Topic": 5}}, {}, 0.35)
    assert priors == {}


def test_effective_weight_still_respects_the_clamp_with_a_prior():
    votes = [_prior_vote(i, "show_more", "Anime & Manga", '["Collectibles"]') for i in range(200)]
    cooc = {"collectible": {"Anime & Manga": 50}}
    result = compute_label_weights(votes, [], PRIOR_DECLARED, {}, PRIOR_NOW, cooccurrence=cooc)
    clamp = VOTE_DEFAULTS["vote_effective_clamp"]
    assert abs(result["secondary"]["collectible"]["effective"]) <= clamp


def test_a_much_voted_label_outranks_a_barely_voted_one_on_the_same_axis():
    """The regression the 5.5 clamp fixes.

    At 3.0 this failed: declared 2.0 plus any real vote tally exceeded the ceiling, so
    a topic with sixty votes and one with a single vote both reported exactly +3.00 and
    the ranker could not tell them apart. Live data had eight topics pinned that way.
    """
    declared = {"topic": {"Japan Heavy": 2, "Barely Seen": 2}, "type": {}, "region": {},
                "secondary": {}}
    votes = [_vote(i, "show_more", topic="Japan Heavy") for i in range(60)]
    votes.append(_vote(999, "show_more", topic="Barely Seen"))
    out = compute_label_weights(votes, [], declared, S, NOW)
    heavy = out["topic"]["Japan Heavy"]["effective"]
    barely = out["topic"]["Barely Seen"]["effective"]
    assert heavy > barely, f"{heavy} should beat {barely}"
    assert heavy - barely > 1.0
