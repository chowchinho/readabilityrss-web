"""One detached translation per article, with a replayable event log."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import ondemand_translation as od


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    yield


@pytest.fixture(autouse=True)
def clear_registry():
    od.clear()
    yield
    od.clear()


ARTICLE = {"id": 1, "source_id": 7, "url": "https://example.com/a",
           "title": "English title", "content": "<p>One</p><p>Two</p>"}


def _fake_iter(html, target_language="zh-TW", translator="qwen"):
    yield {"type": "block", "index": 0, "html": "<blockquote><p>One</p></blockquote><p>一</p>"}
    yield {"type": "block", "index": 1, "html": "<blockquote><p>Two</p></blockquote><p>二</p>"}
    yield {"type": "result", "html": "<p>translated body</p>",
           "provider": "Qwen-MT-flash", "total": 2}


@pytest.mark.asyncio
async def test_a_completed_job_emits_start_blocks_and_done():
    with patch.object(od.translation, "translate_html_iter", side_effect=_fake_iter), \
         patch.object(od.translation, "translate_text", return_value=("中文", "Qwen-MT-flash")), \
         patch.object(od, "db", AsyncMock()):
        job = od.start(ARTICLE, "zh-TW", "qwen")
        events = [e async for e in od.subscribe(job)]

    kinds = [e["type"] for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert [e["index"] for e in events if e["type"] == "block"] == [0, 1]


@pytest.mark.asyncio
async def test_starting_twice_joins_the_same_job():
    with patch.object(od.translation, "translate_html_iter", side_effect=_fake_iter), \
         patch.object(od.translation, "translate_text", return_value=("中文", "Qwen-MT-flash")), \
         patch.object(od, "db", AsyncMock()):
        first = od.start(ARTICLE, "zh-TW", "qwen")
        second = od.start(ARTICLE, "zh-TW", "qwen")
        assert first is second
        [e async for e in od.subscribe(first)]


@pytest.mark.asyncio
async def test_a_late_subscriber_replays_every_event():
    with patch.object(od.translation, "translate_html_iter", side_effect=_fake_iter), \
         patch.object(od.translation, "translate_text", return_value=("中文", "Qwen-MT-flash")), \
         patch.object(od, "db", AsyncMock()):
        job = od.start(ARTICLE, "zh-TW", "qwen")
        [e async for e in od.subscribe(job)]
        replayed = [e async for e in od.subscribe(job)]

    assert [e["type"] for e in replayed][-1] == "done"
    assert len([e for e in replayed if e["type"] == "block"]) == 2


@pytest.mark.asyncio
async def test_the_job_survives_a_subscriber_that_walks_away():
    """A locked phone must not cancel the translation or lose the write."""
    saver = AsyncMock()
    with patch.object(od.translation, "translate_html_iter", side_effect=_fake_iter), \
         patch.object(od.translation, "translate_text", return_value=("中文", "Qwen-MT-flash")), \
         patch.object(od, "db", AsyncMock(save_ondemand_translation=saver)):
        job = od.start(ARTICLE, "zh-TW", "qwen")
        async for _ in od.subscribe(job):
            break  # drop the stream after the first event
        await job.task

    assert job.done
    saver.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failure_writes_nothing_and_emits_an_error():
    saver = AsyncMock()

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    with patch.object(od.translation, "translate_html_iter", side_effect=boom), \
         patch.object(od.translation, "translate_text", return_value=("中文", "Qwen-MT-flash")), \
         patch.object(od, "db", AsyncMock(save_ondemand_translation=saver)):
        job = od.start(ARTICLE, "zh-TW", "qwen")
        events = [e async for e in od.subscribe(job)]

    assert events[-1]["type"] == "error"
    saver.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_article_no_provider_could_translate_is_an_error_not_a_write():
    saver = AsyncMock()

    def nothing_changed(html, target_language="zh-TW", translator="qwen"):
        yield {"type": "result", "html": html, "provider": "none", "total": 0}

    with patch.object(od.translation, "translate_html_iter", side_effect=nothing_changed), \
         patch.object(od.translation, "translate_text", return_value=("中文", "Qwen-MT-flash")), \
         patch.object(od, "db", AsyncMock(save_ondemand_translation=saver)):
        job = od.start(ARTICLE, "zh-TW", "qwen")
        events = [e async for e in od.subscribe(job)]

    assert events[-1]["type"] == "error"
    saver.assert_not_awaited()


MARKER_BANNER = (
    '<div style="color: #c62828; background-color: #ffebee; padding: 10px; '
    'margin-bottom: 1em; border-radius: 4px; font-weight: bold; text-align: center;">'
    '(Translation Error)</div>'
)

DAMAGED = {
    "id": 2, "source_id": 7, "url": "https://example.com/b",
    "title": "English title (Translation Error)",
    "content": MARKER_BANNER + "<p>One</p><p>Two</p>",
}


@pytest.mark.asyncio
async def test_the_translation_error_marker_is_stripped_not_carried_through():
    """The marker sits in a <div>, which is not a BLOCK_TAG.

    Translation can never touch it, so unless it is cleaned off the source the
    article ends up badged as translated with the red banner still on top.
    """
    saver = AsyncMock()
    with patch.object(od.translation, "translate_html_iter", side_effect=_fake_iter), \
         patch.object(od.translation, "translate_text", return_value=("\u4e2d\u6587", "Qwen-MT-flash")), \
         patch.object(od, "db", AsyncMock(save_ondemand_translation=saver)):
        job = od.start(DAMAGED, "zh-TW", "qwen")
        [e async for e in od.subscribe(job)]
        await job.task

    saver.assert_awaited_once()
    _, kwargs = saver.call_args
    args = saver.call_args.args
    stored_title = kwargs.get("title", args[1] if len(args) > 1 else "")
    stored_content = kwargs.get("content", args[2] if len(args) > 2 else "")
    original_title = kwargs.get("original_title", args[3] if len(args) > 3 else "")

    assert "(Translation Error)" not in stored_content
    assert "(Translation Error)" not in stored_title
    assert "(Translation Error)" not in original_title


def test_the_marker_source_is_cleaned_before_it_reaches_the_translator():
    """The translator must never be handed the marker as if it were prose."""
    sent = []

    def capture(html, target_language="zh-TW", translator="qwen"):
        sent.append(html)
        yield {"type": "result", "html": "<p>translated</p>",
               "provider": "Qwen-MT-flash", "total": 1}

    titles = []

    def capture_title(text, *args, **kwargs):
        titles.append(text)
        return "\u4e2d\u6587", "Qwen-MT-flash"

    with patch.object(od.translation, "translate_html_iter", side_effect=capture), \
         patch.object(od.translation, "translate_text", side_effect=capture_title):
        od._translate_blocking(DAMAGED, "zh-TW", "qwen", lambda e: None)

    assert sent and "(Translation Error)" not in sent[0]
    assert titles and "(Translation Error)" not in titles[0]


def test_an_ordinary_pull_quote_is_not_mistaken_for_an_interleaved_original():
    """clean_content would keep only the blockquote; on-demand must not use it.

    On-demand runs on untranslated articles, where a <blockquote> is a pull quote,
    not a translation's preserved original. Rebuilding from blockquotes there
    deletes the entire body except the quote.
    """
    sent = []

    def capture(html, target_language="zh-TW", translator="qwen"):
        sent.append(html)
        yield {"type": "result", "html": html, "provider": "Qwen-MT-flash", "total": 1}

    article = dict(
        DAMAGED,
        content=(MARKER_BANNER
                 + "<p>First paragraph.</p>"
                 + "<blockquote><p>A quote from someone.</p></blockquote>"
                 + "<p>Second paragraph.</p><p>Third paragraph.</p>"),
    )

    with patch.object(od.translation, "translate_html_iter", side_effect=capture), \
         patch.object(od.translation, "translate_text", return_value=("\u4e2d\u6587", "Qwen")):
        od._translate_blocking(article, "zh-TW", "qwen", lambda e: None)

    body = sent[0]
    assert "(Translation Error)" not in body
    for kept in ("First paragraph.", "A quote from someone.",
                 "Second paragraph.", "Third paragraph."):
        assert kept in body, f"on-demand dropped {kept!r} from the source body"
