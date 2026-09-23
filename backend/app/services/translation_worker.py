"""Deferred translation with the local model.

The scheduler gives each article 240 seconds and each source 12 minutes. LMT-60-1.7B
needs about 8.5 seconds per sentence, which puts a normal 22-block article at roughly
four and a half minutes — so when a remote provider fails, the article is stored
untranslated and flagged, and this worker finishes the job afterwards, off the
refresh path.

It picks up two kinds of row:

  - articles flagged `translation_pending` because their provider was down
  - articles still carrying the "(Translation Error)" marker the dead Google endpoint
    wrote between ~2026-09-14 and the Qwen cutover

Both get the same treatment: recover the Japanese source, translate it with the local
model, interleave it the way the scheduler does, and badge it with what failed.
"""
import copy
import logging
import re

from bs4 import BeautifulSoup

from . import translation

logger = logging.getLogger(__name__)

MARKER = "(Translation Error)"
TITLE_MARKER = re.compile(r"\s*[（(]\s*Translation Error\s*[)）]\s*$", re.I)


def clean_title(title: str) -> str:
    out = (title or "").strip()
    while True:
        stripped = TITLE_MARKER.sub("", out).strip()
        if stripped == out:
            return out
        out = stripped


def strip_error_banner(html: str) -> str:
    """Remove the red "(Translation Error)" banner and any translation badge.

    Safe on any body, translated or not: it removes only those two elements and
    leaves the rest of the document alone. Callers that know the body is an
    interleaved translation want clean_content instead.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")

    for div in soup.find_all("div"):
        if div.get_text(strip=True) == MARKER:
            div.decompose()
    for p in soup.find_all("p"):
        if "Translated by" in p.get_text():
            p.decompose()
    return str(soup).strip()


def clean_content(html: str) -> str:
    """Recover the source text from whatever state the body is in.

    Three shapes turn up: a clean untranslated body, a body with the red error banner
    prepended, and a body already interleaved as <blockquote>original</blockquote>
    followed by its translation. The last one matters — re-translating a translation
    compounds every error it already has, so the originals are read back out.

    Only call this on a body known to be damaged or already translated. The
    blockquote rebuild below cannot tell an interleaved original from an ordinary
    pull quote, so on a normal article it would discard everything but the quote.
    """
    if not html:
        return ""
    soup = BeautifulSoup(strip_error_banner(html), "html.parser")

    blockquotes = soup.find_all("blockquote")
    if blockquotes:
        rebuilt = BeautifulSoup("", "html.parser")
        for bq in blockquotes:
            for child in bq.contents:
                rebuilt.append(copy.deepcopy(child))
        return str(rebuilt).strip()
    return str(soup).strip()


def badge_html(provider: str) -> str:
    return translation.badge_html(provider)


def needs_work(row: dict) -> bool:
    """Is this row something the worker should pick up?"""
    content = row.get("content") or ""
    if not content.strip():
        return False
    if "Translated by" in content[:400]:
        return False
    if row.get("translation_pending"):
        return True
    return MARKER in (row.get("title") or "") or MARKER in content


def translate_article(row: dict, target_language: str = "zh-TW") -> dict | None:
    """Translate one article with the local model.

    Returns the fields to persist, or None when this article has nothing to
    translate — no text blocks, or a model that handed every block straight back.
    None means the row is hopeless and should leave the queue.

    Raises LMTError when the server is unreachable. That is a different failure: the
    article is fine and must stay queued for the next pass.
    """
    source_title = clean_title(row.get("original_title") or row.get("title"))
    source_content = clean_content(row.get("content"))
    if not source_content.strip():
        return None

    source_language = (row.get("detected_language") or "ja").split("-")[0]
    note = row.get("translation_note")
    provider = translation.lmt_fallback_label(*note.split(" — ", 1)) if note and " — " in note \
        else (translation.lmt_fallback_label(note, "") if note else translation.LMT_PROVIDER_LABEL)

    soup = BeautifulSoup(source_content, "html.parser")
    elements = [el for el in soup.find_all(list(translation.BLOCK_TAGS))
                if el.get_text().strip() and not el.find_parent(list(translation.BLOCK_TAGS))]
    if not elements:
        return None

    # LMTError is deliberately not caught. The caller has to tell "this article has
    # nothing to translate" (None, take it out of the queue) from "the server is
    # gone" (raise, leave every row where it is).
    title_out = translation._lmt_block(source_title, target_language, source_language)
    originals = [translation._strip_media_for_translation(el.decode_contents())
                 for el in elements]
    translations = translation._translate_blocks_lmt(
        originals, target_language, source_language)

    applied = 0
    for el, original, translated in zip(elements, originals, translations):
        if not translated or translated.strip() == original.strip():
            continue
        translation._interleave_translation(soup, el, translated)
        applied += 1
    if not applied:
        logger.warning(f"LMT returned nothing usable for article {row.get('id')}")
        return None

    if translation._is_traditional_target(target_language):
        title_out = translation.hk_glossary.localize(title_out)

    return {
        "id": row.get("id"),
        "title": title_out or source_title,
        "content": badge_html(provider) + str(soup),
        "original_title": source_title,
        "provider": provider,
        "blocks": applied,
    }
