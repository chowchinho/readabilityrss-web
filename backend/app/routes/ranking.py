"""Ranking API for the native Android app."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..database import db
from ..services.labels import canonical_label
from ..services.ranking import score_article_breakdown
from ..services.vote_weights import (
    _parse_ts,
    get_effective_weights,
    invalidate_weights_cache,
)
from ..services.article_events import record_article_vote, denormalize_events
from ..utils.fever_key import validate_api_key

router = APIRouter(tags=["ranking"])


def _ai_enabled() -> bool:
    return bool(db.get_system_settings_sync().get("ai_enabled", True))


def _parse_secondary_topics(raw) -> list[str]:
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, str)]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [s for s in parsed if isinstance(s, str)]
            return [raw]
        except Exception:
            return [raw]
    return []


@router.get("/api/reader/ranking/scores")
async def get_ranking_scores(
    api_key: str | None = Query(None),
    since: str | None = Query(None),
    since_id: int | None = Query(None),
    max_id: int | None = Query(None),
):
    if not await validate_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    ai_on = _ai_enabled()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    real_since_id = since_id if isinstance(since_id, int) else None
    real_max_id = max_id if isinstance(max_id, int) else None

    where_clauses = ["a.parse_status = 'success'"]
    params = []
    if real_since_id is not None:
        where_clauses.append("a.id > ?")
        params.append(real_since_id)
    if real_max_id is not None:
        where_clauses.append("a.id < ?")
        params.append(real_max_id)

    where_sql = " AND ".join(where_clauses)

    conn = await db._get_db()
    cursor = await conn.execute(f"""
        SELECT a.id, a.source_id, a.primary_topic, a.secondary_topics, a.region, a.article_type,
               a.created_at, a.updated_at, a.is_saved, av.vote AS vote, av.updated_at AS vote_updated_at
        FROM feed_articles a
        LEFT JOIN article_votes av ON av.article_id = a.id
        WHERE {where_sql}
        ORDER BY a.id ASC
    """, params)
    rows = await cursor.fetchall()

    since_dt = _parse_ts(since) if since else None

    tags = []
    present_topics = set()
    present_types = set()
    present_regions = set()
    present_secondaries = set()

    for r in rows:
        sec_list = _parse_secondary_topics(r["secondary_topics"])
        for s in sec_list:
            c = canonical_label(s)
            if c:
                present_secondaries.add(c)
        if r["primary_topic"] and r["primary_topic"] != "unknown":
            present_topics.add(r["primary_topic"])
        if r["article_type"] and r["article_type"] != "unknown":
            present_types.add(r["article_type"])
        if r["region"] and r["region"] != "unknown":
            present_regions.add(r["region"])

        last_mod = max(
            _parse_ts(r["updated_at"]) or datetime.min,
            _parse_ts(r["vote_updated_at"]) or datetime.min,
        )

        if since_dt is None or last_mod >= since_dt:
            tags.append({
                "id": r["id"],
                "primary": r["primary_topic"],
                "secondary": sec_list,
                # The weights payload is keyed by canonical label, so a client that stored only
                # the original spellings could never look a secondary weight up. Canonicalising
                # client-side would mean porting the exception lists in services/labels.py and
                # drifting from them, so the keys are sent instead.
                "secondary_canonical": [canonical_label(s) for s in sec_list],
                "region": r["region"],
                "type": r["article_type"],
                "vote": r["vote"],
            })

    if not ai_on:
        return {
            "scores": None,
            "extras": {},
            "tags": tags,
            "weights": {
                "topic": [],
                "type": [],
                "region": [],
                "secondary": [],
            },
            "ai_enabled": False,
            "generated_at": now_iso,
        }

    source_ids = list({r["source_id"] for r in rows})
    perc_map = await db.get_feed_age_percentiles(source_ids) if source_ids else {}
    exposure_map = await db.get_article_exposure([r["id"] for r in rows]) if rows else {}
    effective_weights = await get_effective_weights(db_instance=db)
    vote_settings = db.get_system_settings_sync()
    display_names = await db.get_secondary_display_names()

    scores = []
    extras = {}

    for r in rows:
        art_id = r["id"]
        sec_list = _parse_secondary_topics(r["secondary_topics"])
        tags_dict = {
            "primary": r["primary_topic"],
            "secondary": sec_list,
            "region": r["region"],
            "type": r["article_type"],
        }
        art_perc = perc_map.get(r["source_id"], {}).get(art_id, 0.5)

        bd = score_article_breakdown(
            tags_dict,
            art_perc,
            weights=effective_weights,
            vote=r["vote"],
            settings=vote_settings,
            exposure=exposure_map.get(art_id),
            is_saved=bool(r["is_saved"]),
        )

        score_val = round(bd["total"], 2)
        scores.append([art_id, score_val])

        freshness_val = 0.0
        exposure_val = 0.0
        pairs_val = []
        for term in bd["terms"]:
            if term["kind"] == "freshness":
                freshness_val = round(term["contribution"], 2)
            elif term["kind"] == "exposure":
                exposure_val = round(term["contribution"], 2)
            elif term["kind"] == "pair":
                pairs_val.append([term["label"], round(term["contribution"], 2)])

        extras[str(art_id)] = {
            "freshness": freshness_val,
            "exposure": exposure_val,
            "pairs": pairs_val,
        }

    weights_out = {
        "topic": [],
        "type": [],
        "region": [],
        "secondary": [],
    }

    if effective_weights:
        for axis, present_set in [
            ("topic", present_topics),
            ("type", present_types),
            ("region", present_regions),
            ("secondary", present_secondaries),
        ]:
            axis_weights = effective_weights.get(axis, {})
            rows_out = []
            for label, entry in axis_weights.items():
                if label in present_set:
                    row = {
                        "label": display_names.get(label, label) if axis == "secondary" else label,
                        "declared": float(entry.get("declared", 0.0)),
                        "prior": float(entry.get("prior", 0.0)),
                        "votes": int(entry.get("votes", 0)),
                        "explicit": float(entry.get("explicit", 0.0)),
                        "behavioural": float(entry.get("behavioural", 0.0)),
                        "effective": float(entry.get("effective", 0.0)),
                    }
                    if axis == "secondary":
                        row["canonical"] = label
                    rows_out.append(row)
            weights_out[axis] = rows_out

    return {
        "scores": scores,
        "extras": extras,
        "tags": tags,
        "weights": weights_out,
        "ai_enabled": True,
        "generated_at": now_iso,
    }


class RankingVoteItem(BaseModel):
    article_id: int
    vote: str | None = None
    mark_read: bool = True


class RankingEventItem(BaseModel):
    article_id: int | None = None
    source_id: int | None = None
    source_name: str | None = None
    event_type: str
    primary_topic: str | None = None
    secondary_topics: str | list[str] | None = None
    region: str | None = None
    article_type: str | None = None
    dwell_seconds: int | float | None = None
    created_at: str | None = None


class RankingFeedbackRequest(BaseModel):
    api_key: str | None = None
    votes: list[RankingVoteItem] | None = []
    events: list[RankingEventItem] | None = []


@router.post("/api/reader/ranking/feedback")
async def post_ranking_feedback(
    req: RankingFeedbackRequest,
    api_key: str | None = Query(None),
):
    effective_api_key = req.api_key or api_key
    if not await validate_api_key(effective_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    ai_on = _ai_enabled()
    if not ai_on:
        return {
            "success": True,
            "votes_processed": 0,
            "events_processed": 0,
            "votes": [],
            "events": [],
        }

    conn = await db._get_db()
    votes_accepted = 0
    votes_rejected = 0
    vote_results = []

    if req.votes:
        for v in req.votes:
            ok, err_reason, _ = await record_article_vote(
                v.article_id, v.vote, mark_read=v.mark_read, conn=conn, db_instance=db
            )
            if not ok:
                votes_rejected += 1
                vote_results.append({"article_id": v.article_id, "accepted": False, "reason": err_reason})
            else:
                votes_accepted += 1
                vote_results.append({"article_id": v.article_id, "accepted": True})

    events_accepted = 0
    events_rejected = 0
    event_results = []
    events_to_insert = []

    if req.events:
        raw_events = [ev.dict(exclude_none=True) for ev in req.events]
        events_to_insert = await denormalize_events(raw_events, conn=conn, db_instance=db)
        for ev in events_to_insert:
            events_accepted += 1
            event_results.append({"article_id": ev.get("article_id"), "accepted": True})

        if events_to_insert:
            await db.insert_events(events_to_insert)

    return {
        "success": True,
        "votes_processed": votes_accepted,
        "events_processed": events_accepted,
        "votes": vote_results,
        "events": event_results,
    }
