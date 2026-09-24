"""Translation status report: badge parsing, success rate, bucketing, and the DB rows."""
from datetime import datetime

import pytest

from app.database import Database
from app.services import translation_status as ts

NOW = datetime(2026, 9, 24, 23, 30)


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    yield


def test_parse_badge_plain_and_fallback():
    assert ts.parse_badge("LMT-60-1.7B</p><p>body") == ("LMT-60-1.7B", False)
    assert ts.parse_badge("LMT-60-1.7B (fell back from Qwen-MT-flash — timeout)</p>") == (
        "LMT-60-1.7B", True)
    assert ts.parse_badge(None) == (None, False)
    assert ts.parse_badge("</p>") == (None, False)


def test_bucket_minutes_by_window():
    assert ts.bucket_minutes(1) == 10
    assert ts.bucket_minutes(12) == 60
    assert ts.bucket_minutes(24) == 60
    assert ts.bucket_minutes(168) == 360


def test_build_status_aggregates():
    translated = [
        {"created_at": "2026-09-24 21:00:00", "updated_at": "2026-09-24 21:30:00",
         "badge": "LMT-60-1.7B</p>"},
        {"created_at": "2026-09-24 21:00:00", "updated_at": "2026-09-24 22:30:00",
         "badge": "LMT-60-1.7B (fell back from Qwen-MT-flash)</p>"},
        {"created_at": "2026-09-24 22:00:00", "updated_at": "2026-09-24 22:02:00",
         "badge": "Qwen-MT-flash</p>"},
    ]
    arrivals = [
        {"created_at": "2026-09-24 21:00:00", "badge": "LMT-60-1.7B</p>",
         "has_error": 0, "pending": 0},
        {"created_at": "2026-09-24 21:00:00",
         "badge": "LMT-60-1.7B (fell back from Qwen-MT-flash)</p>", "has_error": 0, "pending": 0},
        {"created_at": "2026-09-24 22:00:00", "badge": None, "has_error": 1, "pending": 0},
        {"created_at": "2026-09-24 23:15:00", "badge": None, "has_error": 0, "pending": 1},
    ]
    queue = [{"source": "LOVE WALKER", "translator": "lmt", "count": 1,
              "oldest": "2026-09-24 23:15:00"}]
    costs = [{"provider": "qwen", "cost_cny": 0.0123}]

    out = ts.build_status(hours=12, now=NOW, translated=translated, arrivals=arrivals,
                          queue=queue, costs=costs)

    s = out["summary"]
    assert s["translated"] == 3
    assert (s["needed"], s["succeeded"], s["fallbacks"], s["failed"], s["queued"]) == (4, 2, 1, 1, 1)
    # Queued articles have not failed yet, so they stay out of the rate.
    assert s["success_rate"] == pytest.approx(2 / 3, abs=1e-4)

    lmt, qwen = out["providers"]
    assert lmt["provider"] == "LMT-60-1.7B" and lmt["articles"] == 2 and lmt["fallbacks"] == 1
    assert lmt["avg_wait_min"] == 60.0 and lmt["max_wait_min"] == 90.0
    assert qwen["cost_cny"] == 0.0123

    assert out["bucket_minutes"] == 60
    by_start = {b["start"]: b for b in out["series"]}
    assert by_start["2026-09-24T21:00"]["arrived"] == 2
    assert by_start["2026-09-24T22:00"]["translated"] == {"LMT-60-1.7B": 1, "Qwen-MT-flash": 1}
    assert out["queue"]["total"] == 1
    assert out["queue"]["oldest"] == "2026-09-24 23:15:00"


def test_build_status_empty_window():
    out = ts.build_status(hours=1, now=NOW, translated=[], arrivals=[], queue=[], costs=[])
    assert out["summary"]["success_rate"] is None
    assert out["queue"] == {"total": 0, "oldest": None, "by_feed": []}
    assert len(out["series"]) >= 6


@pytest.mark.asyncio
async def test_status_rows_from_database(tmp_path):
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        src = await test_db.create_feed_source({"name": "JP Feed", "url": "https://jp.example/feed"})
        other = await test_db.create_feed_source({"name": "EN Feed", "url": "https://en.example/feed"})
        await test_db.insert_feed_articles(src, [{"url": f"https://jp.example/{i}"} for i in range(3)])
        await test_db.insert_feed_articles(other, [{"url": "https://en.example/0"}])
        db = await test_db._get_db()
        await db.execute("UPDATE feed_sources SET translate_to = '1', translator = 'lmt' WHERE id = ?", (src,))
        badge = '<p style="x">\U0001F310 Translated by LMT-60-1.7B</p><p>本文</p>'
        await db.execute(
            "UPDATE feed_articles SET parse_status = 'success', content = ? WHERE url = ?",
            (badge, "https://jp.example/0"))
        await db.execute(
            "UPDATE feed_articles SET parse_status = 'success', content = '<p>x</p>', "
            "translation_pending = 1 WHERE url = ?", ("https://jp.example/1",))
        await db.execute(
            "UPDATE feed_articles SET parse_status = 'success', "
            "content = '<div>(Translation Error)</div><p>x</p>' WHERE url = ?",
            ("https://jp.example/2",))
        await db.execute("UPDATE feed_articles SET parse_status = 'success', content = '<p>en</p>' "
                         "WHERE source_id = ?", (other,))
        await db.commit()

        rows = await test_db.get_translation_status_rows(12)
        assert len(rows["translated"]) == 1
        assert rows["translated"][0]["badge"].startswith("LMT-60-1.7B</p>")
        assert len(rows["arrivals"]) == 3
        assert sum(r["has_error"] for r in rows["arrivals"]) == 1
        assert sum(r["pending"] for r in rows["arrivals"]) == 1
        assert rows["queue"] == [{"source": "JP Feed", "translator": "lmt", "count": 1,
                                  "oldest": rows["queue"][0]["oldest"]}]
    finally:
        test_db.close_sync()
