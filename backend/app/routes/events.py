import json
from typing import Optional, List, Union, Dict, Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..database import db
from ..services.article_events import denormalize_events

router = APIRouter(tags=["events"])

class EventItem(BaseModel):
    article_id: Optional[int] = None
    source_id: Optional[int] = None
    source_name: Optional[str] = None
    event_type: str
    primary_topic: Optional[str] = None
    secondary_topics: Optional[Union[str, List[str]]] = None
    region: Optional[str] = None
    article_type: Optional[str] = None
    dwell_seconds: Optional[int] = None
    created_at: Optional[str] = None

class EventBatch(BaseModel):
    events: List[EventItem]

@router.post("/api/reader/events")
async def post_events(payload: Union[EventBatch, List[EventItem], Dict[str, Any]], request: Request):
    if isinstance(payload, EventBatch):
        items = payload.events
    elif isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and "events" in payload:
        items = [EventItem(**ev) for ev in payload["events"]]
    else:
        raise HTTPException(status_code=400, detail="Invalid events payload format")

    # Reading events exist only to train the personalisation, so nothing is collected
    # while it is switched off. 200 rather than an error so a client draining a queued
    # outbox discards the batch instead of retrying it forever.
    if not db.get_system_settings_sync().get("ai_enabled", True):
        return {"success": True, "count": 0}

    if not items:
        return {"success": True, "count": 0}

    conn = await db._get_db()
    raw_dicts = [item.model_dump() if isinstance(item, EventItem) else dict(item) for item in items]
    events_to_insert = await denormalize_events(raw_dicts, conn=conn, db_instance=db)

    inserted_count = await db.insert_events(events_to_insert)
    return {"success": True, "count": inserted_count}
