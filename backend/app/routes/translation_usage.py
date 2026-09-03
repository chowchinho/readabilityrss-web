from fastapi import APIRouter
from ..database import db

router = APIRouter(prefix="/api/translation", tags=["translation"])


@router.get("/usage")
async def get_usage(days: int = 7):
    """Per-feed DeepSeek translation cost (CNY) over the last `days` days."""
    rows = await db.get_translation_usage_by_source(days)
    by_source = {
        row["source_id"]: {"cost_cny": round(row["cost_cny"], 4), "calls": row["calls"]}
        for row in rows
    }
    total = round(sum(row["cost_cny"] for row in rows), 4)
    return {"days": days, "total_cny": total, "by_source": by_source}
