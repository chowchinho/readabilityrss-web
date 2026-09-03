"""Translation quality comparison: Google Translate (free) vs DeepSeek.

Fetches 8 sample articles fresh via the Pi `/parse` endpoint (untranslated),
translates each through both providers block-by-block (matching production
translation.py logic), and writes a side-by-side HTML report.

Run from project root:
    python backend/scripts/translation_eval.py
"""

import html as html_mod
import json
import os
import re
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

SCRIPT_DIR = Path(__file__).parent
BACKEND_DIR = SCRIPT_DIR.parent
DATA_DIR = SCRIPT_DIR / "translation_eval_data"
SAMPLE_URLS_FILE = DATA_DIR / "sample_urls.json"
OUT_FILE = SCRIPT_DIR / "translation_comparison.html"

PARSE_ENDPOINT = os.environ.get("PARSE_ENDPOINT", "http://127.0.0.1:8001/api/parse")
GOOGLE_TARGET_LANG = "zh-TW"  # Google Translate does not offer zh-HK; closest Traditional option.
DEEPSEEK_TARGET_LABEL = "Hong Kong Traditional Chinese (zh-HK)"
BLOCK_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"]
MAX_BLOCKS_PER_ARTICLE = 8

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

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
    "Do NOT use Simplified Chinese characters or Mainland-specific vocabulary "
    "(avoid 视频 / use 影片; avoid 软件 / use 軟件 in Traditional).\n\n"
)

# Natural Hong Kong written Chinese — register-matched. Avoids BOTH extremes:
# pure Cantonese verbal particles (嘅, 呢, 喺) on one side, and classical/wenyan
# over-formalism (何所指, 是乃, 焉) on the other. The target register is HK
# magazine / blog / lifestyle article (Esquire HK, GQ HK, Ming Pao Weekly,
# Apple Daily lifestyle), which is the natural register the user wants.
DEEPSEEK_SYSTEM_PROMPT_NATURAL = (
    "You are a professional translator for Hong Kong publications. Translate "
    "Japanese and English text into NATURAL HONG KONG WRITTEN CHINESE — the "
    "register used in HK magazines, blogs, and lifestyle articles (think "
    "Esquire HK, GQ HK, Ming Pao Weekly, modern news features).\n\n"
    + _DEEPSEEK_FORMAT_PREAMBLE +
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
    "(e.g., 「持続可能な開発のための2030アジェンダ」, 「パリ協定」, 「働き方改革」)\n"
    "  • Professional / academic / industry term, jargon, methodology, or "
    "concept (e.g., 「ガン=カタ」, 「ディープラーニング」, 「ESG投資」)\n"
    "  • Institutional, organisational, or company name\n"
    "  • Highlighted term, slogan, emphasised phrase, or section heading\n"
    "  • Rhetorical question posed by the article (not by a speaker)\n"
    "→ treat it as NARRATION (strict written HK, formal where appropriate, "
    "NEVER Cantonese particles, NEVER overly casual).\n\n"
    "RULE OF THUMB for borderline cases: if you cannot point to a clear "
    "speaker being quoted (a named person with a speech-verb attribution), "
    "default to NARRATION treatment. Better to render slightly stiff than to "
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
    "  WRONG (casual rendering): 聯合國通過咗「2030可持續發展計劃啦」。\n"
    "  RIGHT (formal written HK): 聯合國通過了「2030年可持續發展議程」。\n"
    "  (Note: the quoted text is an official UN framework name. It MUST stay "
    "formal and use the standard published Chinese translation if one exists.)\n\n"
    "VOCABULARY — use Hong Kong preferences where they differ from Taiwan:\n"
    "  軟件 (not 軟體), 硬件 (not 硬體), 影片 (not 視頻), 巴士 (not 公車), "
    "的士 (not 計程車), 雪櫃 (not 冰箱), 鐳射 (not 雷射), 平治 (Mercedes), "
    "寶馬 (BMW). Use 甚麼 (not 什麼) and 到底 / 究竟 for question emphasis.\n"
    "CHARACTER PREFERENCES — Hong Kong style:\n"
    "  prefer 着 over 著, prefer 裏 over 裡."
)

# Stricter prompt — explicitly forbids Cantonese verbal particles and gives a
# concrete worked example so the model is forced to convert to formal written Chinese
# with HK vocabulary preferences.
DEEPSEEK_SYSTEM_PROMPT_FORMAL = (
    "You are a professional Chinese translator. Translate Japanese and English "
    "text into FORMAL WRITTEN MODERN STANDARD CHINESE using TRADITIONAL "
    "CHARACTERS, with Hong Kong vocabulary and character preferences. The "
    "target register is a Ming Pao or SCMP Chinese news article — formal, "
    "polished, NEVER colloquial.\n\n"
    + _DEEPSEEK_FORMAT_PREAMBLE +
    "CRITICAL — Cantonese verbal/colloquial particles are FORBIDDEN. After "
    "drafting your translation, REREAD it and replace every Cantonese particle "
    "with its formal written-Chinese equivalent before returning:\n"
    "  嘅 → 的       呢 / 呢個 → 這 / 這個    嗰 / 嗰個 → 那 / 那個\n"
    "  同 (and/with) → 和 / 與    喺 → 在    咗 → 了    啲 → 些 / 一些\n"
    "  咁 → 這樣 / 那樣    唔 → 不    係 → 是    嚟 → 來\n"
    "  咩 → 什麼    啦 / 喎 (sentence-final) → 了 / 吧\n\n"
    "Worked example showing what is WRONG vs RIGHT:\n"
    "  WRONG (Cantonese verbal): "
    "伊貝型號則採用貼近膚色嘅米啡色，官方表示「呢種配色可以同飾物配搭，增添樂趣」。\n"
    "  RIGHT (formal written HK): "
    "伊貝型號則採用貼近膚色的米啡色，官方表示「這種配色可以和飾物配搭，增添樂趣」。\n\n"
    "Vocabulary — prefer Hong Kong terms over Taiwan terms where they differ:\n"
    "  軟件 (not 軟體), 硬件 (not 硬體), 影片 (not 視頻 / 影像), 巴士 (not 公車), "
    "的士 (not 計程車), 雪櫃 (not 冰箱), 鐳射 (not 雷射), 平治 (Mercedes), "
    "寶馬 (BMW).\n"
    "Character preferences — Hong Kong style:\n"
    "  prefer 着 over 著, prefer 裏 over 裡, prefer 為 (both fine).\n"
)


def load_env():
    """Manually load backend/.env so we don't need python-dotenv."""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def fetch_parsed(url: str) -> dict:
    headers = {}
    token = os.environ.get("PARSE_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(PARSE_ENDPOINT, json={"url": url}, headers=headers, timeout=90)
    r.raise_for_status()
    return r.json()


def translate_google(text: str) -> str:
    if not text or not text.strip():
        return text or ""
    try:
        time.sleep(0.5)
        result = GoogleTranslator(source="auto", target=GOOGLE_TARGET_LANG).translate(text)
        if result and "Error 500 (Server Error)" in result:
            time.sleep(2)
            result = GoogleTranslator(source="auto", target=GOOGLE_TARGET_LANG).translate(text)
        return result or text
    except Exception as e:
        return f"[Google error: {e}]"


class DeepSeekError(RuntimeError):
    """Raised when the DeepSeek API returns a non-success response or malformed output."""


def call_deepseek_article(title: str, content_html: str, api_key: str,
                          system_prompt: str) -> tuple[str, str, dict]:
    """Translate title + content HTML in a single DeepSeek call.

    Returns (translated_title, translated_content_html, usage_dict).
    Raises DeepSeekError on any HTTP failure or malformed response — never silently
    embeds an error string into the output.
    """
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY not set in backend/.env")

    user_msg = (
        f"[TITLE]\n{title or ''}\n[/TITLE]\n"
        f"[CONTENT]\n{content_html or ''}\n[/CONTENT]"
    )
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
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
    except (ValueError, KeyError, IndexError) as e:
        raise DeepSeekError(f"malformed DeepSeek response: {e}") from e

    title_match = re.search(r"\[TITLE\]\s*(.*?)\s*\[/TITLE\]", text, re.DOTALL)
    content_match = re.search(r"\[CONTENT\]\s*(.*?)\s*\[/CONTENT\]", text, re.DOTALL)
    if not title_match or not content_match:
        raise DeepSeekError(
            f"DeepSeek response missing [TITLE]/[CONTENT] tags. First 300 chars: {text[:300]!r}"
        )
    return title_match.group(1).strip(), content_match.group(1).strip(), usage


def translate_html_blocks(html_content: str, translator_fn) -> str:
    """Mirror backend translation.py: find block elements at any depth and translate each block."""
    soup = BeautifulSoup(html_content, "html.parser")
    elements = [el for el in soup.find_all(BLOCK_TAGS) if el.get_text().strip()]
    if not elements:
        return html_content
    for el in elements:
        if el.find_parent(BLOCK_TAGS):
            continue
        original_inner = el.decode_contents()
        if not original_inner.strip():
            continue
        translated = translator_fn(original_inner)
        try:
            new_inner = BeautifulSoup(translated, "html.parser")
            el.clear()
            el.append(new_inner)
        except Exception:
            el.string = translated
    return str(soup)


def trim_to_n_blocks(html_content: str, n: int) -> str:
    """Cap article content to first N block elements to keep eval fast/cheap."""
    soup = BeautifulSoup(html_content, "html.parser")
    blocks = soup.find_all(BLOCK_TAGS)
    if len(blocks) <= n:
        return html_content
    for b in blocks[n:]:
        b.decompose()
    return str(soup)


def build_report(results: list, total_usage: dict = None, total_cost: float = 0.0) -> str:
    css = """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
             max-width: 1800px; margin: 20px auto; padding: 0 20px; color: #222;
             background: #fafafa; }
      h1 { color: #1c1510; border-bottom: 3px solid #c97d2e; padding-bottom: 8px; }
      .article { margin-bottom: 50px; padding: 20px; background: #f5efe6;
                 border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
      .article h2 { margin-top: 0; color: #c97d2e; font-size: 1.2em; }
      .meta { color: #666; font-size: 0.82em; margin-bottom: 12px; }
      .meta a { color: #1c1510; }
      .cols { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; }
      .col { background: white; padding: 12px; border-radius: 6px;
             max-height: 70vh; overflow-y: auto;
             box-shadow: 0 1px 2px rgba(0,0,0,0.06); }
      .col h3 { margin: 0 0 10px 0; padding-bottom: 6px; font-size: 0.85em;
                position: sticky; top: 0; background: white; }
      .col-original h3 { border-bottom: 2px solid #999; color: #666; }
      .col-google h3 { border-bottom: 2px solid #4285F4; color: #4285F4; }
      .col-deepseek-natural h3 { border-bottom: 2px solid #e57373; color: #c62828; }
      .col-deepseek-formal h3 { border-bottom: 2px solid #6e3aff; color: #6e3aff; }
      .col p, .col li { line-height: 1.6; font-size: 0.92em; }
      .col img { max-width: 100%; height: auto; border-radius: 4px; }
      .title-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px;
                   margin-bottom: 10px; }
      .title-cell { padding: 10px 12px; background: white; border-radius: 4px;
                    font-weight: 600; font-size: 0.95em; line-height: 1.4; }
      .summary-box { background: white; padding: 16px; border-radius: 6px;
                     margin-bottom: 30px; }
      .summary-box table { border-collapse: collapse; width: 100%; }
      .summary-box td, .summary-box th { padding: 6px 10px; border-bottom: 1px solid #eee; text-align: left; font-size: 0.9em; }
    </style>
    """
    total_google_ms = sum(r["google_ms"] for r in results)
    total_natural_ms = sum(r["deepseek_natural_ms"] for r in results)
    total_formal_ms = sum(r["deepseek_formal_ms"] for r in results)
    avg_google = total_google_ms / len(results)
    avg_natural = total_natural_ms / len(results)
    avg_formal = total_formal_ms / len(results)

    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>Translation Comparison: Google vs DeepSeek</title>{css}</head><body>",
        "<h1>Translation Quality Comparison</h1>",
        "<div class='summary-box'>",
        f"<p><strong>Providers compared:</strong></p>",
        "<ul>",
        f"<li><strong>Google Translate</strong> — current production fallback, "
        f"target <code>{GOOGLE_TARGET_LANG}</code> (Google has no zh-HK option).</li>",
        f"<li><strong>DeepSeek (natural HK)</strong> — register-matched prompt. "
        f"Target: HK magazine / blog / lifestyle article tone (Esquire HK, GQ HK, "
        f"Ming Pao Weekly). Casual source → casual HK, formal source → formal HK. "
        f"Forbids BOTH pure Cantonese verbal particles AND classical/wenyan.</li>",
        f"<li><strong>DeepSeek (formal HK written)</strong> — stricter prompt aimed at "
        f"formal news-style Chinese (Ming Pao / SCMP). Kept here for comparison so "
        f"you can see if the natural prompt overcorrects.</li>",
        "</ul>",
        f"<p><strong>Sample:</strong> {len(results)} articles, first "
        f"{MAX_BLOCKS_PER_ARTICLE} block elements per article.</p>",
        "<p><strong>How to read:</strong> Per article, compare the three columns. Look for: "
        "(1) accuracy vs source, (2) natural Taiwanese phrasing, (3) handling of "
        "brand/proper nouns, (4) preservation of inline tags and structure.</p>",
        "<table><tr><th>Metric</th><th>Google</th><th>DeepSeek (natural)</th><th>DeepSeek (formal)</th></tr>",
        f"<tr><td>Avg time / article</td><td>{avg_google:.0f} ms</td>"
        f"<td>{avg_natural:.0f} ms</td><td>{avg_formal:.0f} ms</td></tr>",
        f"<tr><td>Total time</td><td>{total_google_ms/1000:.1f} s</td>"
        f"<td>{total_natural_ms/1000:.1f} s</td><td>{total_formal_ms/1000:.1f} s</td></tr>",
        f"<tr><td>API calls / article</td><td>~N blocks</td><td colspan='2'>1 (whole-article batch)</td></tr>",
        "</table>",
    ]
    if total_usage and total_usage.get("total_tokens"):
        # Cost below covers BOTH the natural and formal calls; production will only
        # use one, so divide by 2 when projecting.
        per_article_cost_both = total_cost / max(len(results), 1)
        per_article_cost_single = per_article_cost_both / 2
        projected_monthly = per_article_cost_single * 1500
        parts.append("<p style='margin-top:14px;'><strong>DeepSeek usage this run "
                     "(both natural + formal calls):</strong> "
                     f"{total_usage['prompt_tokens']:,} input + "
                     f"{total_usage['completion_tokens']:,} output tokens = "
                     f"<strong>${total_cost:.4f}</strong>. "
                     f"Production will use ONE prompt only → "
                     f"${per_article_cost_single:.4f}/article × 1,500 articles ≈ "
                     f"<strong>${projected_monthly:.2f}/month</strong> "
                     f"(scaled for full-length articles below).</p>")
    parts.append("</div>")
    for r in results:
        parts.append("<div class='article'>")
        parts.append(f"<h2>{html_mod.escape(r['source_name'])}</h2>")
        parts.append(
            f"<div class='meta'>URL: <a href='{html_mod.escape(r['url'])}' target='_blank'>"
            f"{html_mod.escape(r['url'])}</a> | Source lang: "
            f"{html_mod.escape(str(r.get('source_lang') or 'unknown'))} | "
            f"Google: {r['google_ms']:.0f} ms | "
            f"DeepSeek natural: {r['deepseek_natural_ms']:.0f} ms | "
            f"DeepSeek formal: {r['deepseek_formal_ms']:.0f} ms</div>"
        )
        parts.append("<div class='title-row'>")
        parts.append(f"<div class='title-cell'>{html_mod.escape(r['original_title'])}</div>")
        parts.append(f"<div class='title-cell'>{html_mod.escape(r['google_title'])}</div>")
        parts.append(f"<div class='title-cell'>{html_mod.escape(r['deepseek_natural_title'])}</div>")
        parts.append(f"<div class='title-cell'>{html_mod.escape(r['deepseek_formal_title'])}</div>")
        parts.append("</div>")
        parts.append("<div class='cols'>")
        parts.append(f"<div class='col col-original'><h3>Original (extracted)</h3>{r['original_content']}</div>")
        parts.append(f"<div class='col col-google'><h3>Google Translate (zh-TW)</h3>{r['google_content']}</div>")
        parts.append(f"<div class='col col-deepseek-natural'><h3>DeepSeek — natural HK</h3>{r['deepseek_natural_content']}</div>")
        parts.append(f"<div class='col col-deepseek-formal'><h3>DeepSeek — formal written HK</h3>{r['deepseek_formal_content']}</div>")
        parts.append("</div>")
        parts.append("</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    load_env()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not deepseek_key:
        print("WARNING: DEEPSEEK_API_KEY not set in backend/.env — DeepSeek column will be empty.")

    if not SAMPLE_URLS_FILE.exists():
        print(f"Missing sample URL file: {SAMPLE_URLS_FILE}")
        sys.exit(1)

    samples = json.loads(SAMPLE_URLS_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(samples)} sample URLs.")

    results = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                   "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}
    deepseek_failures = []

    for i, s in enumerate(samples, 1):
        print(f"\n[{i}/{len(samples)}] {s['source_name']}")
        print(f"  URL: {s['url']}")
        try:
            parsed = fetch_parsed(s["url"])
        except Exception as e:
            print(f"  Parse fetch failed: {e}")
            continue
        original_title = parsed.get("title", "") or ""
        # /parse returns extracted content as "description"
        original_content = parsed.get("description", "") or parsed.get("content", "") or ""
        if not original_content:
            print("  Skipping: empty content.")
            continue
        original_content = trim_to_n_blocks(original_content, MAX_BLOCKS_PER_ARTICLE)
        print(f"  Original content: {len(original_content)} chars")

        t0 = time.time()
        try:
            google_title = translate_google(original_title)
            google_content = translate_html_blocks(original_content, translate_google)
        except Exception as e:
            print(f"  Google failed: {e}")
            continue
        google_ms = (time.time() - t0) * 1000
        print(f"  Google: {google_ms:.0f} ms")

        prompts = [
            ("natural", DEEPSEEK_SYSTEM_PROMPT_NATURAL),
            ("formal", DEEPSEEK_SYSTEM_PROMPT_FORMAL),
        ]
        deepseek_outputs = {}
        deepseek_failed = False
        for label, sys_prompt in prompts:
            t0 = time.time()
            try:
                d_title, d_content, usage = call_deepseek_article(
                    original_title, original_content, deepseek_key, sys_prompt
                )
            except DeepSeekError as e:
                print(f"  DeepSeek ({label}) FAILED: {e}")
                deepseek_failures.append((f"{s['source_name']} [{label}]", str(e)))
                msg = str(e).lower()
                if any(t in msg for t in ("402", "insufficient", "401", "unauthorized", "invalid api key")):
                    print("\n  Stopping eval — this error will repeat for every article.")
                    deepseek_failed = True
                    break
                deepseek_outputs[label] = {
                    "title": f"[FAILED: {e}]",
                    "content": f"<p style='color:#c62828'>[FAILED: {html_mod.escape(str(e))}]</p>",
                    "ms": (time.time() - t0) * 1000,
                    "usage": {},
                }
                continue
            ms = (time.time() - t0) * 1000
            for k, v in usage.items():
                if isinstance(v, (int, float)):
                    total_usage[k] = total_usage.get(k, 0) + v
            print(f"  DeepSeek ({label}): {ms:.0f} ms — "
                  f"prompt={usage.get('prompt_tokens', 0)} tok, "
                  f"completion={usage.get('completion_tokens', 0)} tok")
            deepseek_outputs[label] = {
                "title": d_title, "content": d_content, "ms": ms, "usage": usage,
            }
        if deepseek_failed:
            break
        if "natural" not in deepseek_outputs or "formal" not in deepseek_outputs:
            continue

        results.append({
            **s,
            "original_title": original_title,
            "original_content": original_content,
            "google_title": google_title,
            "google_content": google_content,
            "google_ms": google_ms,
            "deepseek_natural_title": deepseek_outputs["natural"]["title"],
            "deepseek_natural_content": deepseek_outputs["natural"]["content"],
            "deepseek_natural_ms": deepseek_outputs["natural"]["ms"],
            "deepseek_formal_title": deepseek_outputs["formal"]["title"],
            "deepseek_formal_content": deepseek_outputs["formal"]["content"],
            "deepseek_formal_ms": deepseek_outputs["formal"]["ms"],
        })

    if not results:
        print("\nNo successful translations.")
        if deepseek_failures:
            print(f"DeepSeek failures: {len(deepseek_failures)}")
            for name, err in deepseek_failures:
                print(f"  - {name}: {err[:150]}")
        sys.exit(1)

    print(f"\n=== DeepSeek token usage (across {len(results)} articles) ===")
    print(f"  Prompt tokens:     {total_usage['prompt_tokens']:>10,}")
    print(f"  Completion tokens: {total_usage['completion_tokens']:>10,}")
    print(f"  Total tokens:      {total_usage['total_tokens']:>10,}")
    cache_hit = total_usage.get('prompt_cache_hit_tokens', 0)
    cache_miss = total_usage.get('prompt_cache_miss_tokens', 0)
    if cache_hit or cache_miss:
        print(f"  Cache hit tokens:  {cache_hit:>10,}")
        print(f"  Cache miss tokens: {cache_miss:>10,}")
    # Cost (deepseek-v4-flash): $0.14/M input cache miss, $0.0028/M cache hit, $0.28/M output
    input_miss_cost = (cache_miss or total_usage['prompt_tokens']) * 0.14 / 1_000_000
    input_hit_cost = cache_hit * 0.0028 / 1_000_000
    output_cost = total_usage['completion_tokens'] * 0.28 / 1_000_000
    total_cost = input_miss_cost + input_hit_cost + output_cost
    print(f"  Cost this run:     ${total_cost:.4f} USD")
    if results:
        per_article = total_cost / len(results)
        projected_monthly = per_article * 1500  # current monthly article volume
        print(f"  Per article:       ${per_article:.4f}")
        print(f"  Projected /month (1500 articles): ${projected_monthly:.2f}")

    OUT_FILE.write_text(build_report(results, total_usage, total_cost), encoding="utf-8")
    print(f"\nReport written: {OUT_FILE}")
    print("Open it in your browser to review.")


if __name__ == "__main__":
    main()
