# What (if anything) to take from X's open-sourced ranking code

Date: 2026-08-14
Source reviewed: https://github.com/xai-org/x-algorithm
Compared against: `backend/app/services/ranking.py`, `backend/app/services/rerank.py`,
`backend/app/services/vote_weights.py`, `backend/app/routes/ranking.py`, `backend/app/routes/reader.py`

Status: **notes only, nothing implemented.** Three candidate changes are described at the
bottom; none are approved.

---

## Verdict

Most of X's design is a scale artifact and does not transfer. One real gap surfaced during
the comparison, and it is a gap in our own system rather than something X invented — the
Android ranking API skips the diversity re-ranking that the web reader gets.

Our `declared + prior + behavioural → effective` weight blend is the right shape for a
single-user system, and arguably a better fit than X's approach. Two of X's better
structural ideas (diversity re-ranking, cold-start protection for unseen categories) we
already have, arrived at independently.

---

## What X's pipeline actually is

`sources → hydrators → filters → scorers → selectors → side effects`

| Stage | X | Why it does not transfer |
|---|---|---|
| Candidate sourcing | Thunder (follow graph), Phoenix (embedding retrieval), SimClusters (engagement clustering) | Exists to pick ~1500 posts from billions. Our candidate set *is* the subscription list — a few hundred articles from ~103 sources. There is nothing to retrieve. |
| Light ranker → heavy ranker | Two-stage to fit a compute budget | Our scorer is arithmetic over four tag axes. Scoring the whole corpus is free. |
| Phoenix scorer | Neural net predicting 8 action probabilities | Needs billions of labelled interactions across millions of users. |
| Ranking scorer | `Final Score = Σ (weight_i × P(action_i))` | The weights are hand-set; the *probabilities* are learned. We have one user and an events table — no collaborative signal exists for us by construction. |
| VM Ranker (DPP) | Diversity re-ordering | **We already have this**: Steck calibrated greedy re-rank, `services/rerank.py`. |
| New-author boost | Cold start for unseen authors | **We already have this**: `rerank_target_floor`, and the reasoning is written out at `rerank.py:16` — "a reader cannot vote on what they are never shown". |
| Pre-scoring filters (11) | Dedup, old posts, blocked accounts, muted keywords | Partially relevant — see finding 2 and 3. |
| Ranking vs. visibility split | Ordering and "should this be shown at all" are separate stages | The genuinely portable idea. See finding 2. |

The one architectural principle worth writing down: **X separates ranking (what order) from
visibility (whether to show at all).** Ours are fused into a single score.

---

## Finding 1 — Android ranking API has no diversity control

**The gap.** `calibrated_rerank` is called in exactly one place:
[`reader.py:324`](../backend/app/routes/reader.py#L324), inside the article-list endpoint,
and only when `sort == "smart"`.

`/api/reader/ranking/scores` ([`routes/ranking.py:47`](../backend/app/routes/ranking.py#L47))
returns a flat `[[id, score], …]` list with no re-ranking applied. An Android client that
sorts by that score gets **pure score order**: no per-feed balancing, no topic balancing, no
exploration floor.

**Concrete symptom.** On a day when one source publishes 12 Consumer Tech pieces, the top of
the Android feed is that source 12 times. The web reader would have spread them out.

**Two ways to fix — this is a real design choice, not a detail:**

- **(a) Server-side.** Apply `calibrated_rerank` inside the ranking endpoint and return a
  `rank` field alongside `score`.
  - Pro: consistent with web by construction; single implementation; matches X's model where
    ordering is a server concern and the client renders.
  - Con: the client can no longer meaningfully re-sort locally after offline votes, which is
    part of why the API was built to hand out raw scores in the first place.
  - Also: the re-rank is currently *page-scoped* (it re-orders one `limit`/`offset` slice).
    A server-side rank over an unbounded score list needs a decision about what window it
    balances across.

- **(b) Client-side.** Ship the re-rank parameters (`lambda_`, `floor`, target shares per
  dimension) in the payload and port the greedy loop to Kotlin.
  - Pro: preserves offline re-scoring and re-sorting after local votes.
  - Con: a port that will drift from `rerank.py`, including the `lambda_` calibration note at
    `rerank.py:129` warning that the constant sits on a cliff and must be re-checked if a
    dimension is added or removed.

**Before doing either: measure.** Pull a live scores payload, sort by score, and count the
max run-length per `source_id` and per primary topic in the top 50. If the runs are short,
this is theoretical and can be dropped.

**Note on dimensions.** `calibrated_rerank` balances across feed, primary topic and type
only — *not* region and not secondary topics. Whether region belongs as a fourth dimension is
a separate open question; adding one invalidates the `lambda_` calibration.

---

## Finding 2 — `show_less` is a score penalty, not a filter

[`ranking.py:180`](../backend/app/services/ranking.py#L180) applies
`vote_article_penalty` (default `-4.0`) as an additive term. An article in a strongly-liked
topic with several positive axes can out-score that penalty and climb back onto the page
after being explicitly rejected.

X would drop it in a pre-scoring filter — no score arithmetic can resurrect it.

**Question to settle first:** is `show_less` on an *article* meant to mean "never show me this
one again" (→ hard filter) or "this is evidence about my taste" (→ current behaviour, and the
vote also feeds `vote_weights` regardless)? The two readings are both defensible and the
answer decides the change. If it becomes a filter, the vote must still be recorded for weight
learning — only the display is suppressed.

---

## Finding 3 — No cross-source article dedup

`grep` for dedup/duplicate across `backend/app` finds it only in `link_discovery.py` (by URL),
`dom_comparator.py` and `translation.py`. There is no dedup between *articles from different
sources*.

Effect: the same wire story carried by three outlets occupies three slots. X kills this in a
pre-scoring filter.

Note this partially overlaps finding 1 — the feed-diversity dimension in `calibrated_rerank`
spreads the duplicates apart but does not remove them.

Cheapest viable approach: normalised-title similarity within a time window, keeping the
highest-scoring copy. Needs a real look at how often it actually happens before it is worth
any code.

---

## Explicitly not worth pursuing

- Embedding-based or clustering-based candidate retrieval (SimClusters/Phoenix analogues).
- Two-stage light/heavy ranking.
- Replacing the hand-declared weight tables with a learned action-probability model. Single
  user, no collaborative signal, and the existing `declared + prior + behavioural` blend
  already occupies this niche correctly.
- Out-of-network discounting — every source in our corpus is one the reader subscribed to,
  so there is no "out of network".

---

## Suggested order if picked up later

1. Measure the run-length problem in the Android scores payload (finding 1). Cheap, decides
   whether anything else here matters.
2. Settle the `show_less` semantics question (finding 2). No code until that is answered.
3. Measure duplicate frequency across sources (finding 3) before writing any dedup.
