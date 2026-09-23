"""On-demand article translation, as a job that outlives its reader.

A phone that locks its screen mid-translation must not cancel the work or lose what
was already paid for, so the translation is a task and the HTTP stream is only a
reader of its event log. Events are appended to a list rather than pushed to a
queue, which is what lets a client that reconnects replay what it missed.

This module knows nothing about HTTP. Events carry raw block markup; rendering them
for a particular client is the route's job.
"""
import asyncio
import logging
import time

from ..database import db
from . import translation

logger = logging.getLogger(__name__)

JOB_RETENTION_SECONDS = 300


class TranslationJob:
    def __init__(self, article_id: int):
        self.article_id = article_id
        self.events: list[dict] = []
        self.done = False
        self.finished_at: float | None = None
        self.task: asyncio.Task | None = None
        self._signal = asyncio.Event()

    def append(self, event: dict):
        self.events.append(event)
        if event["type"] in ("done", "error"):
            self.done = True
            self.finished_at = time.monotonic()
        self._signal.set()
        self._signal = asyncio.Event()

    @property
    def signal(self) -> asyncio.Event:
        return self._signal


_jobs: dict[int, TranslationJob] = {}


def clear():
    _jobs.clear()


def get(article_id: int) -> TranslationJob | None:
    _evict_expired()
    return _jobs.get(article_id)


def _evict_expired():
    now = time.monotonic()
    for article_id, job in list(_jobs.items()):
        if job.done and job.finished_at and now - job.finished_at > JOB_RETENTION_SECONDS:
            del _jobs[article_id]


def start(article: dict, target_language: str, translator: str) -> TranslationJob:
    """Start a translation, or join the one already running for this article."""
    _evict_expired()
    existing = _jobs.get(article["id"])
    if existing is not None and not existing.done:
        return existing

    job = TranslationJob(article["id"])
    _jobs[article["id"]] = job
    job.task = asyncio.create_task(_run(job, article, target_language, translator))
    return job


async def subscribe(job: TranslationJob):
    """Replay the job from its first event, then follow it live."""
    index = 0
    while True:
        waiter = job.signal
        while index < len(job.events):
            yield job.events[index]
            index += 1
        if job.done:
            return
        await waiter.wait()


async def _run(job: TranslationJob, article: dict, target_language: str, translator: str):
    loop = asyncio.get_running_loop()

    def emit(event: dict):
        loop.call_soon_threadsafe(job.append, event)

    try:
        result = await asyncio.to_thread(
            _translate_blocking, article, target_language, translator, emit)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"On-demand translation failed for article {article['id']}: {e}")
        job.append({"type": "error", "message": str(e)[:200]})
        return

    if result is None:
        job.append({"type": "error",
                    "message": "The translation provider returned nothing usable."})
        return

    try:
        await db.save_ondemand_translation(
            article["id"], result["title"], result["content"],
            result["original_title"], result["original_excerpt"])
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to persist on-demand translation for {article['id']}: {e}")
        job.append({"type": "error", "message": "The translation could not be saved."})
        return

    usage = result.get("usage") or {}
    if usage.get("calls"):
        try:
            from .translation_cost import qwen_cost_from_usage
            await db.log_translation_usage(
                article.get("source_id"), article.get("url"), 0,
                usage.get("prompt_tokens", 0) or 0,
                usage.get("completion_tokens", 0) or 0,
                qwen_cost_from_usage(usage),
                provider="qwen", kind="ondemand")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"On-demand usage logging failed: {e}")

    job.append({"type": "done"})


def _translate_blocking(article: dict, target_language: str, translator: str, emit) -> dict | None:
    """Run the translation, emitting each block as it lands. Runs off the event loop."""
    from . import translation_worker
    from .topic_classifier import clean_article_body

    # The legacy "(Translation Error)" marker sits in a <div>, which is not a
    # BLOCK_TAG, so translation can neither translate nor remove it: without this
    # the article ends up badged as translated with the red banner still on top.
    # Not clean_content — its blockquote rebuild cannot tell an interleaved
    # original from an ordinary pull quote, and this path runs on articles that
    # have not been translated.
    source_title = translation_worker.clean_title(article.get("title") or "")
    source_content = translation_worker.strip_error_banner(article.get("content") or "")

    translation._tally_reset()
    emit({"type": "start", "total": None,
          "provider": translation.QWEN_PROVIDER_LABEL if translator == "qwen" else translator})

    translated_title = source_title
    if source_title.strip():
        try:
            translated_title, _ = translation.translate_text(
                source_title, target_language, translator=translator)
            emit({"type": "title", "html": translated_title})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"On-demand title translation failed: {e}")

    final = None
    for event in translation.translate_html_iter(source_content, target_language, translator):
        if event["type"] == "result":
            final = event
        else:
            emit(event)

    if final is None or final["provider"] == "none":
        return None

    return {
        "title": translated_title,
        "content": translation.badge_html(final["provider"]) + final["html"],
        "original_title": source_title,
        "original_excerpt": clean_article_body(source_content),
        "usage": translation._tally_get(),
    }
