from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..database import db
from ..utils import hk_glossary

router = APIRouter(prefix="/api/translation", tags=["translation"])


@router.get("/usage")
async def get_usage(days: int = 7):
    """Per-feed translation cost (CNY) over the last `days` days."""
    rows = await db.get_translation_usage_by_source(days)
    by_source = {
        row["source_id"]: {"cost_cny": round(row["cost_cny"], 4), "calls": row["calls"]}
        for row in rows
    }
    total = round(sum(row["cost_cny"] for row in rows), 4)
    return {"days": days, "total_cny": total, "by_source": by_source}


@router.get("/usage/summary")
async def get_usage_summary(days: int = 30):
    """Totals per provider, a daily series, and the costliest feeds."""
    if days not in (7, 14, 30, 90):
        raise HTTPException(status_code=400, detail="days must be 7, 14, 30 or 90")
    return await db.get_translation_usage_summary(days)


class GlossaryOverride(BaseModel):
    tw: str
    hk: Optional[str] = ""
    enabled: Optional[bool] = True


@router.get("/glossary")
async def get_glossary():
    """The built-in table, the user's overrides, and what is actually in force."""
    overrides = await db.get_glossary_overrides()
    return {
        "builtin": hk_glossary.builtin_terms(),
        "rejected": [{"tw": tw, "hk": hk, "count": n} for tw, hk, n in hk_glossary.REJECTED],
        "overrides": [
            {"tw": r["tw"], "hk": r["hk"], "enabled": bool(r["enabled"])} for r in overrides
        ],
        "active": hk_glossary.active_terms(),
        "stats": hk_glossary.stats(),
    }


@router.put("/glossary")
async def put_glossary_override(req: GlossaryOverride):
    """Add a term, change one, or switch a built-in off (hk empty / enabled false)."""
    tw = (req.tw or "").strip()
    hk = (req.hk or "").strip()
    if not tw:
        raise HTTPException(status_code=400, detail="tw is required")
    if len(tw) > 20 or len(hk) > 20:
        raise HTTPException(status_code=400, detail="terms are limited to 20 characters")
    if hk and hk == tw:
        raise HTTPException(status_code=400, detail="the two sides must differ")
    if req.enabled and not hk:
        raise HTTPException(status_code=400, detail="an enabled override needs a replacement")
    await db.upsert_glossary_override(tw, hk, bool(req.enabled))
    await db.refresh_glossary_overrides()
    return {"ok": True, "stats": hk_glossary.stats()}


@router.delete("/glossary/{tw}")
async def delete_glossary_override(tw: str):
    await db.delete_glossary_override(tw)
    await db.refresh_glossary_overrides()
    return {"ok": True, "stats": hk_glossary.stats()}


@router.post("/glossary/preview")
async def preview_glossary(req: dict):
    """Run the pass over a sample of text so the user can see what it does."""
    text = (req or {}).get("text", "")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="text is limited to 4000 characters")
    return {"before": text, "after": hk_glossary.localize(text)}
