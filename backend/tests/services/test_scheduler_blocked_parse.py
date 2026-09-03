import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.database import Database
from app.services import scheduler
from app.services.parser import ReadabilityParser
from app.utils.timeutil import utcnow


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def challenge_html():
    return (FIXTURES_DIR / "akamai_challenge.html").read_text(encoding="utf-8")


@pytest.fixture
def sample_html():
    return (FIXTURES_DIR / "sample.html").read_text(encoding="utf-8")


def test_looks_like_blocked_parse_detects_akamai_challenge(challenge_html):
    parser = ReadabilityParser()
    result = parser.extract_with_images(
        challenge_html,
        base_url="https://news.sky.com/story/example",
    )

    assert scheduler._looks_like_blocked_parse(challenge_html, result) is True


def test_looks_like_blocked_parse_ignores_normal_article(sample_html):
    parser = ReadabilityParser()
    result = parser.extract_with_images(
        sample_html,
        base_url="https://example.com/article",
    )

    assert scheduler._looks_like_blocked_parse(sample_html, result) is False


def test_looks_like_blocked_parse_ignores_thin_but_valid_article():
    parser = ReadabilityParser()
    html = """
    <html>
      <head>
        <title>Short Update</title>
        <meta property="og:title" content="Short Update">
      </head>
      <body>
        <article>
          <h1>Short Update</h1>
          <p>Markets closed higher today.</p>
        </article>
      </body>
    </html>
    """
    result = parser.extract_with_images(html, base_url="https://example.com/short")

    assert scheduler._looks_like_blocked_parse(html, result) is False


@pytest.mark.asyncio
async def test_parse_article_marks_blocked_challenge_failed(monkeypatch, challenge_html):
    article = {"id": 123, "url": "https://news.sky.com/story/example", "main_image": None}

    monkeypatch.setattr(scheduler, "_fetch_html", AsyncMock(return_value=challenge_html))
    monkeypatch.setattr(scheduler, "fetch_html_rendered_async", AsyncMock(return_value=challenge_html))
    monkeypatch.setattr(scheduler, "cache_article_images", AsyncMock())

    update_parsed = AsyncMock()
    update_failed = AsyncMock()
    monkeypatch.setattr(scheduler.db, "update_article_parsed", update_parsed)
    monkeypatch.setattr(scheduler.db, "update_article_failed", update_failed)

    ok = await scheduler._parse_article(article, source_name="Sky News")

    assert ok is False
    update_parsed.assert_not_awaited()
    update_failed.assert_awaited_once()
    assert "Blocked by anti-bot challenge page" in update_failed.await_args.args[1]


@pytest.mark.asyncio
async def test_parse_article_uses_rendered_html_when_it_recovers_title(monkeypatch, challenge_html):
    article = {"id": 456, "url": "https://example.com/rendered", "main_image": None}
    rendered_html = """
    <html>
      <head>
        <title>Recovered article title</title>
        <meta property="og:title" content="Recovered article title">
      </head>
      <body>
        <article>
          <h1>Recovered article title</h1>
          <p>This article becomes readable only after the rendered fetch path succeeds.</p>
        </article>
      </body>
    </html>
    """

    monkeypatch.setattr(scheduler, "_fetch_html", AsyncMock(return_value=challenge_html))
    monkeypatch.setattr(scheduler, "fetch_html_rendered_async", AsyncMock(return_value=rendered_html))
    monkeypatch.setattr(
        scheduler,
        "cache_article_images",
        AsyncMock(side_effect=lambda content, main_image, base_url="": (content, main_image)),
    )

    update_parsed = AsyncMock()
    update_failed = AsyncMock()
    monkeypatch.setattr(scheduler.db, "update_article_parsed", update_parsed)
    monkeypatch.setattr(scheduler.db, "update_article_failed", update_failed)

    ok = await scheduler._parse_article(article, source_name="Example")

    assert ok is True
    update_failed.assert_not_awaited()
    update_parsed.assert_awaited_once()
    assert update_parsed.await_args.args[1] == "Recovered article title"


@pytest.mark.asyncio
async def test_refresh_all_feeds_continues_after_source_timeout(monkeypatch):
    slow_source = {"id": 92, "name": "GAME Watch", "error_count": 0}
    fast_source = {"id": 93, "name": "BBC News", "error_count": 0}

    async def fake_refresh(source, batch_started_at=None):
        if source["id"] == slow_source["id"]:
            await asyncio.sleep(0.05)
        return None

    monkeypatch.setattr(scheduler.db, "get_sources_due_for_refresh", AsyncMock(return_value=[slow_source, fast_source]))
    update_source = AsyncMock()
    monkeypatch.setattr(scheduler.db, "update_feed_source", update_source)
    monkeypatch.setattr(scheduler, "_refresh_source", AsyncMock(side_effect=fake_refresh))
    monkeypatch.setattr(scheduler, "SOURCE_REFRESH_TIMEOUT_SECONDS", 0.01)

    await scheduler._refresh_all_feeds_async()

    assert scheduler._refresh_source.await_count == 2
    update_source.assert_awaited_once()
    assert update_source.await_args.args[0] == slow_source["id"]
    assert "timed out" in update_source.await_args.args[1]["last_error"]
    assert update_source.await_args.args[1]["error_count"] == 1
    assert update_source.await_args.args[1]["next_check_at"]


def test_source_retry_delay_uses_capped_exponential_backoff():
    assert scheduler._source_retry_delay_hours(1) == 1
    assert scheduler._source_retry_delay_hours(2) == 2
    assert scheduler._source_retry_delay_hours(3) == 4
    assert scheduler._source_retry_delay_hours(4) == 8
    assert scheduler._source_retry_delay_hours(10) == 24


@pytest.mark.asyncio
async def test_refresh_source_failure_sets_next_retry(monkeypatch):
    source = {"id": 321, "name": "Broken Feed", "url": "https://example.com/feed", "error_count": 3}

    monkeypatch.setattr(scheduler, "_discover_links_for_source", AsyncMock(side_effect=RuntimeError("DNS failed")))
    # _refresh_source reads system settings; without this the test falls through
    # to the real database, which only exists on a machine that has run the app.
    monkeypatch.setattr(scheduler.db, "get_system_settings", AsyncMock(return_value={}))
    update_source = AsyncMock()
    monkeypatch.setattr(scheduler.db, "update_feed_source", update_source)

    await scheduler._refresh_source(source)

    update_source.assert_awaited_once()
    payload = update_source.await_args.args[1]
    assert payload["status"] == "red"
    assert payload["error_count"] == 4
    assert "DNS failed" in payload["last_error"]

    retry_at = datetime.strptime(payload["next_check_at"], "%Y-%m-%d %H:%M:%S")
    assert retry_at > utcnow() + timedelta(hours=7, minutes=55)
    assert retry_at < utcnow() + timedelta(hours=8, minutes=5)


@pytest.mark.asyncio
async def test_sources_due_for_refresh_includes_sources_after_three_failures(tmp_path):
    test_db = Database(db_path=str(tmp_path / "feeds.db"))
    await test_db.init()
    try:
        past = (utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        source_id = await test_db.create_feed_source({
            "name": "Previously Failed Feed",
            "url": "https://example.com/feed",
            "status": "red",
            "error_count": 3,
            "next_check_at": past,
            "enabled": 1,
        })

        due_sources = await test_db.get_sources_due_for_refresh()

        assert any(source["id"] == source_id for source in due_sources)
    finally:
        await test_db.close()


@pytest.mark.asyncio
async def test_retry_failed_articles_skips_when_refreshing(monkeypatch):
    monkeypatch.setattr(scheduler, "is_refreshing", lambda: True)
    get_retry = AsyncMock()
    monkeypatch.setattr(scheduler.db, "get_retry_articles", get_retry)

    await scheduler._retry_failed_articles_async()

    get_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_parse_article_persists_translation_before_image_cache(monkeypatch):
    article = {"id": 789, "url": "https://example.com/trans", "main_image": None}
    fake_html = "<html><head><title>Original Title</title></head><body><p>Original body text.</p></body></html>"

    monkeypatch.setattr(scheduler, "_fetch_html", AsyncMock(return_value=fake_html))
    monkeypatch.setattr(scheduler.db, "get_system_settings", AsyncMock(return_value={"target_language": "zh-TW"}))

    from app.services import translation
    monkeypatch.setattr(translation, "translate_text_async", AsyncMock(return_value=("翻譯標題", "deepl")))
    monkeypatch.setattr(translation, "translate_html_async", AsyncMock(return_value=("<p>翻譯內容</p>", "deepl")))

    update_translation = AsyncMock()
    monkeypatch.setattr(scheduler.db, "update_article_translation", update_translation)
    monkeypatch.setattr(scheduler, "cache_article_images", AsyncMock(side_effect=RuntimeError("Image cache crash")))
    monkeypatch.setattr(scheduler.db, "update_article_failed", AsyncMock())

    ok = await scheduler._parse_article(article, source_name="Test", translate_to="zh-TW")

    assert ok is False
    # Verified: translation is persisted before image cache fails
    update_translation.assert_awaited_once()
    assert update_translation.await_args.args[0] == 789
    assert update_translation.await_args.args[1] == "翻譯標題"
    assert "翻譯內容" in update_translation.await_args.args[2]


@pytest.mark.asyncio
async def test_refresh_source_resets_parse_progress_on_exception(monkeypatch):
    source = {"id": 100, "name": "Crashing Feed", "url": "https://example.com/feed", "max_articles": 10}

    monkeypatch.setattr(scheduler, "_discover_links_for_source", AsyncMock(return_value=[{"url": "https://example.com/1"}]))
    monkeypatch.setattr(scheduler.db, "insert_feed_articles", AsyncMock(return_value=[1]))
    monkeypatch.setattr(scheduler.db, "get_pending_articles", AsyncMock(return_value=[{"id": 1, "url": "https://example.com/1"}]))
    monkeypatch.setattr(scheduler, "_parse_article", AsyncMock(side_effect=RuntimeError("Crash during parse")))
    # _refresh_source reads system settings; without this the test falls through
    # to the real database, which only exists on a machine that has run the app.
    monkeypatch.setattr(scheduler.db, "get_system_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(scheduler.db, "get_prunable_articles", AsyncMock(return_value=[]))
    monkeypatch.setattr(scheduler.db, "delete_old_articles", AsyncMock())
    monkeypatch.setattr(scheduler.db, "update_feed_source", AsyncMock())

    await scheduler._refresh_source(source)

    progress = scheduler.get_parse_progress()
    assert progress["done"] == 0
    assert progress["total"] == 0
