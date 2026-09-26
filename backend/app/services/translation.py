"""Translation service: Qwen-MT-Flash (default) and DeepSeek, selectable per feed.

Google Translate was removed on 2026-09-21. Its free endpoint (translate.google.com/m)
has been CAPTCHA-walled since ~2026-09-14 and returns HTTP 429 with a /sorry/ redirect
for every request, which silently wrote "(Translation Error)" into 38 feeds.
"""
import asyncio
from collections.abc import Iterator
import copy
import difflib
import html as html_lib
import logging
import os
import re
import threading
import time

from bs4 import BeautifulSoup
import requests

from ..utils import hk_glossary

logger = logging.getLogger(__name__)

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"}

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://maas.qwencloudapi.com/compatible-mode/v1")
QWEN_URL = QWEN_BASE_URL.rstrip("/") + "/chat/completions"
QWEN_MT_MODEL = os.getenv("QWEN_MT_MODEL", "qwen-mt-flash")
QWEN_PROVIDER_LABEL = "Qwen-MT-flash"

BADGE_STYLE = ("color:#888;font-size:0.85em;border-bottom:1px solid #ddd;"
               "padding-bottom:6px;margin-bottom:12px;")


def badge_html(provider: str) -> str:
    """The line an article carries to say what translated it."""
    return f'<p style="{BADGE_STYLE}">\U0001F310 Translated by {provider}</p>'


# Qwen-MT takes no system prompt and caps input at 8k tokens, so blocks are packed
# into marker-wrapped batches instead of sent one per request. 2,400 characters of
# source keeps a batch comfortably inside the cap even for CJK.
QWEN_BATCH_CHAR_BUDGET = 2400

# Google Translate, restored 2026-09-21 for second-tier feeds. This is the endpoint
# googletrans calls, NOT the translate.google.com/m page the old implementation
# scraped — that one has answered 429 with a /sorry/ redirect since ~2026-09-14.
# Free, no key, and unofficial: it can be walled off the same way without notice.
GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
GOOGLE_PROVIDER_LABEL = "Google Translate"

# The client id decides whether this endpoint answers at all. "gtx" — what
# googletrans and deep_translator send — is throttled hard: measured 2026-09-21, it
# served ~60 calls and then returned 429 /sorry/ for every request for over five
# minutes. "dict-chrome-ex" is the id Google's own Translate extension uses, and in
# the same minute that gtx was walled it took 30 batched calls in 8.3s with no
# failures, from the same IP. Same response shape, so only this string changes.
GOOGLE_CLIENT = os.getenv("GOOGLE_TRANSLATE_CLIENT", "dict-chrome-ex")
GOOGLE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# The source text goes in the POST body. A GET carries it in the query string, which
# 400s past roughly 3k characters of CJK, and measured live is also slower: 4.6s for a
# batch that POST returns in 0.6s. With the URL ceiling gone the budget is set by how
# much the endpoint will translate in one pass — 3,000 characters round-tripped 40/40
# markers intact in testing.
GOOGLE_BATCH_CHAR_BUDGET = 3000

# Minimum spacing between Google calls, process-wide. The endpoint is free and
# unmetered but not unlimited: a backfill running flat out beside a refresh-all walled
# the IP on 2026-09-21, and the live scheduler then translated into 429s. Second-tier
# traffic is ~410 articles/day at 1-2 calls each, so one call every 2 seconds is far
# more headroom than the feeds need and keeps bursts from looking like an attack.
GOOGLE_MIN_INTERVAL_SECONDS = float(os.getenv("GOOGLE_MIN_INTERVAL_SECONDS", "2.0"))

# When the wall does appear, stop knocking. Retrying into a 429 wastes the scheduler's
# time and, from what the /m endpoint did, deepens the block. Each wall hit without an
# intervening success doubles the wait, up to the ceiling.
GOOGLE_COOLDOWN_SECONDS = float(os.getenv("GOOGLE_COOLDOWN_SECONDS", "900"))
GOOGLE_COOLDOWN_MAX_SECONDS = float(os.getenv("GOOGLE_COOLDOWN_MAX_SECONDS", "7200"))

_google_gate = threading.Lock()
_google_last_call = 0.0
_google_cooldown_until = 0.0
_google_consecutive_walls = 0


def _google_throttle_reset():
    """Clear the pacing state. For tests, and for an operator forcing a retry."""
    global _google_last_call, _google_cooldown_until, _google_consecutive_walls
    with _google_gate:
        _google_last_call = 0.0
        _google_cooldown_until = 0.0
        _google_consecutive_walls = 0


def _google_cooldown_remaining() -> float:
    with _google_gate:
        return max(0.0, _google_cooldown_until - time.monotonic())


def _google_enter_cooldown():
    """Called when the endpoint returns 429."""
    global _google_cooldown_until, _google_consecutive_walls
    with _google_gate:
        _google_consecutive_walls += 1
        wait = min(GOOGLE_COOLDOWN_SECONDS * (2 ** (_google_consecutive_walls - 1)),
                   GOOGLE_COOLDOWN_MAX_SECONDS)
        _google_cooldown_until = time.monotonic() + wait
    logger.warning("Google Translate walled us; pausing that provider for %.0f minutes",
                   wait / 60)


def _google_wait_turn():
    """Block until this thread may make the next call, or raise if cooling down.

    The slot is reserved inside the lock and the sleep happens outside it, so
    concurrent callers queue up rather than all waking onto the same instant.
    """
    global _google_last_call
    with _google_gate:
        now = time.monotonic()
        if now < _google_cooldown_until:
            raise GoogleError(
                f"Google Translate is cooling down after a 429 for another "
                f"{_google_cooldown_until - now:.0f}s")
        wait = (_google_last_call + GOOGLE_MIN_INTERVAL_SECONDS) - now
        _google_last_call = now if wait <= 0 else _google_last_call + GOOGLE_MIN_INTERVAL_SECONDS
    if wait > 0:
        time.sleep(wait)

# LMT-60-1.7B (NiuTrans, Apache-2.0), running locally on the Pi through llama.cpp
# behind an OpenAI-compatible server. It is the last resort under every remote
# provider: it cannot be rate limited, walled or billed, and it is the only
# translator here that keeps working when the network does not.
#
# It is also slow — 8.5s for one sentence, measured from the container — so it backs
# up titles inline and bodies through the deferred worker, never inside the
# scheduler's 240s per-article budget.
# No default: this is a machine on someone's own network, and baking one in
# would both publish that address and point every other installation at it.
# Unset means the provider is simply unavailable, which every caller handles.
LMT_URL = os.getenv("LMT_URL", "")
LMT_MODEL = os.getenv("LMT_MODEL", "LMT-60-1.7B")
LMT_PROVIDER_LABEL = "LMT-60-1.7B"
LMT_TIMEOUT_SECONDS = float(os.getenv("LMT_TIMEOUT_SECONDS", "120"))

# The model takes language NAMES, not codes: the repo's own table maps zh -> "Chinese"
# and yue -> "Yue Chinese", and there is no "cht" (that is NiuTrans's commercial API).
# Measured: "Chinese" returns Simplified, "Traditional Chinese" returns Traditional,
# "Yue Chinese" returns real Cantonese, and "cht" returns the Japanese back.
LMT_LANG_MAP = {
    "zh-TW": "Traditional Chinese",
    "zh-HK": "Traditional Chinese",
    "zh-HK-yue": "Yue Chinese",
    "yue": "Yue Chinese",
    "zh-CN": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
}
LMT_SOURCE_MAP = {"ja": "Japanese", "ja-jp": "Japanese", "en": "English",
                  "ko": "Korean", "zh": "Chinese"}

# Qwen-MT names its targets in English; it rejects "Chinese (Traditional)" and
# "Taiwanese Mandarin" with a 400.
QWEN_LANG_MAP = {
    "zh-TW": "Traditional Chinese",
    "zh-HK": "Traditional Chinese",
    "zh-CN": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
}

CHINESE_LANGUAGES = {"zh", "zh-tw", "zh-cn", "zh-hk", "zh-hant", "zh-hans"}

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

class GoogleError(RuntimeError):
    """Raised when the Google endpoint errors or returns something unparseable."""


class LMTError(RuntimeError):
    """Raised when the local LMT-60 server is unreachable or errors."""


class QwenError(RuntimeError):
    """Raised when the Qwen-MT API returns a non-success response."""


def _is_traditional_target(target_language: str) -> bool:
    return target_language in ("zh-TW", "zh-HK")


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
            logger.warning("DeepL quota exhausted, falling back to Qwen-MT")
            return None
        if response.status_code != 200:
            logger.error(f"DeepL API error {response.status_code}: {response.text}")
            return None
        return response.json()["translations"][0]["text"]
    except requests.RequestException as e:
        logger.error(f"DeepL request failed: {e}")
        return None


def _qwen_settings() -> tuple[bool, str]:
    settings = _get_translation_settings()
    key = settings.get("qwen_api_key") or QWEN_API_KEY
    enabled = settings.get("qwen_enabled", bool(key))
    return bool(enabled), key


# Qwen-MT reports usage per request and an article takes several, so the provider
# functions accumulate into this (thread-local) tally rather than returning one dict.
_usage_tally = threading.local()


def _tally_reset():
    _usage_tally.value = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def _tally_add(usage: dict):
    current = getattr(_usage_tally, "value", None)
    if current is None:
        return
    current["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
    current["completion_tokens"] += usage.get("completion_tokens", 0) or 0
    current["calls"] += 1


def _tally_get() -> dict:
    return dict(getattr(_usage_tally, "value", None) or {})


def _qwen_call(text: str, target_language: str, max_retries: int = 3) -> tuple[str, dict]:
    """One Qwen-MT request. Returns (translated_text, usage)."""
    enabled, api_key = _qwen_settings()
    if not enabled or not api_key:
        raise QwenError("Qwen translation is disabled or QWEN_API_KEY is not set.")

    target = QWEN_LANG_MAP.get(target_language, target_language)
    body = {
        "model": QWEN_MT_MODEL,
        "messages": [{"role": "user", "content": text}],
        "translation_options": {"source_lang": "auto", "target_lang": target},
    }
    last = None
    for attempt in range(max_retries):
        try:
            r = requests.post(
                QWEN_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=90,
            )
            if r.status_code == 200:
                data = r.json()
                return (data["choices"][0]["message"]["content"],
                        data.get("usage", {}) or {})
            last = f"HTTP {r.status_code}: {r.text[:160]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except requests.RequestException as e:
            last = str(e)
        if attempt < max_retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise QwenError(f"Qwen-MT request failed: {last}")


def _parse_qwen_batch(out: str, indices: list[int]) -> dict[int, str]:
    """Split a marker-wrapped batch response back into per-block translations.

    Returns {} unless every marker sent came back exactly once, so the caller can
    fall back to one call per block.
    """
    got: dict[int, str] = {}
    current = None
    for line in out.split("\n"):
        m = re.match(r"^\s*\[(\d+)\]\s*(.*)$", line)
        if m:
            current = int(m.group(1))
            if current in got:
                return {}
            got[current] = m.group(2).strip()
        elif current is not None and line.strip():
            got[current] += " " + line.strip()
    if set(got) != set(indices):
        return {}
    return got


def _qwen_block(text: str, target_language: str) -> str:
    """Translate one block, repairing a Simplified response."""
    out, usage = _qwen_call(text, target_language)
    _tally_add(usage)
    if _is_traditional_target(target_language) and hk_glossary.looks_simplified(out):
        try:
            second, retry_usage = _qwen_call(text, target_language)
            _tally_add(retry_usage)
            out = second if not hk_glossary.looks_simplified(second) else hk_glossary.force_traditional(second)
        except QwenError:
            out = hk_glossary.force_traditional(out)
    return out


def _translate_blocks_qwen_iter(blocks: list[str], target_language: str) -> Iterator[tuple[list[int], list[str]]]:
    """Translate block inner-HTML strings via Qwen-MT, yielding (indices, translations) per batch."""
    if not blocks:
        return

    batches: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    for i, block in enumerate(blocks):
        wrapped = len(block) + 8
        if current and current_len + wrapped > QWEN_BATCH_CHAR_BUDGET:
            batches.append(current)
            current, current_len = [], 0
        current.append(i)
        current_len += wrapped
    if current:
        batches.append(current)

    traditional = _is_traditional_target(target_language)
    for batch in batches:
        payload = "\n".join(f"[{j}] {blocks[j]}" for j in batch)
        parsed: dict[int, str] = {}
        try:
            out, usage = _qwen_call(payload, target_language)
            _tally_add(usage)
            parsed = _parse_qwen_batch(out, batch)
            if parsed and traditional and hk_glossary.looks_simplified(out):
                logger.warning("Qwen-MT returned Simplified for a batch of %d; splitting", len(batch))
                parsed = {}
        except QwenError as e:
            logger.warning(f"Qwen batch failed, retrying per block: {e}")

        if parsed:
            batch_translations = [parsed[idx] for idx in batch]
        else:
            batch_translations = []
            for j in batch:
                try:
                    batch_translations.append(_qwen_block(blocks[j], target_language))
                except QwenError as e:
                    logger.error(f"Qwen block translation failed: {e}")
                    batch_translations.append(blocks[j])

        if traditional:
            batch_translations = [hk_glossary.localize(r) for r in batch_translations]
        yield batch, batch_translations


def _translate_blocks_qwen(blocks: list[str], target_language: str) -> list[str]:
    """Translate block inner-HTML strings via Qwen-MT, batched.

    A thin drain of _translate_blocks_qwen_iter; the batching lives there so the
    on-demand path can stream each batch as it lands.
    """
    out = list(blocks)
    for indices, translations in _translate_blocks_qwen_iter(blocks, target_language):
        for i, translated in zip(indices, translations):
            out[i] = translated
    return out


def lmt_fallback_label(primary_provider: str, reason: str) -> str:
    """The badge an article carries when the local model rescued it.

    Names what failed and why, because "translated by the slow local model" is only
    useful if you can see which provider dropped it.
    """
    reason = re.sub(r"<[^>]*>", " ", reason or "")
    reason = re.sub(r"\s+", " ", reason).strip(" :;,")
    if len(reason) > 48:
        reason = reason[:48].rstrip() + "…"
    if not reason:
        return f"{LMT_PROVIDER_LABEL} (fell back from {primary_provider})"
    return f"{LMT_PROVIDER_LABEL} (fell back from {primary_provider} — {reason})"


def _lmt_call(text: str, target_language: str, source_language: str = "ja",
              max_retries: int = 2) -> str:
    """One line through the local model. Returns the translated line."""
    if not text or not text.strip():
        return text

    target = LMT_LANG_MAP.get(target_language, "Traditional Chinese")
    source = LMT_SOURCE_MAP.get((source_language or "ja").lower(), "Japanese")
    prompt = (f"Translate the following text from {source} into {target}:\n"
              f"{source}: {text}\n{target}:")
    body = {"model": LMT_MODEL, "temperature": 0.0, "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}]}

    if not LMT_URL:
        raise LMTError("LMT_URL is not set; the local model is not configured")

    last = None
    for attempt in range(max_retries):
        try:
            r = requests.post(LMT_URL, json=body, timeout=LMT_TIMEOUT_SECONDS)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            last = str(e)[:120]
        if attempt < max_retries - 1:
            time.sleep(2)
    raise LMTError(f"LMT request failed: {last}")


def _lmt_sanitize(html: str) -> str:
    """Reduce a block to what the local model handles without damaging it.

    Measured against the live server: `<a href>` round-trips intact, `<strong>` is
    silently dropped, and `<span id="...">` is echoed into the visible text as
    `< span id="jin_huawo">` — three articles carried that leak on 2026-09-23.
    So links keep their href and everything else is unwrapped to its text. The link
    round-trip turned out to fail too, which _lmt_repair_links handles on the way out.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        if tag.name == "a" and tag.get("href"):
            tag.attrs = {"href": tag["href"]}
        else:
            tag.unwrap()
    return str(soup).strip()


_LMT_QUOTES = "\"'“”‘’＂"
_LMT_LINK_TAG = re.compile(
    r"(?:<|&lt;)\s*(?:"
    r"(?P<close>/\s*a\s*)"
    r"|a\s+href\s*=\s*[" + _LMT_QUOTES + r"]?(?P<url>[^" + _LMT_QUOTES + r"<>\n]*)"
    r"\s*[" + _LMT_QUOTES + r"]?[^<>\n]*?"
    r")(?:>|&gt;)",
    re.IGNORECASE,
)


def _lmt_repair_links(text: str) -> str:
    """Rebuild the link tags the local model echoes back as text.

    The link round-trip in _lmt_sanitize is not reliable: articles showed
    `< a href = “ /magazine/article/100/ ” >` as visible text, spaced out
    and with typographic quotes, which html.parser keeps as text. A tag that pairs
    with a closer is rebuilt so _restore_markup_attributes can put the original href
    back; an unpaired one is dropped, because html.parser would otherwise stretch the
    link to the end of the block.
    """
    if not text or ("<" not in text and "&lt;" not in text):
        return text

    matches = list(_LMT_LINK_TAG.finditer(text))
    if not matches:
        return text

    replacement: dict[int, str] = {}
    pending = None
    for i, m in enumerate(matches):
        replacement[i] = ""
        if m.group("close") is None:
            pending = i
        elif pending is not None:
            opener = matches[pending]
            if text[opener.end():m.start()].strip():
                url = re.sub(r"\s+", "", opener.group("url") or "")
                replacement[pending] = f'<a href="{html_lib.escape(url, quote=True)}">'
                replacement[i] = "</a>"
            pending = None

    out, last = [], 0
    for i, m in enumerate(matches):
        out.append(text[last:m.start()])
        out.append(replacement[i])
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _lmt_block(text: str, target_language: str, source_language: str = "ja") -> str:
    """Translate one block.

    LMT's prompt template is line-delimited — a newline inside the source ends the
    segment and the model stops there, silently dropping the rest. Measured on a
    385-character block: one request returned 28 characters, line by line returned
    the lot. So each line goes on its own, and blank lines are kept as spacing.
    """
    out = []
    for line in (text or "").split("\n"):
        if not line.strip():
            out.append("")
            continue
        out.append(_lmt_call(line, target_language, source_language))
    return "\n".join(out)


def _translate_blocks_lmt(blocks: list[str], target_language: str,
                          source_language: str = "ja") -> list[str]:
    """Translate block inner-HTML strings with the local model.

    No batching: the model is line-delimited and has no system prompt, so there is
    nothing to gain from packing blocks together and a marker round-trip to lose.
    """
    if not blocks:
        return []
    traditional = _is_traditional_target(target_language)
    results = []
    for block in blocks:
        try:
            out = _lmt_repair_links(
                _lmt_block(_lmt_sanitize(block), target_language, source_language))
        except LMTError as e:
            logger.error(f"LMT block translation failed: {e}")
            results.append(block)
            continue
        if traditional:
            # min_ratio=0: this model mixes scripts within a block, which never
            # reaches the 0.15 floor the remote providers use. See force_traditional.
            out = hk_glossary.force_traditional(out, min_ratio=0.0)
        results.append(out)
    if traditional:
        results = [hk_glossary.localize(r) for r in results]
    return results


def _google_call(text: str, target_language: str, max_retries: int = 3) -> str:
    """One Google request. Returns the translated text.

    The response is a nested array whose first element is a list of segments; long
    input comes back split across several, so they are joined rather than indexed.
    """
    if not text or not text.strip():
        return text

    global _google_consecutive_walls
    params = {"client": GOOGLE_CLIENT, "sl": "auto", "tl": target_language, "dt": "t"}
    last = None
    for attempt in range(max_retries):
        try:
            _google_wait_turn()
            r = requests.post(
                GOOGLE_URL,
                params=params,
                data={"q": text},
                headers={"User-Agent": GOOGLE_USER_AGENT},
                timeout=30,
            )
            if r.status_code == 200:
                try:
                    segments = r.json()[0] or []
                except (ValueError, TypeError, IndexError, KeyError):
                    last = f"unparseable response: {r.text[:160]}"
                else:
                    with _google_gate:
                        _google_consecutive_walls = 0
                    return "".join(s[0] for s in segments if s and s[0])
            elif r.status_code == 429:
                # The wall. Do not retry into it — cool off and let the caller
                # leave the article untranslated for now.
                _google_enter_cooldown()
                raise GoogleError(f"HTTP 429: {r.text[:120]}")
            else:
                last = f"HTTP {r.status_code}: {r.text[:160]}"
                if r.status_code not in (500, 502, 503, 504):
                    break
        except requests.RequestException as e:
            last = str(e)
        if attempt < max_retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise GoogleError(f"Google Translate request failed: {last}")


def _parse_google_batch(out: str, indices: list[int]) -> dict[int, str]:
    """Split a marker-wrapped batch response back into per-block translations.

    Returns {} unless every marker sent came back exactly once and no marker leaked
    into visible text, so the caller can fall back to one call per block.
    """
    soup = BeautifulSoup(out, "html.parser")
    got: dict[int, str] = {}
    for div in soup.find_all(attrs={"data-i": True}):
        try:
            idx = int(div.get("data-i"))
        except (TypeError, ValueError):
            continue
        if idx in indices and idx not in got:
            got[idx] = div.decode_contents().strip()
    if set(got) != set(indices):
        return {}
    if any("data-i" in text for text in got.values()):
        return {}
    return got


def _google_block(text: str, target_language: str) -> str:
    """Translate one block, repairing a Simplified response."""
    out = _google_call(text, target_language)
    if _is_traditional_target(target_language) and hk_glossary.looks_simplified(out):
        out = hk_glossary.force_traditional(out)
    return out


def _translate_blocks_google(blocks: list[str], target_language: str) -> list[str]:
    """Translate block inner-HTML strings via Google, batched.

    HTML survives this endpoint, so blocks are wrapped in <div data-i="N"> and sent
    together; a batch whose markers do not round-trip is re-sent one block at a time.
    Unlike Qwen-MT this translator takes no instructions, so the Hong Kong vocabulary
    pass is the only lever on register — and it needs it: the endpoint drops to
    Simplified on short segments (路线概览 for ルート概要) even with tl=zh-TW.
    """
    if not blocks:
        return []

    batches: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    for i, block in enumerate(blocks):
        wrapped = len(block) + len(f'<div data-i="{i}"></div>')
        if current and current_len + wrapped > GOOGLE_BATCH_CHAR_BUDGET:
            batches.append(current)
            current, current_len = [], 0
        current.append(i)
        current_len += wrapped
    if current:
        batches.append(current)

    results: list[str] = [""] * len(blocks)
    traditional = _is_traditional_target(target_language)
    for batch in batches:
        payload = "".join(f'<div data-i="{j}">{blocks[j]}</div>' for j in batch)
        parsed: dict[int, str] = {}
        try:
            parsed = _parse_google_batch(_google_call(payload, target_language), batch)
        except GoogleError as e:
            logger.warning(f"Google batch failed, retrying per block: {e}")

        if parsed:
            for idx, text in parsed.items():
                results[idx] = hk_glossary.force_traditional(text) if (
                    traditional and hk_glossary.looks_simplified(text)) else text
            continue

        for j in batch:
            try:
                results[j] = _google_block(blocks[j], target_language)
            except GoogleError as e:
                logger.error(f"Google block translation failed: {e}")
                results[j] = blocks[j]

    if traditional:
        results = [hk_glossary.localize(r) for r in results]
    return results


# Attributes that carry machine-readable values, not prose. A machine translator
# will localise punctuation inside any attribute value containing spaces or commas
# (notably srcset) — full-width commas, CJK quote brackets — which breaks the URL.
# alt/title are deliberately absent: they are human-readable and should stay
# translated.
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


def _interleave_translation(soup: BeautifulSoup, el, translated_text: str) -> str:
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
    return str(blockquote) + str(translated_el)


def translate_text(text: str, target_language: str = "zh-TW", translator: str = "qwen",
                   source_language: str = "ja") -> tuple[str, str]:
    """Translate plain text (a title, say) to the target language.

    Returns:
        (translated_text, provider), or (the original text, "none") on failure.

    A failure returns the source text untouched. It used to append
    "(Translation Error)", which the reader then showed as the headline and built the
    article's URL slug from, and no later pass ever repaired it.
    """
    if not text or not text.strip():
        return text or "", "none"
    try:
        if translator == "deepl":
            result = _translate_deepl(text, target_language)
            if result is not None:
                return result, "DeepL"
        if translator == "lmt":
            out = _lmt_block(text, target_language, source_language)
            if _is_traditional_target(target_language):
                out = hk_glossary.localize(out)
            return out, LMT_PROVIDER_LABEL
        if translator == "google":
            out = _google_block(text, target_language)
            primary = GOOGLE_PROVIDER_LABEL
        else:
            out = _qwen_block(text, target_language)
            primary = QWEN_PROVIDER_LABEL
        if _is_traditional_target(target_language):
            out = hk_glossary.localize(out)
        return out, primary
    except Exception as e:
        # The local model is the last resort under every remote provider. A title is
        # one line — about 8s — so it can be rescued inline; a body cannot, and is
        # left for the deferred worker.
        primary = {"google": GOOGLE_PROVIDER_LABEL, "deepl": "DeepL"}.get(
            translator, QWEN_PROVIDER_LABEL)
        logger.warning(f"{primary} title translation failed, trying {LMT_PROVIDER_LABEL}: {e}")
        try:
            out = _lmt_block(text, target_language, source_language)
            if _is_traditional_target(target_language):
                out = hk_glossary.localize(out)
            return out, lmt_fallback_label(primary, str(e))
        except Exception as lmt_err:
            logger.error(f"Translation failed, {LMT_PROVIDER_LABEL} too: {lmt_err}")
            return text, "none"


def translate_html_iter(html: str, target_language: str = "zh-TW", translator: str = "qwen") -> Iterator[dict]:
    """Translate HTML content yielding block events as batches arrive, followed by a result event.

    Yields:
        {"type": "block", "index": int, "html": str}
        {"type": "result", "html": str, "provider": str, "total": int}
    """
    if not html or not html.strip():
        yield {"type": "result", "html": html or "", "provider": "none", "total": 0}
        return

    soup = BeautifulSoup(html, "html.parser")

    # Find all block elements with non-empty text (at any depth)
    elements = soup.find_all(list(BLOCK_TAGS))
    elements = [el for el in elements if el.get_text().strip()]

    if not elements:
        yield {"type": "result", "html": html, "provider": "none", "total": 0}
        return

    # Top-level blocks only — skip nested blocks (e.g. <li> inside <blockquote>)
    # and empty shells. These are the units we translate and interleave.
    top_elements = [
        el for el in elements
        if not el.find_parent(list(BLOCK_TAGS)) and el.decode_contents().strip()
    ]
    if not top_elements:
        yield {"type": "result", "html": html, "provider": "none", "total": 0}
        return

    providers_used = set()

    if translator == "deepl":
        for el in top_elements:
            original_text = _strip_media_for_translation(el.decode_contents())
            translated_text = _translate_deepl(original_text, target_language)
            if translated_text is None:
                translated_text = _qwen_block(original_text, target_language)
                providers_used.add(QWEN_PROVIDER_LABEL)
            else:
                providers_used.add("DeepL")
            _interleave_translation(soup, el, translated_text)
        if len(providers_used) > 1:
            provider = " + ".join(sorted(providers_used))
        elif providers_used:
            provider = providers_used.pop()
        else:
            provider = "none"
        yield {"type": "result", "html": str(soup), "provider": provider, "total": len(top_elements)}
        return

    if translator != "qwen":
        label = GOOGLE_PROVIDER_LABEL if translator == "google" else translator
        batch = _translate_blocks_google if translator == "google" else _translate_blocks_qwen
        originals = [_strip_media_for_translation(el.decode_contents()) for el in top_elements]
        translations = batch(originals, target_language)
        applied = 0
        for el, original_text, translated_text in zip(top_elements, originals, translations):
            if not translated_text or translated_text.strip() == original_text.strip():
                continue
            _interleave_translation(soup, el, translated_text)
            applied += 1
        if applied:
            yield {"type": "result", "html": str(soup), "provider": label, "total": len(top_elements)}
        else:
            yield {"type": "result", "html": html, "provider": "none", "total": len(top_elements)}
        return

    for i, el in enumerate(top_elements):
        el["data-tb"] = str(i)
    source_html = str(soup)
    for el in top_elements:
        del el["data-tb"]

    yield {"type": "source", "html": source_html}

    originals = [_strip_media_for_translation(el.decode_contents()) for el in top_elements]
    applied = 0
    for batch_indices, translations in _translate_blocks_qwen_iter(originals, target_language):
        for idx, translated_text in zip(batch_indices, translations):
            original_text = originals[idx]
            if not translated_text or translated_text.strip() == original_text.strip():
                continue
            el = top_elements[idx]
            block_markup = _interleave_translation(soup, el, translated_text)
            applied += 1
            yield {"type": "block", "index": idx, "html": block_markup}

    if applied:
        provider = QWEN_PROVIDER_LABEL
        out_html = str(soup)
    else:
        provider = "none"
        out_html = html

    yield {"type": "result", "html": out_html, "provider": provider, "total": len(top_elements)}


def translate_html(html: str, target_language: str = "zh-TW", translator: str = "qwen") -> tuple[str, str]:
    """Translate HTML content with bilingual interleaving.

    For each block element, the original is wrapped in a <blockquote> (for
    Miniflux-compatible visual distinction) followed by the translated element.

    A thin drain of translate_html_iter.

    Returns:
        (translated_html, provider), or (the source html, "none") on failure.
    """
    if not html or not html.strip():
        return html or "", "none"
    try:
        final = None
        for event in translate_html_iter(html, target_language, translator):
            if event["type"] == "result":
                final = event
        return (final["html"], final["provider"]) if final else (html, "none")
    except Exception as e:
        logger.error(f"HTML translation failed: {e}")
        return html, "none"


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
    """Translate title and HTML content using DeepSeek, falling back to Qwen-MT.

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
        logger.warning(f"DeepSeek translation failed, falling back to Qwen-MT: {e}")
        try:
            fallback_title, _ = translate_text(title, target_language, translator="qwen")
            fallback_content, fallback_provider = translate_html(
                content_html, target_language, translator="qwen")
            if fallback_provider == "none":
                # Both providers are down. Say so rather than badging the untouched
                # source as translated.
                return title, content_html, "none", {}
            return (fallback_title, fallback_content,
                    f"{QWEN_PROVIDER_LABEL} (fallback after DeepSeek failed)", {})
        except Exception as fb_err:
            logger.error(f"Fallback translation failed: {fb_err}")
            return title, content_html, "none", {}


def translate_article_qwen(title: str, content_html: str,
                           target_language: str = "zh-TW") -> tuple[str, str, str, dict]:
    """Translate title and content with Qwen-MT-Flash.

    Mirrors translate_article_deepseek's signature so the scheduler can treat the two
    providers alike. `usage` sums every request the article took; Qwen-MT has no cache
    tier, so it reports prompt_tokens / completion_tokens / calls only.
    """
    if not content_html or not content_html.strip():
        return title, content_html, "none", {}

    _tally_reset()
    translated_title = title
    try:
        if title and title.strip():
            translated_title, _ = translate_text(title, target_language, translator="qwen")
    except Exception as e:
        logger.warning(f"Qwen title translation failed: {e}")

    translated_content, provider = translate_html(content_html, target_language, translator="qwen")
    return translated_title, translated_content, provider, _tally_get()


async def translate_text_async(text: str, target_language: str = "zh-TW", translator: str = "qwen",
                               source_language: str = "ja") -> tuple[str, str]:
    """Async wrapper for translate_text — runs in thread pool to avoid blocking event loop."""
    return await asyncio.to_thread(translate_text, text, target_language, translator, source_language)


async def translate_html_async(html: str, target_language: str = "zh-TW", translator: str = "qwen") -> tuple[str, str]:
    """Async wrapper for translate_html — runs in thread pool to avoid blocking event loop."""
    return await asyncio.to_thread(translate_html, html, target_language, translator)


async def translate_article_deepseek_async(title: str, content_html: str, target_language: str = "zh-TW") -> tuple[str, str, str, dict]:
    """Async wrapper for translate_article_deepseek — runs in thread pool to avoid blocking event loop."""
    return await asyncio.to_thread(translate_article_deepseek, title, content_html, target_language)


async def translate_article_qwen_async(title: str, content_html: str,
                                       target_language: str = "zh-TW") -> tuple[str, str, str, dict]:
    """Async wrapper for translate_article_qwen — runs in a thread to keep the loop free."""
    return await asyncio.to_thread(translate_article_qwen, title, content_html, target_language)
