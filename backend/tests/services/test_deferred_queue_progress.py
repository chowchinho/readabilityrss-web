"""One unusable article must not stop the queue.

On 2026-09-23 the deferred worker did exactly one article per pass for forty minutes
and then stopped. Article 7476545 sat at the head of the queue: a `recovered-gallery`
div of images with no text blocks at all. `translate_article` correctly returned None,
and the pass broke on it — every pass, for as long as that row stayed at the head.

The break exists for a real case, a dead model server, where continuing would burn the
budget failing identically on every row. These tests separate the two: skip a row that
cannot be translated, stop only when the provider itself is gone.
"""
from unittest.mock import AsyncMock, patch
import pytest

from app.services import scheduler


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture — these are pure unit tests."""
    yield


def _rows(n):
    return [{"id": i, "title": f"t{i}", "content": f"<p>本文{i}</p>",
             "original_title": None, "translation_pending": 1,
             "translation_note": None, "detected_language": "ja",
             "source_name": "Feed"} for i in range(1, n + 1)]


def _result(row):
    return {"id": row["id"], "title": "標題", "content": "<p>譯</p>",
            "original_title": "t", "provider": "LMT-60-1.7B", "blocks": 1}


@pytest.mark.asyncio
async def test_an_untranslatable_row_is_skipped_not_fatal():
    """The image gallery case: no text to translate, so move on to the next row."""
    rows = _rows(4)
    seen = []

    def translate(row, target):
        seen.append(row["id"])
        return None if row["id"] == 1 else _result(row)

    with patch.object(scheduler.db, "get_system_settings",
                      AsyncMock(return_value={"target_language": "zh-TW"})), \
         patch.object(scheduler.db, "get_articles_awaiting_translation",
                      AsyncMock(return_value=rows)), \
         patch.object(scheduler.db, "save_deferred_translation", AsyncMock()) as save, \
         patch.object(scheduler.db, "mark_translation_unusable", AsyncMock()) as unusable, \
         patch.object(scheduler.db, "count_articles_awaiting_translation",
                      AsyncMock(return_value=0)), \
         patch("app.services.translation_worker.translate_article", side_effect=translate):
        done = await scheduler.run_deferred_translations(budget_seconds=60)

    assert seen == [1, 2, 3, 4], "every row must be attempted"
    assert done == 3
    assert save.await_count == 3
    unusable.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_a_dead_provider_still_stops_the_pass():
    """Nothing translates, so stop rather than fail identically 50 times."""
    rows = _rows(10)
    seen = []

    def translate(row, target):
        seen.append(row["id"])
        return None

    with patch.object(scheduler.db, "get_system_settings",
                      AsyncMock(return_value={"target_language": "zh-TW"})), \
         patch.object(scheduler.db, "get_articles_awaiting_translation",
                      AsyncMock(return_value=rows)), \
         patch.object(scheduler.db, "save_deferred_translation", AsyncMock()), \
         patch.object(scheduler.db, "mark_translation_unusable", AsyncMock()), \
         patch.object(scheduler.db, "count_articles_awaiting_translation",
                      AsyncMock(return_value=10)), \
         patch("app.services.translation_worker.translate_article", side_effect=translate):
        done = await scheduler.run_deferred_translations(budget_seconds=60)

    assert done == 0
    assert len(seen) == scheduler.DEFERRED_MAX_CONSECUTIVE_FAILURES, \
        "give up after a few failures in a row, not after all 10"


@pytest.mark.asyncio
async def test_a_success_resets_the_failure_run():
    """Scattered unusable rows must not add up to a false 'provider is down'."""
    rows = _rows(9)
    seen = []

    def translate(row, target):
        seen.append(row["id"])
        return None if row["id"] % 2 else _result(row)

    with patch.object(scheduler.db, "get_system_settings",
                      AsyncMock(return_value={"target_language": "zh-TW"})), \
         patch.object(scheduler.db, "get_articles_awaiting_translation",
                      AsyncMock(return_value=rows)), \
         patch.object(scheduler.db, "save_deferred_translation", AsyncMock()), \
         patch.object(scheduler.db, "mark_translation_unusable", AsyncMock()), \
         patch.object(scheduler.db, "count_articles_awaiting_translation",
                      AsyncMock(return_value=0)), \
         patch("app.services.translation_worker.translate_article", side_effect=translate):
        done = await scheduler.run_deferred_translations(budget_seconds=60)

    assert seen == list(range(1, 10)), "alternating failures must not end the pass"
    assert done == 4
