"""A feed set to the local model must not translate its bodies inline.

LMT-60-1.7B runs 2-5 minutes on a normal article against ARTICLE_PARSE_TIMEOUT_SECONDS
= 240, and a source only gets 12 minutes for all of its articles. Translating a body
on the refresh path would time out the article and starve the ones behind it.

So a feed on `lmt` takes the title inline — measured at 2-3 seconds, which buys a
readable headline in the feed list straight away — and hands the body to the deferred
worker, which is where the local model belongs.
"""
from unittest.mock import AsyncMock, patch
import pytest

from app.services import scheduler


@pytest.fixture(autouse=True)
def bypass_auth_middleware():
    """Override the conftest autouse fixture — these are pure unit tests."""
    yield


@pytest.mark.asyncio
async def test_an_lmt_feed_translates_the_title_and_defers_the_body():
    calls = {}

    async def fake_text(text, target_language="zh-TW", translator="qwen", source_language="ja"):
        calls["title_translator"] = translator
        return "標題", scheduler.T.LMT_PROVIDER_LABEL

    async def fake_html(*a, **k):
        calls["body_translated"] = True
        return "", "none"

    with patch("app.services.translation.translate_text_async", side_effect=fake_text), \
         patch("app.services.translation.translate_html_async", side_effect=fake_html):
        title, content, provider, deferred = await scheduler._translate_for_feed(
            "見出し", "<p>本文です</p>", target_lang="zh-TW", translator="lmt")

    assert title == "標題", "the title is worth the few seconds inline"
    assert content == "<p>本文です</p>", "the body is stored untouched"
    assert provider == "none", "nothing was translated in the body, so do not badge it"
    assert deferred is True, "and it must be queued for the worker"
    assert calls["title_translator"] == "lmt"
    assert "body_translated" not in calls, "the body must never go through the refresh path"


@pytest.mark.asyncio
async def test_a_google_feed_still_translates_inline():
    """Google is fast enough to stay on the refresh path, and stays selectable."""
    async def fake_text(text, **k):
        return "標題", "Google Translate"

    async def fake_html(html, **k):
        return "<blockquote><p>本文です</p></blockquote><p>譯文</p>", "Google Translate"

    with patch("app.services.translation.translate_text_async", side_effect=fake_text), \
         patch("app.services.translation.translate_html_async", side_effect=fake_html):
        title, content, provider, deferred = await scheduler._translate_for_feed(
            "見出し", "<p>本文です</p>", target_lang="zh-TW", translator="google")

    assert provider == "Google Translate"
    assert "譯文" in content
    assert deferred is False


@pytest.mark.asyncio
async def test_a_google_feed_that_fails_is_deferred():
    async def fake_text(text, **k):
        return "見出し", "none"

    async def fake_html(html, **k):
        return html, "none"

    with patch("app.services.translation.translate_text_async", side_effect=fake_text), \
         patch("app.services.translation.translate_html_async", side_effect=fake_html):
        title, content, provider, deferred = await scheduler._translate_for_feed(
            "見出し", "<p>本文です</p>", target_lang="zh-TW", translator="google")

    assert provider == "none"
    assert deferred is True


@pytest.mark.asyncio
async def test_an_lmt_feed_with_no_body_still_gets_its_title():
    async def fake_text(text, **k):
        return "標題", scheduler.T.LMT_PROVIDER_LABEL

    with patch("app.services.translation.translate_text_async", side_effect=fake_text):
        title, content, provider, deferred = await scheduler._translate_for_feed(
            "見出し", "", target_lang="zh-TW", translator="lmt")

    assert title == "標題"
    assert deferred is False, "nothing to defer when there is no body"


@pytest.mark.asyncio
async def test_a_failed_title_does_not_stop_the_body_being_deferred():
    """The local server can be down for the title and back by the time the worker runs."""
    async def fake_text(text, **k):
        return "見出し", "none"

    with patch("app.services.translation.translate_text_async", side_effect=fake_text):
        title, content, provider, deferred = await scheduler._translate_for_feed(
            "見出し", "<p>本文です</p>", target_lang="zh-TW", translator="lmt")

    assert title == "見出し", "untouched, not damaged"
    assert deferred is True


# --- the badge a deferred body ends up with ---------------------------------


def test_a_feed_on_the_local_model_records_no_fallback():
    """Deferring is this provider's normal route, not a failure.

    A feed set to lmt always defers its body, so labelling that "LMT-60-1.7B —
    unavailable" produced the badge "Translated by LMT-60-1.7B (fell back from
    LMT-60-1.7B — unavailable)" on every second-tier article.
    """
    assert scheduler._deferred_note("", "lmt") == ""
    assert scheduler._deferred_note(scheduler.T.LMT_PROVIDER_LABEL, "lmt") == ""


def test_a_remote_provider_that_failed_is_still_named():
    note = scheduler._deferred_note(
        "LMT-60-1.7B (fell back from Google Translate — HTTP 429)", "google")
    assert note == "Google Translate — HTTP 429"


def test_a_remote_provider_with_no_reason_is_still_named():
    assert scheduler._deferred_note("none", "google") == "Google Translate — unavailable"


def test_the_worker_badges_a_deferred_lmt_body_plainly():
    """End of the chain: an empty note must give a plain badge, not a fallback one."""
    from unittest.mock import patch
    from app.services import translation_worker as W

    row = {"id": 1, "title": "見出し", "content": "<p>本文です</p>", "original_title": None,
           "translation_pending": 1, "translation_note": "", "detected_language": "ja"}

    with patch.object(W.translation, "_translate_blocks_lmt",
                      side_effect=lambda b, *a, **k: ["譯文"]), \
         patch.object(W.translation, "_lmt_block", return_value="標題"):
        result = W.translate_article(row, target_language="zh-TW")

    assert result["provider"] == "LMT-60-1.7B"
    assert "fell back" not in result["content"]
