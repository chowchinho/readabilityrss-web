"""Translation status report for the dashboard, derived from article state.

Local-model runs and failures are not logged anywhere, so everything here is read
back from the articles themselves: the "Translated by X" badge names the provider,
"(fell back from ...)" in that badge marks a fallback, the red error banner marks a
failure, and translation_pending marks the queue. Wait times use updated_at, which a
later image refresh can push forward, so they can read slightly long.
"""
from datetime import datetime, timedelta

WINDOW_HOURS = (1, 6, 12, 24, 72, 168)
ERROR_MARKER = "(Translation Error)"
FALLBACK_MARKER = " (fell back from"


def bucket_minutes(hours: int) -> int:
    if hours <= 1:
        return 10
    if hours <= 24:
        return 60
    return 360


def parse_badge(fragment: str | None) -> tuple[str | None, bool]:
    """Provider name and whether it was a fallback, from the text after "Translated by "."""
    if not fragment:
        return None, False
    text = fragment.split("<", 1)[0].strip()
    if not text:
        return None, False
    if FALLBACK_MARKER in text:
        return text.split(FALLBACK_MARKER, 1)[0].strip(), True
    return text, False


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("T", " ")[:19])


_EPOCH = datetime(1970, 1, 1)


def _bucket_start(ts: datetime, minutes: int) -> datetime:
    # Naive UTC arithmetic: .timestamp() would read these as local time.
    epoch_min = int((ts - _EPOCH).total_seconds() // 60)
    return _EPOCH + timedelta(minutes=epoch_min - epoch_min % minutes)


def build_status(*, hours: int, now: datetime, translated: list[dict],
                 arrivals: list[dict], queue: list[dict], costs: list[dict]) -> dict:
    """Aggregate raw rows into the report.

    translated: rows updated in the window carrying a badge (created_at, updated_at, badge).
    arrivals:   rows created in the window on translate-enabled feeds
                (created_at, badge, has_error, pending).
    queue:      pending rows grouped by feed (source, translator, count, oldest).
    costs:      translation_usage totals by provider (provider, cost_cny).
    """
    step = bucket_minutes(hours)
    start = now - timedelta(hours=hours)

    providers: dict[str, dict] = {}
    series: dict[datetime, dict] = {}
    first = _bucket_start(start, step)
    b = first
    while b <= now:
        series[b] = {"arrived": 0, "translated": {}}
        b += timedelta(minutes=step)

    for row in translated:
        name, fallback = parse_badge(row.get("badge"))
        if not name:
            continue
        done = _ts(row["updated_at"])
        wait = max(0.0, (done - _ts(row["created_at"])).total_seconds() / 60)
        p = providers.setdefault(name, {"provider": name, "articles": 0, "fallbacks": 0,
                                        "wait_total": 0.0, "max_wait_min": 0.0,
                                        "cost_cny": 0.0})
        p["articles"] += 1
        p["fallbacks"] += int(fallback)
        p["wait_total"] += wait
        p["max_wait_min"] = max(p["max_wait_min"], wait)
        key = _bucket_start(done, step)
        if key in series:
            t = series[key]["translated"]
            t[name] = t.get(name, 0) + 1

    for c in costs:
        label = _cost_label(c["provider"])
        if label in providers:
            providers[label]["cost_cny"] += c["cost_cny"] or 0

    for p in providers.values():
        p["avg_wait_min"] = round(p.pop("wait_total") / p["articles"], 1)
        p["max_wait_min"] = round(p["max_wait_min"], 1)
        p["cost_cny"] = round(p["cost_cny"], 4)

    ok = fallback_n = failed = queued = unbadged = 0
    for row in arrivals:
        key = _bucket_start(_ts(row["created_at"]), step)
        if key in series:
            series[key]["arrived"] += 1
        name, fallback = parse_badge(row.get("badge"))
        if row.get("has_error"):
            failed += 1
        elif name:
            ok += 1
            fallback_n += int(fallback)
        elif row.get("pending"):
            queued += 1
        else:
            unbadged += 1

    settled = ok + failed + unbadged
    return {
        "hours": hours,
        "bucket_minutes": step,
        "generated_at": now.isoformat(timespec="seconds"),
        "summary": {
            "translated": sum(p["articles"] for p in providers.values()),
            "needed": len(arrivals),
            "succeeded": ok,
            "fallbacks": fallback_n,
            "failed": failed,
            "unbadged": unbadged,
            "queued": queued,
            "success_rate": round(ok / settled, 4) if settled else None,
            "cost_cny": round(sum(c["cost_cny"] or 0 for c in costs), 4),
        },
        "providers": sorted(providers.values(), key=lambda p: -p["articles"]),
        "series": [{"start": k.isoformat(timespec="minutes"), **v}
                   for k, v in sorted(series.items())],
        "queue": {
            "total": sum(q["count"] for q in queue),
            "oldest": min((q["oldest"] for q in queue), default=None),
            "by_feed": queue,
        },
    }


def _cost_label(provider: str | None) -> str:
    from .translation import LMT_PROVIDER_LABEL, QWEN_PROVIDER_LABEL
    return {"qwen": QWEN_PROVIDER_LABEL, "lmt": LMT_PROVIDER_LABEL,
            "deepseek": "DeepSeek", "google": "Google Translate",
            "deepl": "DeepL"}.get(provider or "", provider or "")
