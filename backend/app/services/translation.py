"""Translation service using DeepL API with Google Translate fallback."""
import asyncio
import logging
import os
import copy
import difflib
import re

import requests
import time
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"}

# Max characters of wrapped source text packed into a single Google Translate
# request. deep_translator sends the text in the request URL, so the practical
# ceiling is URL length, not Google's nominal 5000-char limit: measured against
# the live endpoint, ~1500 chars succeed and ~2000 fail. Stay well under that.
# Batching many small blocks into one request (instead of one request per block)
# is what keeps long listicles (300+ blocks) inside the per-article timeout.
GOOGLE_BATCH_CHAR_BUDGET = 1400

# Pre-request pause before each Google Translate call, to stay under the free
# endpoint's rate limits. Batching keeps normal sources to a handful of requests,
# so the only high-volume path is the per-block fallback on ultra-dense articles
# (300+ tiny blocks); 0.2s keeps those inside the per-article timeout while
# staying gentle enough to avoid tripping rate limits.
GOOGLE_REQUEST_DELAY_SECONDS = 0.2

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TARGET_LABEL = "Hong Kong Traditional Chinese (zh-HK)"

_DEEPSEEK_FORMAT_PREAMBLE = (
    "You will receive an article with a title and HTML content, wrapped in "
    "[TITLE]...[/TITLE] and [CONTENT]...[/CONTENT] tags. Return the translated "
    "article using the EXACT same tag structure:\n"
    "[TITLE]\n<translated title>\n[/TITLE]\n[CONTENT]\n<translated HTML>\n[/CONTENT]\n\n"
    "Output ONLY the [TITLE]...[/TITLE][CONTENT]...[/CONTENT] block — no "
    "explanations, prefaces, or notes.\n"
    "Preserve all HTML tags inside [CONTENT] exactly as they appear, including "
    "attributes (<a href=\"...\">, <strong>, <em>, <br>, <img>, etc.). Translate "
    "only the visible text inside the tags.\n"
    "Keep brand names, product names, model numbers, and proper nouns in their "
    "original form when commonly done so (e.g., iPhone, Bandai, Amazon, "
    "Microsoft 365).\n"
)

# Only meaningful when the target is Traditional Chinese — kept out of the shared
# preamble so other target languages are not told to avoid Simplified characters.
_DEEPSEEK_TRAD_CHINESE_RULE = (
    "Do NOT use Simplified Chinese characters or Mainland-specific vocabulary "
    "(avoid 视频 / use 影片; avoid 软件 / use 軟件 in Traditional).\n\n"
)

DEEPSEEK_SYSTEM_PROMPT_NATURAL = (
    "You are a professional translator for Hong Kong publications. Translate "
    "Japanese and English text into NATURAL HONG KONG WRITTEN CHINESE — the "
    "register used in HK magazines, blogs, and lifestyle articles (think "
    "Esquire HK, GQ HK, Ming Pao Weekly, modern news features).\n\n"
    + _DEEPSEEK_FORMAT_PREAMBLE
    + _DEEPSEEK_TRAD_CHINESE_RULE +
    "TWO REGISTERS, ONE SIMPLE RULE:\n\n"
    "(1) NARRATION (everything outside quote markers — article body, "
    "descriptions, the journalist's voice): use modern WRITTEN HK CHINESE. "
    "Never use Cantonese-only spoken particles here. This includes:\n"
    "  • Casual JP magazine/blog prose → casual-toned but still written HK. "
    "Keep sentences light and natural, but particles stay written.\n"
    "  • Formal JP news → formal HK written.\n\n"
    "(2) QUOTED DIRECT SPEECH (text inside JP dialogue markers that is "
    "REPORTED SPEECH from a real person): you MAY use casual conversational "
    "HK with light Cantonese verbal particles (啦, 啊, 喎, 㗎, 嘛) where they "
    "would feel natural in actual HK speech. The aim is to make dialogue sound "
    "like a real Hongkonger speaking, not stiff narration.\n\n"
    "JAPANESE DIALOGUE MARKERS to watch for: 「...」 (most common), 『...』, "
    "\"...\", '...', 〝...〟, 《...》. Important: not every quote is dialogue. "
    "Apply the dialogue rule ONLY when the quoted text is REPORTED SPEECH "
    "(attributed to a person via verbs like と言った, と語る, と話す, と述べた, "
    "と答えた, とコメントした, or context makes clear someone is speaking). "
    "If the quoted text is a:\n"
    "  • Work title (e.g., 『アサシンクリード』, 「機動戦士ガンダム」)\n"
    "  • Product / brand / feature name (e.g., 「Find My」, 「Local Mac」)\n"
    "  • Official name of a policy, treaty, programme, framework, or initiative "
    "(e.g., 「持続可能な開發のための2030アジェンダ」, 「パリ協定」, 「働き方改革」)\n"
    "  • Professional / academic / industry term, jargon, methodology, or "
    "concept (e.g., 「ガン=カタ」, 「ディープラーニング」, 「ESG投資」)\n"
    "  • Institutional, organisational, or company name\n"
    "  • Highlighted term, slogan, emphasised phrase, or section heading\n"
    "  • Rhetorical question posed by the article (not by a speaker)\n"
    "→ treat it as NARRATION (strict written HK, formal where appropriate, "
    "NEVER Cantonese particles, NEVER overly casual).\n\n"
    "RULE OF THUMB for borderline cases: if you cannot point to a clear "
    "speaker being quoted (a named person with a speech-verb attribution), "
    "default to NARRATION treatment. Better to render slightly stilted than to "
    "translate a UN policy name as Cantonese spoken slang.\n\n"
    "AVOID BOTH OF THESE EXTREMES IN NARRATION:\n\n"
    "(A) Do NOT use pure Cantonese verbal particles in narration:\n"
    "  嘅 → 的       呢 / 呢個 → 這 / 這個    嗰 / 嗰個 → 那 / 那個\n"
    "  同 (and) → 和 / 與    喺 → 在    咗 → 了    啲 → 些 / 一些\n"
    "  係 → 是    唔 → 不    咁 → 這樣 / 那樣    嚟 → 來    咩 → 甚麼\n\n"
    "(B) Do NOT use classical / wenyan (文言文) constructions — they sound "
    "stilted and foreign in modern HK Chinese:\n"
    "  WRONG (wenyan):   何所指, 是乃, 此乃, 是矣, 焉能, 何以見得, 蓋因, "
    "故曰, 之乎者也\n"
    "  RIGHT (modern HK): 究竟指的是甚麼, 這就是, 因為, 怎麼能, 怎樣才能\n\n"
    "WORKED EXAMPLES:\n\n"
    "Example 1 — narration, casual conversational invitation (NOT a real "
    "quoted speech):\n"
    "  JP source: さあ、ご一緒に。\n"
    "  WRONG (too formal/stiff):    來，讓我們一起。\n"
    "  WRONG (pure Cantonese):      嚟啦，一齊嚟。\n"
    "  RIGHT (natural HK written):  來吧，一起來。\n\n"
    "Example 2 — narration, rhetorical question with quoted work title:\n"
    "  JP source: 「アサクリ」でガン=カタとはどういうことなのか？\n"
    "  WRONG (classical/wenyan):   《刺客教條》中的「槍鬥術」究竟是何所指？\n"
    "  RIGHT (natural HK written): 《刺客教條》中的「槍鬥術」到底是甚麼？\n"
    "  (Note: 「アサクリ」is a work-title quote, NOT dialogue — keep strict.)\n\n"
    "Example 3 — REAL quoted speech (apply casual HK with light particles):\n"
    "  JP source: 田中さんは「みんなで楽しめるゲームを作りたかったんです」と語った。\n"
    "  WRONG (stiff): 田中先生說：「我想做一款大家都能享受的遊戲。」\n"
    "  RIGHT (casual HK dialogue): 田中先生說：「我哋想整一款大家都玩得開心嘅遊戲啦。」\n"
    "  (Note: surrounding narration 「田中先生說」 stays in written HK.)\n\n"
    "Example 4 — official policy / professional term inside quotes (NOT "
    "dialogue — treat as narration, never use Cantonese):\n"
    "  JP source: 国連は「持続可能な開発のための2030アジェンダ」を採択した。\n"
    "  WRONG (Cantonese for the term): 聯合國通過咗「為咗可持續發展嘅2030議程」。\n"
    "  WRONG (casual rendering): 聯合國通過咗「2030年可持續發展計劃啦」。\n"
    "  RIGHT (formal written HK): 聯合國通過了「2030年可持續發展議程」。\n"
    "  (Note: the quoted text is an official UN framework name. It MUST stay "
    "formal and use the standard published Chinese translation if one exists.)\n\n"
    "VOCABULARY — use Hong Kong preferences where they differ from Taiwan:\n"
    "  軟件 (not 軟體), 硬件 (not 硬體), 影片 (not 視頻), 巴士 (not 公車), "
    "的士 (not 計程車), 雪櫃 (not 冰箱), 鐳射 (not 雷射), 平治 (Mercedes), "
    "寶馬 (BMW). Use 甚麼 (not 什麼) and 到底 / 究竟 for question emphasis.\n"
    "CHARACTER PREFERENCES — Hong Kong style:\n"
    "  prefer 着 over 著, prefer 裏 over 裡.\n\n"
    "=== STRUCTURAL PRESERVATION (do not deviate) ===\n\n"
    "Every block element inside [CONTENT]...[/CONTENT] has an HTML attribute\n"
    "data-i=\"N\" where N is an integer index. You MUST preserve this attribute\n"
    "exactly, including the numeric value, on every block you return.\n\n"
    "Rules:\n"
    "1. Do not add, remove, merge, split, or reorder blocks. The number of\n"
    "   blocks you return must equal the number of blocks sent, and the data-i\n"
    "   values must match one-to-one.\n"
    "2. Do not strip the data-i attribute. Do not rename it. Do not change its\n"
    "   value. It is required for downstream layout and is not metadata to\n"
    "   discard.\n"
    "3. Do not introduce new HTML attributes. Do not change tag names. Only\n"
    "   translate the visible text content inside each tag.\n"
    "4. If a block contains text that should not be translated (URLs, code\n"
    "   snippets, brand names already in Latin script, numeric data), keep the\n"
    "   original text as-is, but still return the block with its data-i\n"
    "   attribute intact and unchanged.\n"
    "5. Preserve inline tags (<a>, <strong>, <em>, <span>, <code>) and their\n"
    "   attributes (e.g. href on <a>) inside the translated text."
)

class DeepSeekError(RuntimeError):
    """Raised when the DeepSeek API returns a non-success response or malformed output."""


DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"

# Language code mapping: caller uses zh-TW, DeepL uses ZH-HANT
DEEPL_LANG_MAP = {"zh-TW": "ZH-HANT", "zh-CN": "ZH-HANS"}

# Track whether DeepL quota is exhausted for this process lifetime
_deepl_quota_exhausted = False


def _get_translation_settings() -> dict:
    try:
        from ..database import db
        return db.get_system_settings_sync()
    except Exception:
        return {
            'deepseek_enabled': bool(DEEPSEEK_API_KEY),
            'deepseek_api_key': DEEPSEEK_API_KEY,
            'deepl_enabled': bool(DEEPL_API_KEY),
            'deepl_api_key': DEEPL_API_KEY,
            'target_language': 'zh-TW',
        }


def _translate_deepl(text: str, target_language: str) -> str | None:
    """Translate via DeepL API. Returns None if unavailable or quota exhausted."""
    global _deepl_quota_exhausted
    settings = _get_translation_settings()
    deepl_enabled = settings.get("deepl_enabled", False)
    api_key = settings.get("deepl_api_key") or DEEPL_API_KEY

    if _deepl_quota_exhausted or not deepl_enabled or not api_key:
        return None

    deepl_target = DEEPL_LANG_MAP.get(target_language, target_language.upper())
    try:
        response = requests.post(
            DEEPL_API_URL,
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
            data={"text": text, "target_lang": deepl_target},
            timeout=10,
        )
        if response.status_code == 456:
            _deepl_quota_exhausted = True
            logger.warning("DeepL quota exhausted, falling back to Google Translate")
            return None
        if response.status_code != 200:
            logger.error(f"DeepL API error {response.status_code}: {response.text}")
            return None
        return response.json()["translations"][0]["text"]
    except requests.RequestException as e:
        logger.error(f"DeepL request failed: {e}")
        return None


def _translate_google_direct(text: str, target_language: str, timeout: float = 15.0) -> str:
    """Call the endpoint directly so the request carries a timeout.

    deep_translator's GoogleTranslator wraps a bare requests.get with no timeout; a hang
    there leaks the asyncio.to_thread worker permanently, because the scheduler's outer
    wait_for abandons the coroutine but cannot kill the blocked OS thread.
    """
    if not text or not text.strip():
        return text
    resp = requests.get(
        "https://translate.google.com/m",
        params={"sl": "auto", "tl": target_language, "q": text},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise Exception("Google Translate rate limited (HTTP 429)")
    if resp.status_code != 200:
        raise Exception(f"Google Translate HTTP error {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    element = soup.find("div", class_="result-container") or soup.find("div", class_="t0")
    if not element:
        raise Exception("Google Translate result element not found")
    return element.get_text()


def _translate_google(text: str, target_language: str, max_retries: int = 3, delay_seconds: float = 2.0) -> str:
    """Translate via Google Translate (free, no API key) with retry logic to avoid 500 errors."""
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # Small delay before each request to prevent rate limiting when translating multiple HTML blocks
            if attempt == 0:
                time.sleep(GOOGLE_REQUEST_DELAY_SECONDS)
                
            result = _translate_google_direct(text, target_language, timeout=15.0)
            
            # Google Translate web endpoint sometimes returns its 500 Server Error page text as the translation
            if result and "Error 500 (Server Error)" in result:
                if attempt < max_retries - 1:
                    logger.warning(f"Google Translate returned 500 error text. Retrying in {delay_seconds}s (Attempt {attempt+1}/{max_retries})")
                    time.sleep(delay_seconds)
                    continue
                else:
                    raise Exception("Google Translate returned 500 error text after all retries")
            
            return result
            
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                logger.warning(f"Google Translate exception: {e}. Retrying in {delay_seconds}s (Attempt {attempt+1}/{max_retries})")
                time.sleep(delay_seconds)
            else:
                raise Exception(f"Google Translate failed after {max_retries} attempts. Last error: {last_error}")

    raise Exception(f"Google Translate failed. Last error: {last_error}")


def _translate_google_batch(blocks: list[str], indices: list[int], target_language: str) -> dict[int, str]:
    """Translate one batch of blocks in a single Google request.

    Blocks are wrapped in <div data-i="N"> markers (N is the block's global
    index) so the translated text can be split back apart. Returns a mapping of
    global index -> translated inner HTML, but ONLY if the response round-trips
    cleanly (every marker present). On any mismatch it returns {} so the caller
    falls back to per-block translation for this batch.
    """
    payload = "".join(f'<div data-i="{j}">{blocks[j]}</div>' for j in indices)
    try:
        translated_html = _translate_google(payload, target_language)
    except Exception as e:
        logger.warning(f"Google batch translation failed, will retry per-block: {e}")
        return {}

    soup = BeautifulSoup(translated_html, "html.parser")
    out: dict[int, str] = {}
    for div in soup.find_all(attrs={"data-i": True}):
        try:
            idx = int(div.get("data-i"))
        except (TypeError, ValueError):
            continue
        if idx in indices and idx not in out:
            out[idx] = div.decode_contents()

    # Accept only a clean round-trip: every sent block came back exactly once,
    # and no marker leaked into visible content (a sign Google merged blocks and
    # corrupted their text). Otherwise discard so the caller retries per-block.
    if set(out) != set(indices):
        return {}
    if any("data-i" in text for text in out.values()):
        return {}
    return out


def _translate_blocks_google(blocks: list[str], target_language: str) -> list[str]:
    """Translate a list of block inner-HTML strings via Google, batched.

    Packs blocks into requests under GOOGLE_BATCH_CHAR_BUDGET characters, sending
    each batch as one HTTP call instead of one call per block. Any batch whose
    response does not round-trip cleanly falls back to per-block translation, so
    output is always a correct 1:1, same-order list of translated strings.
    """
    if not blocks:
        return []

    # Greedily pack block indices into batches under the char budget.
    batches: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    for i, block in enumerate(blocks):
        wrapped_len = len(block) + len(f'<div data-i="{i}"></div>')
        if current and current_len + wrapped_len > GOOGLE_BATCH_CHAR_BUDGET:
            batches.append(current)
            current = []
            current_len = 0
        current.append(i)
        current_len += wrapped_len
    if current:
        batches.append(current)

    results: list[str | None] = [None] * len(blocks)
    for batch in batches:
        for idx, text in _translate_google_batch(blocks, batch, target_language).items():
            results[idx] = text

    # Fill any block the batch path could not resolve with a direct per-block call.
    for i, value in enumerate(results):
        if value is None:
            results[i] = _translate_google(blocks[i], target_language)

    return results


# Attributes that carry machine-readable values, not prose. Google's free
# endpoint is a plain-text translator, so any attribute value containing spaces
# or commas (notably srcset) gets its punctuation localised — full-width commas,
# CJK quote brackets — which breaks the URL. alt/title are deliberately absent:
# they are human-readable and should stay translated.
_IMMUTABLE_MARKUP_ATTRS = (
    "src", "srcset", "data-src", "data-srcset", "data-lazy-src",
    "href", "width", "height", "sizes", "poster",
)
_MEDIA_TAGS_TO_REPAIR = ("img", "source", "a", "iframe", "video")
# Thresholds for last-resort URL matching: long enough that a bare scheme+host
# cannot qualify, similar enough to be a corruption rather than a sibling, and a
# margin so a genuine tie is refused instead of guessed.
_URL_MATCH_MIN_LENGTH = 24
_URL_MATCH_MIN_RATIO = 0.72
_URL_MATCH_MIN_MARGIN = 0.06


# Media carries no prose, so sending it to a text translator can only do harm:
# the URL comes back localised or repeated, and the tag is duplicated into the
# translated copy even though the original block already shows it. Stripping it
# from the request also shrinks the payload, which means fewer batches.
_NON_TRANSLATABLE_TAGS = ("img", "picture", "source", "video", "audio", "iframe", "svg", "canvas")


def _strip_media_for_translation(block_html: str) -> str:
    """Remove media tags from a block before it is sent to a translator.

    The original block keeps its media — only the copy handed to the translation
    API loses it, and the translated element is rendered next to the untouched
    original, so nothing disappears from the article.
    """
    if not block_html or "<" not in block_html:
        return block_html
    soup = BeautifulSoup(block_html, "html.parser")
    for tag in soup.find_all(list(_NON_TRANSLATABLE_TAGS)):
        tag.decompose()
    return str(soup)


def _copy_immutable_attrs(original, translated) -> None:
    for attr in _IMMUTABLE_MARKUP_ATTRS:
        if attr in original.attrs:
            translated[attr] = original[attr]
        elif attr in translated.attrs:
            del translated[attr]


def _restore_markup_attributes(original_html: str, translated_html: str) -> str:
    """Copy machine-readable attributes back from the original after translation.

    Translation must only change prose. When the tag sequence survives intact the
    original attributes are authoritative; when the translator also reshuffled
    inline tags, fall back to repairing media tags by position, since a mangled
    src is the only failure a reader actually sees.
    """
    if not translated_html or not translated_html.strip():
        return translated_html
    if "<" not in original_html:
        return translated_html

    original_soup = BeautifulSoup(original_html, "html.parser")
    translated_soup = BeautifulSoup(translated_html, "html.parser")

    original_tags = original_soup.find_all(True)
    translated_tags = translated_soup.find_all(True)

    if [t.name for t in original_tags] == [t.name for t in translated_tags]:
        for original_tag, translated_tag in zip(original_tags, translated_tags):
            _copy_immutable_attrs(original_tag, translated_tag)
        return str(translated_soup)

    for name in _MEDIA_TAGS_TO_REPAIR:
        originals = original_soup.find_all(name)
        translations = translated_soup.find_all(name)
        if not originals or not translations:
            continue
        if len(originals) == len(translations):
            for original_tag, translated_tag in zip(originals, translations):
                _copy_immutable_attrs(original_tag, translated_tag)
            continue
        # Counts diverged — the translator dropped or merged tags. The damaged
        # URL still starts with the real one, so match on that.
        url_attr = "href" if name == "a" else "src"
        for translated_tag in translations:
            match = _match_by_url_prefix(translated_tag.get(url_attr), originals, url_attr)
            if match is not None:
                _copy_immutable_attrs(match, translated_tag)
    return str(translated_soup)


def _match_by_url_prefix(damaged_url: str, candidates: list, url_attr: str):
    """Find the one original whose URL the damaged value unambiguously came from.

    Compares whole URLs rather than shared prefixes: images from one CDN folder
    share almost their entire path, so prefix length cannot tell siblings apart.
    Candidates are de-duplicated first because lazy-load markup repeats the same
    URL across <img> and its <noscript> twin — identical URLs are not ambiguity.
    Refuses on a genuine tie; a wrong image is worse than a broken one.
    """
    if not damaged_url or len(damaged_url) < _URL_MATCH_MIN_LENGTH:
        return None

    by_url: dict[str, object] = {}
    for candidate in candidates:
        value = candidate.get(url_attr)
        if value and value not in by_url:
            by_url[value] = candidate
    if not by_url:
        return None

    scored = sorted(
        (
            (difflib.SequenceMatcher(None, damaged_url, url).ratio(), url)
            for url in by_url
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_ratio, best_url = scored[0]
    if best_ratio < _URL_MATCH_MIN_RATIO:
        return None
    if len(scored) > 1 and best_ratio - scored[1][0] < _URL_MATCH_MIN_MARGIN:
        return None
    return by_url[best_url]


def _interleave_translation(soup: BeautifulSoup, el, translated_text: str) -> None:
    """Replace a block element with <blockquote>original</blockquote> + translation."""
    tag_name = el.name
    original_inner = el.decode_contents()
    blockquote = soup.new_tag("blockquote")
    original_copy = BeautifulSoup(str(el), "html.parser").find(tag_name)
    blockquote.append(original_copy)

    translated_text = _restore_markup_attributes(original_inner, translated_text)

    translated_el = soup.new_tag(tag_name)
    translated_el.append(BeautifulSoup(translated_text, "html.parser"))

    el.replace_with(blockquote)
    blockquote.insert_after(translated_el)


def translate_text(text: str, target_language: str = "zh-TW", translator: str = "google") -> tuple[str, str]:
    """Translate plain text to target language.

    Returns:
        (translated_text, provider) where provider is "DeepL", "Google Translate",
        or "none" if translation failed.
    """
    if not text or not text.strip():
        return text or "", "none"
    try:
        # Standalone text translation (e.g. titles before content is ready) falls back to Google.
        if translator in ("google", "deepseek"):
            return _translate_google(text, target_language), "Google Translate"
            
        # Legacy fallback logic
        result = _translate_deepl(text, target_language)
        if result is not None:
            return result, "DeepL"
        return _translate_google(text, target_language), "Google Translate"
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return text + " (Translation Error)", "none"


def translate_html(html: str, target_language: str = "zh-TW", translator: str = "google") -> tuple[str, str]:
    """Translate HTML content with bilingual interleaving.

    For each block element, the original is wrapped in a <blockquote>
    (for Miniflux-compatible visual distinction) followed by the translated element.

    Handles readability's <html><body><div> wrappers by searching for block
    elements at any depth rather than only direct children.

    Returns:
        (translated_html, provider) where provider is "DeepL", "Google Translate",
        "DeepL + Google Translate" (if both were used), or "none" on failure.
    """
    if not html or not html.strip():
        return html or "", "none"
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Find all block elements with non-empty text (at any depth)
        elements = soup.find_all(list(BLOCK_TAGS))
        elements = [el for el in elements if el.get_text().strip()]

        if not elements:
            return html, "none"

        # Top-level blocks only — skip nested blocks (e.g. <li> inside <blockquote>)
        # and empty shells. These are the units we translate and interleave.
        top_elements = [
            el for el in elements
            if not el.find_parent(list(BLOCK_TAGS)) and el.decode_contents().strip()
        ]
        if not top_elements:
            return html, "none"

        providers_used = set()

        if translator in ("google", "deepseek"):
            # Bypass DeepL on the default Google path; batch to minimize requests.
            originals = [_strip_media_for_translation(el.decode_contents()) for el in top_elements]
            translations = _translate_blocks_google(originals, target_language)
            providers_used.add("Google Translate")
            for el, translated_text in zip(top_elements, translations):
                _interleave_translation(soup, el, translated_text)
        else:
            for el in top_elements:
                original_text = _strip_media_for_translation(el.decode_contents())
                translated_text = _translate_deepl(original_text, target_language)
                if translated_text is not None:
                    providers_used.add("DeepL")
                else:
                    translated_text = _translate_google(original_text, target_language)
                    providers_used.add("Google Translate")
                _interleave_translation(soup, el, translated_text)

        if len(providers_used) > 1:
            provider = "DeepL + Google Translate"
        elif providers_used:
            provider = providers_used.pop()
        else:
            provider = "none"

        return str(soup), provider
    except Exception as e:
        logger.error(f"HTML translation failed: {e}")
        
        try:
            error_soup = BeautifulSoup(html, "html.parser")
            banner = error_soup.new_tag("div")
            banner.string = "(Translation Error)"
            banner["style"] = "color: #c62828; background-color: #ffebee; padding: 10px; margin-bottom: 1em; border-radius: 4px; font-weight: bold; text-align: center;"
            
            if error_soup.body:
                error_soup.body.insert(0, banner)
            else:
                error_soup.insert(0, banner)
            return str(error_soup), "none"
        except Exception:
            # Fallback if soup parsing fails on the error handler
            return "<div style='color: #c62828; background-color: #ffebee; padding: 10px; margin-bottom: 1em; border-radius: 4px; font-weight: bold; text-align: center;'>(Translation Error)</div>" + html, "none"


LANG_NAME_MAP = {
    "zh-TW": "Traditional Chinese (Hong Kong / Taiwan standard)",
    "zh-CN": "Simplified Chinese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "ko": "Korean",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
}


def _get_deepseek_system_prompt(target_lang: str) -> str:
    if target_lang in ("zh-TW", "zh-HK"):
        return DEEPSEEK_SYSTEM_PROMPT_NATURAL
    lang_name = LANG_NAME_MAP.get(target_lang, target_lang)
    return (
        f"You are a professional translator. Translate foreign text into NATURAL, FLUENT {lang_name}.\n\n"
        + _DEEPSEEK_FORMAT_PREAMBLE
    )


def _translate_deepseek_article(title: str, content_html: str, target_language: str = "zh-TW") -> tuple[str, str, dict]:
    """Translate title + content HTML in a single DeepSeek call.
    
    Uses HTML cleansing to optimize token usage and prevent output limit truncation.
    Returns (translated_title, translated_content_html, usage_dict).
    Raises DeepSeekError on HTTP errors, missing tags, or block count mismatch.
    """
    settings = _get_translation_settings()
    deepseek_enabled = settings.get("deepseek_enabled", False)
    api_key = settings.get("deepseek_api_key") or DEEPSEEK_API_KEY

    if not deepseek_enabled or not api_key:
        raise DeepSeekError("DeepSeek Translation is disabled or DEEPSEEK_API_KEY is not set.")

    soup = BeautifulSoup(content_html, "html.parser")
    elements = soup.find_all(list(BLOCK_TAGS))
    elements = [el for el in elements if el.get_text().strip()]

    clean_blocks = []
    sent_count = 0

    inline_tags = {"a", "strong", "em", "span", "code"}
    tags_to_decompose = {"img", "iframe", "noscript", "video", "audio", "source", "picture"}

    for el in elements:
        if el.find_parent(list(BLOCK_TAGS)):
            continue

        el_copy = copy.deepcopy(el)
        el_copy.attrs = {"data-i": str(sent_count)}

        for tag in el_copy.find_all(True):
            if tag.name in tags_to_decompose:
                tag.decompose()
            elif tag.name not in inline_tags:
                tag.unwrap()
            else:
                href = tag.get("href")
                tag.attrs = {}
                if tag.name == "a" and href:
                    tag["href"] = href

        clean_blocks.append(str(el_copy))
        sent_count += 1

    cleansed_html = "\n".join(clean_blocks)

    user_msg = (
        f"[TITLE]\n{title or ''}\n[/TITLE]\n"
        f"[CONTENT]\n{cleansed_html or ''}\n[/CONTENT]"
    )

    system_prompt = _get_deepseek_system_prompt(target_language)

    try:
        r = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
                "stream": False,
                "max_tokens": 8192
            },
            timeout=120,
        )
    except requests.RequestException as e:
        raise DeepSeekError(f"network error calling DeepSeek: {e}") from e

    if r.status_code != 200:
        body = r.text[:500]
        raise DeepSeekError(f"HTTP {r.status_code} from DeepSeek: {body}")

    try:
        data = r.json()
        choice = data["choices"][0]
        text = choice["message"]["content"]
        usage = data.get("usage", {}) or {}
        finish_reason = choice.get("finish_reason", "unknown")
    except (ValueError, KeyError, IndexError) as e:
        raise DeepSeekError(f"malformed DeepSeek response: {e}") from e

    title_match = re.search(r"\[TITLE\]\s*(.*?)\s*\[/TITLE\]", text, re.DOTALL)
    content_match = re.search(r"\[CONTENT\]\s*(.*?)\s*\[/CONTENT\]", text, re.DOTALL)

    if not title_match or not content_match:
        snippet = text[:300]
        raise DeepSeekError(
            f"DeepSeek response missing [TITLE]/[CONTENT] tags (finish_reason={finish_reason}). "
            f"Start snippet: {snippet!r}"
        )

    translated_title = title_match.group(1).strip()
    translated_content = content_match.group(1).strip()

    # Verify block count matches
    returned_soup = BeautifulSoup(translated_content, "html.parser")
    returned_elements = returned_soup.find_all(list(BLOCK_TAGS))
    returned_blocks = []
    for el in returned_elements:
        if el.find_parent(list(BLOCK_TAGS)):
            continue
        returned_blocks.append(el)

    returned_count = len(returned_blocks)
    if returned_count != sent_count:
        raise DeepSeekError(
            f"block count mismatch: sent {sent_count} blocks, but DeepSeek returned {returned_count} blocks."
        )

    return translated_title, translated_content, usage


def translate_article_deepseek(title: str, content_html: str, target_language: str = "zh-TW") -> tuple[str, str, str, dict]:
    """Translate title and HTML content using DeepSeek with fallback to Google Translate.

    Returns (translated_title, translated_content, provider, usage). `usage` is the
    DeepSeek API usage dict, and is {} on the empty/fallback/error paths.
    """
    if not content_html or not content_html.strip():
        return title, content_html, "none", {}

    try:
        t_title, t_content, usage = _translate_deepseek_article(title, content_html, target_language)

        soup = BeautifulSoup(content_html, "html.parser")
        orig_elements = soup.find_all(list(BLOCK_TAGS))
        orig_elements = [el for el in orig_elements if el.get_text().strip()]

        top_orig_elements = []
        for el in orig_elements:
            if el.find_parent(list(BLOCK_TAGS)):
                continue
            top_orig_elements.append(el)

        returned_soup = BeautifulSoup(t_content, "html.parser")
        returned_elements = returned_soup.find_all(list(BLOCK_TAGS))
        top_returned_elements = []
        for el in returned_elements:
            if el.find_parent(list(BLOCK_TAGS)):
                continue
            top_returned_elements.append(el)

        if len(top_orig_elements) != len(top_returned_elements):
            raise DeepSeekError(
                f"block count mismatch during stitching: original has {len(top_orig_elements)} blocks, "
                f"returned has {len(top_returned_elements)} blocks."
            )

        # Iterate backwards to preserve indexing while modifying tree
        for i in range(len(top_orig_elements) - 1, -1, -1):
            orig_el = top_orig_elements[i]
            ret_el = top_returned_elements[i]

            blockquote = soup.new_tag("blockquote")
            orig_copy = copy.deepcopy(orig_el)
            blockquote.append(orig_copy)

            translated_el = soup.new_tag(orig_el.name)
            translated_el.attrs = copy.deepcopy(orig_el.attrs)
            if "data-i" in translated_el.attrs:
                del translated_el.attrs["data-i"]

            translated_el.clear()
            repaired = _restore_markup_attributes(orig_el.decode_contents(), ret_el.decode_contents())
            for child in BeautifulSoup(repaired, "html.parser").contents:
                translated_el.append(copy.deepcopy(child))

            orig_el.replace_with(blockquote)
            blockquote.insert_after(translated_el)

        return t_title, str(soup), "DeepSeek", usage

    except Exception as e:
        logger.warning(f"DeepSeek translation failed, falling back to Google: {e}")
        try:
            fallback_title, _ = translate_text(title, target_language, translator="google")
            fallback_content, _ = translate_html(content_html, target_language, translator="google")
            return fallback_title, fallback_content, "Google Translate (fallback after DeepSeek failed)", {}
        except Exception as fb_err:
            logger.error(f"Fallback translation failed: {fb_err}")
            return title + " (Translation Error)", content_html, "none", {}


async def translate_text_async(text: str, target_language: str = "zh-TW", translator: str = "google") -> tuple[str, str]:
    """Async wrapper for translate_text — runs in thread pool to avoid blocking event loop."""
    return await asyncio.to_thread(translate_text, text, target_language, translator)


async def translate_html_async(html: str, target_language: str = "zh-TW", translator: str = "google") -> tuple[str, str]:
    """Async wrapper for translate_html — runs in thread pool to avoid blocking event loop."""
    return await asyncio.to_thread(translate_html, html, target_language, translator)


async def translate_article_deepseek_async(title: str, content_html: str, target_language: str = "zh-TW") -> tuple[str, str, str, dict]:
    """Async wrapper for translate_article_deepseek — runs in thread pool to avoid blocking event loop."""
    return await asyncio.to_thread(translate_article_deepseek, title, content_html, target_language)

