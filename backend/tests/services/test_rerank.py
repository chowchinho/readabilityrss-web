import pytest
from app.services.rerank import calibrated_rerank, promote_exploration_slots

def test_greedy_rerank_promotes_feed_diversity():
    # 40 candidates where 30 come from feed 1, 4 from feed 2, 3 from feed 3, 3 from feed 4
    candidates = []
    cid = 1
    for _ in range(30):
        candidates.append({"id": cid, "source_id": 1, "score": 1.0})
        cid += 1
    for _ in range(4):
        candidates.append({"id": cid, "source_id": 2, "score": 1.0})
        cid += 1
    for _ in range(3):
        candidates.append({"id": cid, "source_id": 3, "score": 1.0})
        cid += 1
    for _ in range(3):
        candidates.append({"id": cid, "source_id": 4, "score": 1.0})
        cid += 1

    out = calibrated_rerank(candidates, lambda_=0.3)

    assert len(out) == len(candidates)
    assert set(x["id"] for x in out) == set(x["id"] for x in candidates)

    top_10 = out[:10]
    top_10_feeds = {x["source_id"] for x in top_10}
    assert len(top_10_feeds) >= 4, f"Expected >= 4 distinct feeds in top 10, got {len(top_10_feeds)}"

def test_calibrated_rerank_is_permutation():
    candidates = [
        {"id": i, "source_id": i % 3, "score": float(10 - i)}
        for i in range(15)
    ]
    out = calibrated_rerank(candidates)
    assert len(out) == len(candidates)
    assert set(x["id"] for x in out) == set(x["id"] for x in candidates)

def test_empty_and_single_candidate():
    assert calibrated_rerank([]) == []
    single = [{"id": 100, "source_id": 1, "score": 2.5}]
    assert calibrated_rerank(single) == single


def _c(cid, source_id, topic, art_type, score):
    return {"id": cid, "source_id": source_id, "score": score,
            "topics": {"primary": topic, "type": art_type}}


def _penalised_page(penalty):
    """A page where score order and diversity genuinely conflict, plus one penalised
    article. The top ten scorers share a feed, topic and type, so the re-ranker has to
    trade score against spread - which is the only condition under which the
    normalisation range matters at all.
    """
    candidates = [_c(i, 0, "Crowded", "Same", 9.0 - i * 0.1) for i in range(10)]
    candidates += [_c(i, i, f"Spread{i}", f"Kind{i % 3}", 2.0 + (i - 10) * 0.1)
                   for i in range(10, 20)]
    candidates.append(_c(99, 50, "Victim", "Same", 5.0 + penalty))
    return candidates


_PENALTY_WEIGHTS = {
    "topic": {label: {"effective": 1.0} for label in
              ["Crowded", "Victim"] + [f"Spread{i}" for i in range(10, 20)]},
    "type": {"Same": {"effective": 1.0},
             **{f"Kind{i}": {"effective": 1.0} for i in range(3)}},
}


def _top5_mean_excluding_victim(penalty):
    out = [c for c in calibrated_rerank(_penalised_page(penalty), lambda_=0.3,
                                        label_weights=_PENALTY_WEIGHTS)
           if c["id"] != 99]
    return sum(c["score"] for c in out[:5]) / 5


def test_article_penalty_stays_inside_the_normalisation_range():
    """A dislike must not reorder the rest of the page. Guard for spec 3.3.

    calibrated_rerank min/max normalises scores across the candidate set, so one extreme
    outlier stretches the range and compresses every other article into a narrow band
    while the KL term keeps its full size - at which point diversity, not score, decides
    the page. Measured top-5 mean score of the *other* articles, by penalty:

        0.0 -> 8.80    -2.0 -> 8.80    -4.0 -> 6.48    -20.0 -> 4.00    -100.0 -> 4.00

    So the default -4.0 costs some ordering quality but the page still leads with its
    best articles; -100 collapses it to near the floor. Note -2.0 is the largest fully
    neutral value - worth knowing before raising vote_article_penalty, which is editable
    from system_settings without a redeploy.
    """
    assert _top5_mean_excluding_victim(-4.0) >= 6.0, (
        "the default article penalty is distorting the rest of the page"
    )
    assert _top5_mean_excluding_victim(-100.0) <= 4.5, (
        "an extreme penalty should visibly collapse the ordering - if this no longer "
        "holds, the normalisation trap this test guards has changed shape"
    )
    assert _top5_mean_excluding_victim(-4.0) > _top5_mean_excluding_victim(-100.0)


def test_passing_label_weights_none_is_the_same_as_omitting_it():
    candidates = [_c(i, i % 3, "Travel", "News", float(10 - i)) for i in range(15)]
    baseline = calibrated_rerank([dict(c) for c in candidates], lambda_=0.3)
    with_arg = calibrated_rerank([dict(c) for c in candidates], lambda_=0.3,
                                 label_weights=None)
    assert [c["id"] for c in baseline] == [c["id"] for c in with_arg]


# The two tests below are also the lambda_ calibration guard. lambda_ = 0.3 is tuned
# against KL *summed* over feed, topic and type; averaging the three instead cuts the
# penalty to a third and both fail - the dominant label reclaims the whole page and the
# demoted article falls from position 4 to 49. Re-run them after any change to lambda_,
# to the KL aggregation, or to the number of dimensions.


def test_one_label_cannot_own_the_page():
    # 40 high-scoring Deals from 40 different feeds, 10 low-scoring others.
    candidates = [_c(i, i, "Consumer Tech", "Deal", 9.0) for i in range(40)]
    candidates += [_c(100 + i, 100 + i, "Travel", "Feature", 1.0) for i in range(10)]
    weights = {"type": {"Deal": {"effective": 3.0}, "Feature": {"effective": 2.0}},
               "topic": {"Consumer Tech": {"effective": 3.0},
                         "Travel": {"effective": 2.0}}}

    out = calibrated_rerank(candidates, lambda_=0.3, label_weights=weights, floor=0.02)

    top_20_types = [c["topics"]["type"] for c in out[:20]]
    assert top_20_types.count("Deal") < 20, "a single type took the whole page"


def test_a_demoted_label_still_surfaces():
    # 49 strong Travel pieces and one buried Politics piece.
    candidates = [_c(i, i, "Travel", "Feature", 8.0) for i in range(49)]
    candidates.append(_c(999, 999, "Politics", "News", -4.0))
    weights = {"topic": {"Travel": {"effective": 3.0},
                         "Politics": {"effective": -3.0}},
               "type": {"Feature": {"effective": 2.0}, "News": {"effective": 2.0}}}

    out = calibrated_rerank(candidates, lambda_=0.3, label_weights=weights, floor=0.02)

    positions = [i for i, c in enumerate(out) if c["id"] == 999]
    assert positions, "the demoted article vanished"
    assert positions[0] < 25, (
        f"demoted article landed at {positions[0]}; the floor should surface it "
        "in the part of the page a reader actually reaches"
    )


def test_still_a_permutation_with_label_weights():
    candidates = [_c(i, i % 4, "Travel" if i % 2 else "Gaming",
                     "News", float(i)) for i in range(20)]
    weights = {"topic": {"Travel": {"effective": 2.0}, "Gaming": {"effective": 1.0}}}
    out = calibrated_rerank(candidates, lambda_=0.3, label_weights=weights)
    assert len(out) == len(candidates)
    assert set(c["id"] for c in out) == set(c["id"] for c in candidates)


def test_missing_topics_key_does_not_crash():
    candidates = [{"id": i, "source_id": i, "score": 1.0} for i in range(5)]
    out = calibrated_rerank(candidates, label_weights={"topic": {}})
    assert len(out) == 5


def test_exploration_slots_is_permutation():
    # 8. Output is a permutation — same length, same object identities.
    candidates = [_c(i, i % 3, "Travel", "News", float(25 - i)) for i in range(25)]
    exposure = {i: (1, "2026-08-14 10:00:00") for i in range(12)}  # top half seen, lower half unseen
    out = promote_exploration_slots(candidates, every=10, exposure=exposure)
    assert len(out) == len(candidates)
    assert set(id(c) for c in out) == set(id(c) for c in candidates)


def test_exploration_slot_promotes_unseen_tail_article_to_index_9():
    # 9. A zero-impression tail article lands at index 9 when one is available.
    candidates = [_c(i, i, "Travel", "News", float(20 - i)) for i in range(20)]
    # All seen except candidate 15 in the tail
    exposure = {i: (2, "2026-08-14 10:00:00") for i in range(20) if i != 15}
    out = promote_exploration_slots(candidates, every=10, exposure=exposure)
    assert out[9]["id"] == 15


def test_exploration_slot_empty_pool_leaves_order_untouched():
    # 10. Empty pool leaves the order untouched.
    candidates = [_c(i, i, "Travel", "News", float(20 - i)) for i in range(20)]
    # All articles seen
    exposure = {i: (1, "2026-08-14 10:00:00") for i in range(20)}
    out = promote_exploration_slots(candidates, every=10, exposure=exposure)
    assert [c["id"] for c in out] == [c["id"] for c in candidates]


def test_exploration_slot_never_promotes_show_less():
    # 11. A show_less article is never promoted.
    candidates = [_c(i, i, "Travel", "News", float(20 - i)) for i in range(20)]
    # Only candidate 15 is unseen, but it was voted show_less
    candidates[15]["vote"] = "show_less"
    exposure = {i: (2, "2026-08-14 10:00:00") for i in range(20) if i != 15}
    out = promote_exploration_slots(candidates, every=10, exposure=exposure)
    assert out[9]["id"] != 15
    assert [c["id"] for c in out] == [c["id"] for c in candidates]


def test_exploration_slot_every_zero_disables_pass():
    # 12. every=0 disables the pass.
    candidates = [_c(i, i, "Travel", "News", float(20 - i)) for i in range(20)]
    exposure = {i: (2, "2026-08-14 10:00:00") for i in range(20) if i != 15}
    out = promote_exploration_slots(candidates, every=0, exposure=exposure)
    assert [c["id"] for c in out] == [c["id"] for c in candidates]


def test_exploration_slot_short_page_returned_unchanged():
    # 13. A page shorter than every is returned unchanged.
    candidates = [_c(i, i, "Travel", "News", float(5 - i)) for i in range(5)]
    out = promote_exploration_slots(candidates, every=10, exposure={})
    assert [c["id"] for c in out] == [c["id"] for c in candidates]


def test_exploration_slots_spread_across_feeds():
    """14. One prolific feed must not take every slot.

    This pass runs after calibrated_rerank, so picking purely by score let a single
    feed with a backlog of unseen articles win slot after slot — on live data, 20
    slots held 8 distinct feeds and one feed took 7.
    """
    # Feed 1 owns the whole tail by score; feeds 2-5 each have one lower-scoring entry.
    candidates = [_c(i, 0, "Travel", "News", 100.0 - i) for i in range(50)]
    for i in range(50, 90):
        candidates.append(_c(i, 1, "Travel", "News", 50.0 - (i - 50) * 0.01))
    for feed, i in enumerate(range(90, 94), start=2):
        candidates.append(_c(i, feed, "Travel", "News", 10.0))

    exposure = {i: (3, "2026-08-14 10:00:00") for i in range(50)}
    out = promote_exploration_slots(candidates, every=10, exposure=exposure)

    slot_feeds = [out[i]["source_id"] for i in range(9, len(out), 10)]

    # The guarantee: no feed takes a second slot until every eligible feed has had one.
    # Feeds 2-5 hold a single article each, so after four slots only feed 1 is left and
    # falling back to it is correct.
    eligible_feeds = {1, 2, 3, 4, 5}
    prefix = slot_feeds[:len(eligible_feeds)]
    assert len(set(prefix)) == len(prefix), f"a feed repeated too early: {slot_feeds}"
    assert set(slot_feeds) >= eligible_feeds, f"a feed never got a slot: {slot_feeds}"


def test_exploration_slots_fall_back_to_score_when_feeds_exhausted():
    # With only one eligible feed available, the pass must still fill slots.
    candidates = [_c(i, 0, "Travel", "News", 100.0 - i) for i in range(20)]
    candidates += [_c(i, 1, "Travel", "News", 5.0) for i in range(20, 40)]
    exposure = {i: (3, "2026-08-14 10:00:00") for i in range(20)}
    out = promote_exploration_slots(candidates, every=10, exposure=exposure)
    assert set(c["id"] for c in out) == set(c["id"] for c in candidates)
    assert out[9]["source_id"] == 1
