"""Background scheduler for RSS feed generation — asyncio-native (no APScheduler)."""
import asyncio
import logging
import json
import re
import threading
from collections import deque
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from ..database import db
from ..services.article_image_cache import (
    cache_article_images,
    content_has_uncached_signed_images,
    recache_signed_images,
    release_cached_images,
    sweep_derived_image_cache,
    sweep_orphaned_cached_images,
)
from ..services.link_discovery import LinkDiscovery
from ..utils.timeutil import utcnow
from ..services.parser import ReadabilityParser
from ..services.topic_classifier import clean_article_body
from ..utils.fetch import fetch_html_async, fetch_html_rendered_async
from ..utils.structured_article import extract_structured_article

logger = logging.getLogger(__name__)
discovery_service = LinkDiscovery()
parser = ReadabilityParser()

_CHALLENGE_HTML_MARKERS = (
    "sec-if-cpt-container",
    "behavioral-content",
    "powered and protected by",
    "akamai",
)
SOURCE_REFRESH_TIMEOUT_SECONDS = 12 * 60
# Ultra-dense articles (300+ blocks) translate one batch/block at a time and run
# ~100-160s; 240s leaves headroom for that worst case plus rate-limit retries.
ARTICLE_PARSE_TIMEOUT_SECONDS = 240
MAX_SOURCE_RETRY_DELAY_HOURS = 24
TIMEOUT_SHORT_RETRY_MINUTES = 15
TIMEOUT_SHORT_RETRY_ATTEMPTS = 2


def _source_retry_delay_hours(error_count: int) -> int:
    """Return capped exponential backoff delay for source refresh failures."""
    failures = max(1, error_count)
    return min(2 ** (failures - 1), MAX_SOURCE_RETRY_DELAY_HOURS)


def _source_retry_timestamp(error_count: int, ref_time: datetime = None) -> str:
    ref = ref_time or utcnow()
    retry_at = ref + timedelta(hours=_source_retry_delay_hours(error_count))
    return retry_at.strftime("%Y-%m-%d %H:%M:%S")


def _source_timeout_retry_delay_minutes(error_count: int) -> int:
    """Return retry delay in minutes for refresh timeouts.

    Timeouts often mean the upstream site was momentarily slow or rate-limiting,
    so the first few retries are short (15m). After that, fall back to the same
    exponential curve as generic errors, capped at 24h.
    """
    failures = max(1, error_count)
    if failures <= TIMEOUT_SHORT_RETRY_ATTEMPTS:
        return TIMEOUT_SHORT_RETRY_MINUTES
    hours = min(2 ** (failures - TIMEOUT_SHORT_RETRY_ATTEMPTS - 1), MAX_SOURCE_RETRY_DELAY_HOURS)
    return hours * 60


def _source_timeout_retry_timestamp(error_count: int, ref_time: datetime = None) -> str:
    ref = ref_time or utcnow()
    retry_at = ref + timedelta(minutes=_source_timeout_retry_delay_minutes(error_count))
    return retry_at.strftime("%Y-%m-%d %H:%M:%S")

# --- Refresh lock ---
_refreshing_count = 0
_current_source_id = None
_parse_done = 0
_parse_total = 0

# --- Generate queue ---
_generate_queue = []  # List of source dicts queued for manual generation
_queue_lock_gen = threading.Lock()


def enqueue_generate(source: dict) -> bool:
    """Add source to the generate queue. Returns False if already queued."""
    with _queue_lock_gen:
        if any(s['id'] == source['id'] for s in _generate_queue):
            return False
        _generate_queue.append(source)
    return True


def dequeue_generate():
    """Pop the next queued source, or None if empty."""
    with _queue_lock_gen:
        return _generate_queue.pop(0) if _generate_queue else None


def get_queued_source_ids() -> list:
    with _queue_lock_gen:
        return [s['id'] for s in _generate_queue]

def is_refreshing() -> bool:
    return _refreshing_count > 0

def set_refreshing(val: bool):
    global _refreshing_count
    if val:
        _refreshing_count += 1
    else:
        _refreshing_count = max(0, _refreshing_count - 1)

def get_current_source_id():
    return _current_source_id

def set_current_source_id(val):
    global _current_source_id
    _current_source_id = val

def get_parse_progress():
    return {"done": _parse_done, "total": _parse_total}

def set_parse_progress(done: int, total: int):
    global _parse_done, _parse_total
    _parse_done = done
    _parse_total = total

# --- Activity Log Ring Buffer ---
_log_entries = deque(maxlen=50)
_log_lock = threading.Lock()
_log_counter = 0


def _log_event(level: str, source_name: str, message: str):
    """Append a structured log entry to the ring buffer."""
    global _log_counter
    with _log_lock:
        _log_counter += 1
        _log_entries.append({
            "id": _log_counter,
            "ts": utcnow().strftime("%H:%M:%S"),
            "level": level,
            "source": source_name,
            "message": message,
        })


def get_activity_log(since_id: int = 0) -> list[dict]:
    """Return log entries with id > since_id (for polling)."""
    with _log_lock:
        result = [e for e in _log_entries if e["id"] > since_id]
        return result


def _extract_nonempty_match(pattern: str, html: str) -> str:
    match = re.search(pattern, html or "", re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    value = BeautifulSoup(match.group(1), "html.parser").get_text(" ", strip=True)
    return value.strip()


def _result_has_meaningful_article_signals(html: str) -> bool:
    title_text = _extract_nonempty_match(r"<title[^>]*>(.*?)</title>", html)
    og_title = _extract_nonempty_match(
        r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)",
        html,
    )
    h1_text = _extract_nonempty_match(r"<h1[^>]*>(.*?)</h1>", html)
    return any((title_text, og_title, h1_text))


def _content_text_length(content: str) -> int:
    return len(BeautifulSoup(content or "", "html.parser").get_text(" ", strip=True))


def _looks_like_blocked_parse(html: str, result: dict) -> bool:
    html_lower = (html or "").lower()
    marker_hits = sum(marker in html_lower for marker in _CHALLENGE_HTML_MARKERS)
    has_challenge_markers = "sec-if-cpt-container" in html_lower or marker_hits >= 2
    if not has_challenge_markers:
        return False

    title = (result.get("title") or "").strip()
    content_len = _content_text_length(result.get("content", ""))
    has_meaningful_title = bool(title and title != "Untitled")

    return (
        not has_meaningful_title
        and content_len < 80
        and not _result_has_meaningful_article_signals(html)
    )


async def _fetch_html(url: str) -> str:
    """Fetch HTML from a URL without blocking the event loop."""
    return await fetch_html_async(url, timeout=15)


def _parse_negative_keywords(raw: str | None) -> list[str]:
    """Split a comma-separated keyword string into a normalized lowercase list."""
    if not raw:
        return []
    return [k.strip().lower() for k in raw.split(',') if k.strip()]


def _title_matches_negative_keywords(title: str | None, keywords: list[str]) -> bool:
    """True if title contains any of the keywords (case-insensitive substring)."""
    if not title or not keywords:
        return False
    lowered = title.lower()
    return any(kw in lowered for kw in keywords)


async def _discover_links_for_source(source: dict, max_articles: int = 50) -> tuple[list, str | None]:
    """Discover article URLs for a feed source using its selectors.

    Returns (links, site_url) where site_url is the feed's canonical site link
    (from the channel <link>) when the source is an RSS/Atom feed, else None.
    """
    url = source['url']
    item_selector = source.get('item_selector')
    link_selector = source.get('link_selector', 'a')
    exclude_selector = source.get('exclude_selector')

    html = await _fetch_html(url)

    if item_selector and not item_selector.startswith(('item > link', 'entry > link')):
        result = await asyncio.to_thread(discovery_service.discover_with_selector, html, url, item_selector, link_selector, exclude_selector)
    else:
        result = await asyncio.to_thread(discovery_service.discover, html, url)

    links = result.get('links', [])

    # Apply exclusion patterns
    exclusion_json = source.get('exclusion_patterns')
    if exclusion_json:
        patterns = json.loads(exclusion_json)
        links = discovery_service.apply_exclusion_patterns(links, patterns)

    # Apply negative keyword title filter (only affects newly discovered links)
    keywords = _parse_negative_keywords(source.get('negative_keywords'))
    if keywords:
        links = [
            l for l in links
            if not _title_matches_negative_keywords(
                l.get('title') if isinstance(l, dict) else None,
                keywords,
            )
        ]

    # Cap discovery to the newest N links from the listing page.
    # Listing pages are conventionally ordered newest-first, so we take the
    # head of the list. Prevents listicle archives (T3 returns 400+ links)
    # from generating unbounded insert/parse work each refresh.
    if len(links) > max_articles:
        links = links[:max_articles]

    return links, result.get('site_url')


async def _parse_article(article: dict, source_name: str = "", translate_to: str = None,
                         content_selector: str = None, title_selector: str = None,
                         date_selector: str = None, image_selector: str = None,
                         content_exclude_selector: str = None, translator: str = 'google',
                         use_parse_date: bool = False) -> bool:
    """Parse a single article with a hard per-article timeout.

    Wraps _parse_article_inner with asyncio.wait_for so one slow article
    (large image-heavy listicle, hung CDN fetch, etc.) cannot exhaust the
    source-level refresh budget and block subsequent articles.
    """
    short_url = article['url'].split('/')[-1][:60] or article['url'][:60]
    try:
        return await asyncio.wait_for(
            _parse_article_inner(
                article,
                source_name=source_name,
                translate_to=translate_to,
                content_selector=content_selector,
                title_selector=title_selector,
                date_selector=date_selector,
                image_selector=image_selector,
                content_exclude_selector=content_exclude_selector,
                translator=translator,
                use_parse_date=use_parse_date,
            ),
            timeout=ARTICLE_PARSE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        msg = f"Article parse timed out after {ARTICLE_PARSE_TIMEOUT_SECONDS}s"
        _log_event("error", source_name, f"{msg}: {short_url}")
        try:
            await db.update_article_failed(article['id'], msg)
        except Exception:
            pass
        return False


async def _parse_article_inner(article: dict, source_name: str = "", translate_to: str = None,
                               content_selector: str = None, title_selector: str = None,
                               date_selector: str = None, image_selector: str = None,
                               content_exclude_selector: str = None, translator: str = 'google',
                               use_parse_date: bool = False) -> bool:
    """Parse a single article. Returns True on success."""
    short_url = article['url'].split('/')[-1][:60] or article['url'][:60]
    try:
        html = await _fetch_html(article['url'])
        current_html = html
        result = await asyncio.to_thread(
            parser.extract_with_images,
            html,
            preferred_image=article.get('main_image'),
            title_selector=title_selector,
            content_selector=content_selector,
            image_selector=image_selector,
            publish_date_selector=date_selector,
            content_exclude_selector=content_exclude_selector,
            base_url=article['url']
        )

        # Client-rendered sites ship no <img> at all, so readability returns text
        # with every image missing. Rebuild from the page's JSON payload instead.
        structured = await asyncio.to_thread(extract_structured_article, html, article['url'])
        if structured and structured.get('content'):
            result = {**result, **{k: v for k, v in structured.items() if v}}

        # If content is very thin, retry with FlareSolverr (JS-rendered)
        content_len = len(result.get('content') or '')
        if content_len < 200:
            try:
                rendered_html = await fetch_html_rendered_async(article['url'])
                rendered_result = await asyncio.to_thread(
                    parser.extract_with_images,
                    rendered_html,
                    preferred_image=article.get('main_image'),
                    title_selector=title_selector,
                    content_selector=content_selector,
                    image_selector=image_selector,
                    publish_date_selector=date_selector,
                    content_exclude_selector=content_exclude_selector,
                    base_url=article['url']
                )
                rendered_content_len = len(rendered_result.get('content') or '')
                if (
                    rendered_content_len > content_len
                    or (
                        _looks_like_blocked_parse(current_html, result)
                        and not _looks_like_blocked_parse(rendered_html, rendered_result)
                    )
                ):
                    current_html = rendered_html
                    result = rendered_result
            except Exception:
                pass

        if _looks_like_blocked_parse(current_html, result):
            raise ValueError("Blocked by anti-bot challenge page")

        title = result.get('title', 'Untitled')
        content = result.get('content', '')
        pub_date = result.get('publish_date', '')
        if use_parse_date:
            pub_date = utcnow().strftime("%Y-%m-%d")
        main_image = result.get('main_image', '')

        # Tagging classifies better on the source language than on machine-translated
        # output, so keep a copy before translation overwrites these. clean_article_body
        # caps it at 600 chars — the only part the classifier ever reads.
        original_title = title
        original_excerpt = clean_article_body(content)

        # Translate if configured
        if translate_to:
            # translate_to is an on/off flag only — the language always comes from
            # the global Options setting, so changing it re-targets every feed.
            sys_settings = await db.get_system_settings()
            target_lang = sys_settings.get('target_language', 'zh-TW')

            _log_event("info", source_name, f"Translating ({target_lang}): {title[:60]}...")
            if translator == "deepseek":
                from ..services.translation import translate_article_deepseek_async
                from ..services.translation_cost import cost_from_usage
                title, content, provider, usage = await translate_article_deepseek_async(title, content, target_language=target_lang)
                if usage:
                    try:
                        await db.log_translation_usage(
                            article.get('source_id'),
                            article.get('url'),
                            usage.get('prompt_cache_hit_tokens', 0) or 0,
                            usage.get('prompt_cache_miss_tokens', 0) or 0,
                            usage.get('completion_tokens', 0) or 0,
                            cost_from_usage(usage),
                        )
                    except Exception as log_err:
                        logger.warning(f"translation usage logging failed: {log_err}")
            else:
                from ..services.translation import translate_text_async, translate_html_async
                title, _ = await translate_text_async(title, target_language=target_lang, translator=translator)
                if content:
                    content, provider = await translate_html_async(content, target_language=target_lang, translator=translator)
            
            if content and provider != "none":
                badge = f'<p style="color:#888;font-size:0.85em;border-bottom:1px solid #ddd;padding-bottom:6px;margin-bottom:12px;">🌐 Translated by {provider}</p>'
                content = badge + content

            try:
                await db.update_article_translation(article['id'], title, content,
                                                   original_title, original_excerpt)
            except Exception as tr_err:
                logger.warning(f"Failed to persist intermediate translation for article {article.get('id')}: {tr_err}")

        content, main_image = await cache_article_images(
            content, main_image, base_url=article.get('url') or ''
        )

        await db.update_article_parsed(article['id'], title, content, pub_date, main_image,
                                       original_title, original_excerpt)
        _log_event("ok", source_name, f"Parsed: {title[:80]}")
        return True
    except Exception as e:
        _log_event("error", source_name, f"Failed: {short_url} — {str(e)[:120]}")
        try:
            await db.update_article_failed(article['id'], str(e)[:500])
        except Exception:
            pass
        return False


async def _refresh_source(source: dict, batch_started_at: datetime = None):
    """Refresh a single feed source: discover links, parse articles, update status."""
    source_id = source['id']
    name = source['name']
    set_current_source_id(source_id)
    _log_event("info", name, "Starting feed refresh")
    logger.info(f"Refreshing source {source_id}: {name}")

    sys_settings = await db.get_system_settings()
    max_articles = sys_settings.get("max_articles_per_feed", 50)
    refresh_interval = float(sys_settings.get("feed_refresh_interval_hours", 1.0))

    try:
        # 2. Insert new articles as pending
        articles, discovered_site_url = await _discover_links_for_source(source, max_articles=max_articles)
        if discovered_site_url and discovered_site_url != source.get('site_url'):
            await db.update_feed_source(source_id, {'site_url': discovered_site_url})
        new_count = await db.insert_feed_articles(source_id, articles)
        if new_count:
            _log_event("info", name, f"{new_count} new articles queued")
            logger.info(f"  {new_count} new articles inserted")

        # 3. Reset failed articles for retry in this cycle
        await db.reset_failed_articles(source_id)

        # 3b. Clean up any legacy backlog from before the discovery cap.
        # In steady state (with the discovery cap above) this is a no-op,
        # but it handles sources that previously accumulated stuck-pending
        # backlog beyond the configured per-source cap.
        early_prunable = await db.get_prunable_articles(source_id, keep=max_articles)
        await db.delete_old_articles(source_id, keep=max_articles)
        if early_prunable:
            await release_cached_images(early_prunable)

        # 4. Parse all pending articles
        translate_to = source.get('translate_to')
        pending = await db.get_pending_articles(source_id=source_id)
        if pending:
            extra = f" (translating to {translate_to})" if translate_to else ""
            _log_event("info", name, f"Parsing {len(pending)} pending articles{extra}...")
        success_count = 0
        fail_count = 0
        set_parse_progress(1, len(pending))
        try:
            for i, article in enumerate(pending):
                ok = await _parse_article(
                    article,
                    source_name=name,
                    translate_to=translate_to,
                    content_selector=source.get('content_selector'),
                    title_selector=source.get('title_selector'),
                    date_selector=source.get('date_selector'),
                    image_selector=source.get('image_selector'),
                    content_exclude_selector=source.get('content_exclude_selector'),
                    translator=source.get('translator', 'google'),
                    use_parse_date=bool(source.get('use_parse_date')),
                )
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                set_parse_progress(i + 1, len(pending))
                await asyncio.sleep(1)  # Rate limiting — yields to event loop
        finally:
            set_parse_progress(0, 0)

        logger.info(f"  Parsed: {success_count} success, {fail_count} failed")

        # 5. Prune old articles
        prunable_articles = await db.get_prunable_articles(source_id, keep=max_articles)
        await db.delete_old_articles(source_id, keep=max_articles)
        await release_cached_images(prunable_articles)

        # 5b. Re-cache expiring signed images that failed to cache on first parse.
        # Some sites (e.g. BANDAI) serve short-lived CloudFront signed image URLs;
        # if the signature expired before the original cache pass ran, the raw URL
        # was kept and now 403s. Re-fetch the page for fresh signatures and cache
        # permanently. SQL-prefiltered, so it is a no-op for sources without signed
        # URLs, and self-limits: once an image is cached it no longer matches.
        for art in await db.get_signed_image_articles(source_id):
            if not content_has_uncached_signed_images(art.get('content'), art.get('main_image')):
                continue
            try:
                page_html = await _fetch_html(art['url'])
                new_content, new_main_image, recached = await recache_signed_images(
                    art.get('content') or '', art.get('main_image') or '', page_html
                )
                if recached:
                    await db.update_article_images(art['id'], new_content, new_main_image)
                    _log_event("ok", name, f"Re-cached {recached} expiring image(s)")
            except Exception as e:
                _log_event("error", name, f"Image re-cache failed: {str(e)[:80]}")
            await asyncio.sleep(1)  # Rate limiting — yields to event loop

        # 6. Update source stats
        stats = await db.get_article_stats(source_id)
        total_success = stats.get('success', 0)
        total_failed = stats.get('failed', 0)

        # Determine status
        if total_success == 0:
            status = 'red'
        elif total_failed > 0 or stats.get('pending', 0) > 0:
            status = 'yellow'
        else:
            status = 'green'

        await db.update_feed_source(source_id, {
            'last_generation_at': 'CURRENT_TIMESTAMP',
            'feed_article_count': total_success,
            'status': status,
            'last_error': None,
            'error_count': 0,  # reset on success
            'next_check_at': None,  # set below via raw SQL
        })

        # Schedule the next check from when this source finished its refresh,
        # so sequential execution of long batches does not cause immediate backlog.
        next_dt = utcnow() + timedelta(minutes=int(refresh_interval * 60))
        next_str = next_dt.strftime("%Y-%m-%d %H:%M:%S")
        db_conn = await db._get_db()
        await db_conn.execute(
            "UPDATE feed_sources SET next_check_at = ? WHERE id = ?",
            (next_str, source_id)
        )
        await db_conn.commit()

        _log_event("ok", name, f"Done — {total_success} articles in feed")

    except Exception as e:
        _log_event("error", name, f"Refresh failed: {str(e)[:150]}")
        logger.error(f"  Failed to refresh source {source_id}: {e}")
        error_count = source.get('error_count', 0) + 1
        retry_at = _source_retry_timestamp(error_count)
        await db.update_feed_source(source_id, {
            'status': 'red',
            'last_error': f"Feed generation failed: {str(e)[:200]}",
            'error_count': error_count,
            'next_check_at': retry_at,
        })


async def _refresh_all_feeds_async():
    """Hourly job: discover links and parse articles for due sources."""
    if is_refreshing():
        _log_event("info", "SCHEDULER", "Skipped — refresh already in progress")
        return
    set_refreshing(True)
    try:
        sources = await db.get_sources_due_for_refresh()
        if not sources:
            _log_event("info", "SCHEDULER", "No sources due for refresh")
            return
        _log_event("info", "SCHEDULER", f"Hourly refresh started — {len(sources)} sources due")
        logger.info(f"Starting hourly feed refresh — {len(sources)} sources due")
        for source in sources:
            try:
                await asyncio.wait_for(
                    _refresh_source(source),
                    timeout=SOURCE_REFRESH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                timeout_minutes = max(1, SOURCE_REFRESH_TIMEOUT_SECONDS // 60)
                error_count = source.get("error_count", 0) + 1
                retry_at = _source_timeout_retry_timestamp(error_count)
                _log_event("error", source["name"], f"Refresh timed out after {timeout_minutes} minutes")
                logger.error(f"  Refresh timed out for source {source['id']}: {source['name']}")
                await db.update_feed_source(source["id"], {
                    "status": "red",
                    "last_error": f"Refresh timed out after {timeout_minutes} minutes",
                    "error_count": error_count,
                    "next_check_at": retry_at,
                })
            except Exception as e:
                _log_event("error", source.get("name", "SOURCE"), f"Refresh error: {str(e)[:150]}")
                logger.error(f"  Unhandled error refreshing source {source.get('id')}: {e}")
        _log_event("ok", "SCHEDULER", f"Hourly refresh complete — {len(sources)} sources processed")
        logger.info("Hourly feed refresh complete")
        await _sweep_orphaned_images_if_due()
    finally:
        set_current_source_id(None)
        set_refreshing(False)


_last_sweep_at: datetime | None = None
SWEEP_INTERVAL_HOURS = 24


async def _sweep_orphaned_images_if_due():
    """Reclaim cache files nothing references, at most once a day.

    Scans every cache file against every article, so it is too heavy to run
    each cycle — but it is the only thing that catches images stranded by a
    parse that was cancelled after caching and before saving.
    """
    global _last_sweep_at
    now = utcnow()
    if _last_sweep_at and (now - _last_sweep_at) < timedelta(hours=SWEEP_INTERVAL_HOURS):
        return
    _last_sweep_at = now
    try:
        removed, freed = await sweep_orphaned_cached_images()
        if removed:
            _log_event("ok", "SCHEDULER", f"Swept {removed} orphaned images ({freed // (1024 * 1024)} MB)")
            logger.info(f"Swept {removed} orphaned cached images, freed {freed} bytes")
        derived_removed, derived_freed = await sweep_derived_image_cache()
        if derived_removed:
            _log_event("ok", "SCHEDULER", f"Swept {derived_removed} derived images ({derived_freed // (1024 * 1024)} MB)")
            logger.info(f"Swept {derived_removed} derived cached images, freed {derived_freed} bytes")
    except Exception as e:
        logger.error(f"Orphan sweep failed: {e}")


async def _retry_failed_articles_async():
    """Every 5 min: retry articles that failed at least once."""
    if is_refreshing():
        _log_event("info", "RETRY", "Skipped retry — refresh already in progress")
        return
    articles = await db.get_retry_articles(max_retry=10)
    if not articles:
        return
    _log_event("info", "RETRY", f"Retrying {len(articles)} failed articles")
    logger.info(f"Retrying {len(articles)} failed articles")
    # Cache source translate_to settings
    source_cache = {}
    for article in articles:
        sid = article['source_id']
        if sid not in source_cache:
            source_cache[sid] = await db.get_feed_source(sid)
        source = source_cache[sid]
        translate_to = source.get('translate_to') if source else None
        await _parse_article(
            article,
            source_name="RETRY",
            translate_to=translate_to,
            content_selector=source.get('content_selector') if source else None,
            title_selector=source.get('title_selector') if source else None,
            date_selector=source.get('date_selector') if source else None,
            image_selector=source.get('image_selector') if source else None,
            content_exclude_selector=source.get('content_exclude_selector') if source else None,
            translator=source.get('translator', 'google') if source else 'google',
            use_parse_date=bool(source.get('use_parse_date')) if source else False,
        )
        await asyncio.sleep(1)  # Rate limiting — yields to event loop


# --- Native asyncio scheduler (replaces APScheduler) ---

async def _scheduler_loop():
    """Main scheduler loop — runs on FastAPI's event loop, triggers hourly."""
    _log_event("ok", "SCHEDULER", "Feed scheduler started (hourly, retry every 5m)")
    logger.info("Feed scheduler started (hourly refresh, retry every 5 min)")
    # Immediate startup refresh for any overdue sources
    try:
        await _refresh_all_feeds_async()
    except Exception as e:
        logger.error(f"Startup refresh error: {e}")
        _log_event("error", "SCHEDULER", f"Startup refresh error: {str(e)[:150]}")
    while True:
        try:
            await asyncio.sleep(900)  # Check for due feeds every 15 minutes
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled — shutting down")
            _log_event("info", "SCHEDULER", "Scheduler loop cancelled")
            return
        try:
            await _refresh_all_feeds_async()
        except asyncio.CancelledError:
            logger.info("Scheduler refresh cancelled — shutting down")
            _log_event("info", "SCHEDULER", "Refresh cancelled")
            return
        except Exception as e:
            logger.error(f"Scheduler refresh error: {e}")
            _log_event("error", "SCHEDULER", f"Refresh error: {str(e)[:150]}")


async def _retry_loop():
    """Retry loop — runs every 5 minutes on FastAPI's event loop."""
    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes
        except asyncio.CancelledError:
            logger.info("Retry loop cancelled — shutting down")
            return
        try:
            await _retry_failed_articles_async()
        except asyncio.CancelledError:
            logger.info("Retry loop cancelled — shutting down")
            return
        except Exception as e:
            logger.error(f"Retry loop error: {e}")
            _log_event("error", "RETRY", f"Retry error: {str(e)[:150]}")


async def _tagging_loop():
    """Tagging backfill loop — runs every 10 minutes on FastAPI's event loop."""
    from .tagging_worker import run_tagging_backfill, backfill_event_labels
    while True:
        try:
            await asyncio.sleep(600)  # 10 minutes
        except asyncio.CancelledError:
            logger.info("Tagging loop cancelled — shutting down")
            return
        try:
            await run_tagging_backfill(limit=200)
            # After tagging, not before: an article tagged in this very cycle should
            # have its earlier events filled in without waiting another 10 minutes.
            await backfill_event_labels()
        except asyncio.CancelledError:
            logger.info("Tagging loop cancelled — shutting down")
            return
        except Exception as e:
            logger.error(f"Tagging loop error: {e}")
            _log_event("error", "TAGGING", f"Tagging error: {str(e)[:150]}")


_started = False


def start_scheduler():
    """Start scheduler tasks on the current event loop."""
    global _started
    if _started:
        return
    _started = True
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_scheduler_loop())
        loop.create_task(_retry_loop())
        loop.create_task(_tagging_loop())
    except RuntimeError:
        # No running event loop (e.g. during tests) — skip scheduling
        logger.warning("No running event loop — scheduler not started")
        _started = False
