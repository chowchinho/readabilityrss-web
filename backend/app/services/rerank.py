"""Calibrated greedy re-ranking (Steck, RecSys '18) to balance ranking scores with
feed and label diversity."""
import math
from typing import Any

DEFAULT_FLOOR = 0.02


def _target_distribution(values: list[Any], weights: dict | None,
                         floor: float) -> dict[Any, float]:
    """Target share per distinct value.

    Uniform when no weights are supplied, so the two-argument call still balances
    feeds alone. Not bit-identical to the version before votes existed: `eps` in
    _kl_penalty went from 1e-9 to a real Laplace pseudo-count, because at 1e-9 an
    unseen category produced a near-infinite penalty and swamped the score term.
    With weights, share rises with the effective
    weight but never falls below `floor`: a reader cannot vote on what they are never
    shown, so a label with no floor would be demoted permanently and irreversibly.
    """
    distinct = list(set(values))
    if not distinct:
        return {}
    if not weights:
        return {v: 1.0 / len(distinct) for v in distinct}

    raw = {}
    for value in distinct:
        entry = weights.get(value) or {}
        effective = float(entry.get("effective", 0.0))
        # exp keeps every share strictly positive and makes the mapping monotone in
        # the weight without needing to know its range.
        raw[value] = math.exp(effective)

    total = sum(raw.values()) or 1.0
    shares = {v: raw[v] / total for v in distinct}

    # Apply the floor, then renormalise what is left above it.
    floored = {v: max(floor, s) for v, s in shares.items()}
    excess = sum(floored.values())
    return {v: s / excess for v, s in floored.items()}


def _kl_terms(counts: dict, target: dict, k: int) -> tuple[float, dict]:
    """KL at this step, plus how much selecting each category would move it.

    Returns (base, deltas) where the penalty for picking a candidate in category c is
    `base + deltas[c]`.

    Selecting a candidate only shifts its own category's term; every other term is
    identical for every candidate under consideration at this step. Evaluating the full
    sum per candidate instead cost one logarithm per category per candidate, which at a
    200-article page over 103 feeds, 33 topics and 11 types was ~5.9M logarithms and
    just over a second of blocked event loop on the Pi. This is the same arithmetic,
    hoisted: the per-candidate work drops to one dict lookup per dimension.
    """
    eps = 0.01
    denom = k + eps * max(1, len(target))
    base = 0.0
    deltas = {}
    for cat, qk in target.items():
        cnt = counts.get(cat, 0)
        pk = (cnt + eps) / denom
        if qk > 0 and pk > 0:
            base += qk * math.log(qk / pk)
            # qk*log(qk/p_after) - qk*log(qk/p_before), reduced.
            deltas[cat] = qk * math.log((cnt + eps) / (cnt + 1 + eps))
        else:
            deltas[cat] = 0.0
    return base, deltas


def calibrated_rerank(candidates: list[dict[str, Any]], lambda_: float = 0.3,
                      label_weights: dict | None = None,
                      floor: float = DEFAULT_FLOOR) -> list[dict[str, Any]]:
    """Re-rank to balance score against distribution alignment over feed source,
    primary topic and article type. Returns a permutation of candidates."""
    if not candidates or len(candidates) <= 1:
        return list(candidates)

    scores = [float(c.get("score") or 0.0) for c in candidates]
    min_score, max_score = min(scores), max(scores)
    score_range = max_score - min_score
    if score_range > 1e-9:
        norm_scores = [(s - min_score) / score_range for s in scores]
    else:
        norm_scores = [1.0 for _ in scores]

    def feed_of(c):
        if c.get("source_id") is not None:
            return c["source_id"]
        return c.get("feed_name") or c.get("feed") or "unknown"

    dimensions = [("feed", [feed_of(c) for c in candidates], None)]
    if label_weights:
        dimensions.append((
            "topic",
            [(c.get("topics") or {}).get("primary") or "unknown" for c in candidates],
            label_weights.get("topic"),
        ))
        dimensions.append((
            "type",
            [(c.get("topics") or {}).get("type") or "unknown" for c in candidates],
            label_weights.get("type"),
        ))

    targets = {name: _target_distribution(values, weights, floor)
               for name, values, weights in dimensions}
    counts = {name: {} for name, _, _ in dimensions}
    values_by_dim = {name: values for name, values, _ in dimensions}

    remaining = list(range(len(candidates)))
    selected = []

    while remaining:
        best_idx = None
        best_combined = -float("inf")
        k = len(selected) + 1

        # Hoisted out of the candidate loop - see _kl_terms.
        step_terms = [(values_by_dim[name],) + _kl_terms(counts[name], targets[name], k)
                      for name in targets]

        for idx in remaining:
            kl = 0.0
            for values, base, deltas in step_terms:
                kl += base + deltas.get(values[idx], 0.0)
            # Summed, not averaged, and lambda_ is calibrated against the sum. Averaging
            # makes lambda_ portable across dimension counts but cuts the penalty to a
            # third, and at that pressure the floor stops working: a demoted label falls
            # back to the end of the page instead of surfacing where it can be voted on.
            # Matching pressure needs lambda_ ~= 0.6, which sits on a cliff - between 0.5
            # and 0.6 a demoted article jumps from position 49 to 4, and by 0.7 to 1.
            # If a dimension is ever added or removed, re-check that lambda_ still holds.
            combined = (1.0 - lambda_) * norm_scores[idx] - lambda_ * kl
            if combined > best_combined:
                best_combined = combined
                best_idx = idx

        if best_idx is None:
            best_idx = remaining[0]

        selected.append(best_idx)
        remaining.remove(best_idx)
        for name in counts:
            key = values_by_dim[name][best_idx]
            counts[name][key] = counts[name].get(key, 0) + 1

    result = [candidates[i] for i in selected]

    assert len(result) == len(candidates), "calibrated_rerank must return same number of items"
    assert set(id(c) for c in result) == set(id(c) for c in candidates), \
        "calibrated_rerank must return exact same items"
    return result


def _exposure_count(c: dict, exposure: dict | None) -> int:
    if not exposure:
        return 0
    cid = c.get("id")
    exp = exposure.get(cid)
    if exp is None:
        return 0
    if isinstance(exp, (tuple, list)):
        return int(exp[0])
    if isinstance(exp, (int, float)):
        return int(exp)
    return 0


def promote_exploration_slots(candidates: list[dict[str, Any]], every: int = 10,
                              exposure: dict[int, tuple[int, str]] | None = None,
                              label_weights: dict | None = None) -> list[dict[str, Any]]:
    """Promote unseen, high-scoring tail articles into exploration slots at regular intervals.

    Returns a permutation of candidates.
    """
    if not candidates or every <= 0 or len(candidates) <= every:
        return list(candidates)

    n = len(candidates)
    half_idx = n // 2
    lower_half = candidates[half_idx:]

    def _is_eligible(c: dict) -> bool:
        if c.get("vote") == "show_less":
            return False
        return _exposure_count(c, exposure) == 0

    pool = [c for c in lower_half if _is_eligible(c)]
    if not pool:
        return list(candidates)

    def _pool_sort_key(c: dict):
        score = float(c.get("score") or 0.0)
        primary = (c.get("topics") or {}).get("primary") or "unknown"
        topic_votes = 0
        if label_weights and "topic" in label_weights:
            topic_votes = int((label_weights["topic"].get(primary) or {}).get("votes", 0))
        # Deterministic: highest score first (-score), then fewest topic votes (+topic_votes)
        return (-score, topic_votes)

    pool.sort(key=_pool_sort_key)

    out = list(candidates)
    target_indices = list(range(every - 1, n, every))

    # This pass runs after calibrated_rerank, so taking the best-scoring candidate every
    # time would undo the feed balancing it just did: one prolific feed with a backlog of
    # unseen articles wins slot after slot. Measured on live data before this guard, 20
    # slots held only 8 distinct feeds and a single feed took 7 of them. Falls back to
    # score order once every eligible feed has had a slot.
    used_feeds = set()

    for target_idx in target_indices:
        if not pool:
            break
        pick = next((i for i, c in enumerate(pool)
                     if c.get("source_id") not in used_feeds), 0)
        chosen = pool.pop(pick)
        used_feeds.add(chosen.get("source_id"))
        curr_idx = next(i for i, c in enumerate(out) if c is chosen)
        if curr_idx > target_idx:
            out.pop(curr_idx)
            out.insert(target_idx, chosen)

    assert len(out) == len(candidates), "promote_exploration_slots must return same number of items"
    assert set(id(c) for c in out) == set(id(c) for c in candidates), \
        "promote_exploration_slots must return exact same items"
    return out
