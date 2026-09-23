"""A provider outage must not empty the queue.

The worker has to tell two failures apart, because they look identical from the
outside and deserve opposite treatment:

  - the article has nothing to translate (an image gallery, no text blocks). It will
    never succeed, so it comes out of the queue.
  - the model server is unreachable. Every row would fail the same way, and taking
    them out of the queue would silently drop articles that are perfectly fine.

The first version of this code marked a row unusable on any failure, so a dead server
dequeued three articles per pass — 36 an hour — and none of them would ever have been
translated.
"""
from unittest.mock import AsyncMock, patch
import pytest

from app.services import scheduler
from app.services import translation


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture — these are pure unit tests."""
    yield


def _rows(n):
    return [{"id": i, "title": f"t{i}", "content": f"<p>本文{i}</p>",
             "original_title": None, "translation_pending": 1,
             "translation_note": None, "detected_language": "ja",
             "source_name": "Feed"} for i in range(1, n + 1)]


def _patches(rows, translate):
    return (
        patch.object(scheduler.db, "get_system_settings",
                     AsyncMock(return_value={"target_language": "zh-TW"})),
        patch.object(scheduler.db, "get_articles_awaiting_translation",
                     AsyncMock(return_value=rows)),
        patch.object(scheduler.db, "save_deferred_translation", AsyncMock()),
        patch.object(scheduler.db, "count_articles_awaiting_translation",
                     AsyncMock(return_value=0)),
        patch("app.services.translation_worker.translate_article", side_effect=translate),
    )


@pytest.mark.asyncio
async def test_a_dead_server_dequeues_nothing():
    """Every row here is translatable; the server is simply gone."""
    rows = _rows(10)

    def translate(row, target):
        raise translation.LMTError("connection refused")

    a, b, c, d, e = _patches(rows, translate)
    with a, b, c, d, e, \
         patch.object(scheduler.db, "mark_translation_unusable", AsyncMock()) as unusable:
        done = await scheduler.run_deferred_translations(budget_seconds=60)

    assert done == 0
    unusable.assert_not_awaited(), "an outage must never take articles out of the queue"


@pytest.mark.asyncio
async def test_a_dead_server_still_stops_the_pass_early():
    rows = _rows(10)
    seen = []

    def translate(row, target):
        seen.append(row["id"])
        raise translation.LMTError("connection refused")

    a, b, c, d, e = _patches(rows, translate)
    with a, b, c, d, e, patch.object(scheduler.db, "mark_translation_unusable", AsyncMock()):
        await scheduler.run_deferred_translations(budget_seconds=60)

    assert len(seen) == scheduler.DEFERRED_MAX_CONSECUTIVE_FAILURES


@pytest.mark.asyncio
async def test_an_untranslatable_row_is_still_dequeued():
    """The image-gallery case keeps its old behaviour."""
    rows = _rows(3)

    def translate(row, target):
        return None if row["id"] == 1 else {
            "id": row["id"], "title": "標題", "content": "<p>譯</p>",
            "original_title": "t", "provider": "LMT-60-1.7B", "blocks": 1}

    a, b, c, d, e = _patches(rows, translate)
    with a, b, c, d, e, \
         patch.object(scheduler.db, "mark_translation_unusable", AsyncMock()) as unusable:
        done = await scheduler.run_deferred_translations(budget_seconds=60)

    assert done == 2
    unusable.assert_awaited_once_with(1)
