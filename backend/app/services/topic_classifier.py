"""DeepSeek batch article tagging with strict ID reconciliation."""
import json
import logging
import os
import re
import time
import requests

logger = logging.getLogger(__name__)

BATCH_PAUSE_SECONDS = 0.5
RETRY_BACKOFF_SECONDS = 1.0

# Load .env fallback if DEEPSEEK_API_KEY not set in environment
if not os.getenv("DEEPSEEK_API_KEY"):
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Overridable so the model and the deliberation switch can be changed without a
# code edit - useful if a future model is added, or if low-confidence articles are
# ever escalated to a second pass.
TAGGING_MODEL = os.getenv("TAGGING_MODEL", "deepseek-v4-flash")

# Measured 2026-08-11 on 8 real articles: thinking on produced 4,421 completion
# tokens of which 3,742 were reasoning; thinking off produced 629, with the same
# 8/8 articles returned. Deliberation is ~7x the output cost and none of it is kept.
# Scoped to tagging only - translation uses the same client and is left untouched.
TAGGING_THINKING = os.getenv("TAGGING_THINKING", "disabled").strip().lower()

CLOSED_TOPICS = {
    "Travel", "Gaming", "Consumer Tech", "Science", "Software & AI",
    "Entertainment", "Photography", "Anime & Manga", "Design & Art",
    "DIY & Hardware", "Supernatural", "Real Estate", "Retro Gaming",
    "Local Life", "Business & Economy", "Sports", "Transport",
    "Food & Dining", "Books & Literature", "Skincare & Beauty",
    "Sneakers & Streetwear", "Fashion & Accessories", "Crime & Policing",
    "Culture", "Education", "Football", "Other Sports", "Health & Fitness",
    "Home & Garden", "Politics", "Automotive", "Personal Finance", "unknown"
}

CLOSED_REGIONS = {
    "Japan", "United Kingdom", "Taiwan", "Global", "Hong Kong",
    "United States", "Australia", "Germany", "China", "unknown"
}

CLOSED_TYPES = {
    "Feature", "News", "Announcement", "Product Launch", "Review",
    "Opinion", "Sponsored", "Buying Guide", "Deal", "Listing", "unknown"
}

SYSTEM_PROMPT = """You are a precise article tagging and classification assistant.
Given a list of articles, classify each article into primary topic, secondary topics, region, and article type.

CLOSED VOCABULARIES & DESCRIPTIONS:

PRIMARY TOPIC (choose exactly one):
- Travel: Travel guides, destinations, trips, hotels, flights, and tourism.
- Gaming: Video games, game releases, gaming hardware, and esports.
- Consumer Tech: Smart home, gadgets, consumer electronics, smartphones, and wearables.
- Science: Scientific discoveries, astronomy, physics, biology, and environment.
- Software & AI: Programming, software applications, artificial intelligence, operating systems, and web dev.
- Entertainment: Movies, TV shows, music, celebrity news, and streaming services.
- Photography: Cameras, lenses, photo techniques, and photography gear.
- Anime & Manga: Japanese animation, manga series, anime figures, and otaku culture.
- Design & Art: Graphic design, industrial design, architecture, visual art, and typography.
- DIY & Hardware: Home improvement, woodworking, maker projects, tools, and 3D printing.
- Supernatural: Paranormal phenomena, UFOs, ghosts, myths, and urban legends.
- Real Estate: Housing market, property listings, home buying, architecture, and interior decor.
- Retro Gaming: Emulation, retro consoles, vintage arcade, and classic video games.
- Local Life: Local community, regional events, neighborhood news, and living guides.
- Business & Economy: Financial markets, economy, companies, stock market, and corporate news.
- Sports: Athletic competitions, sports teams, matches, and general sports news.
- Transport: Trains, public transit, aviation, railways, and urban transport infrastructure.
- Food & Dining: Cooking, recipes, restaurants, food reviews, and drinks.
- Books & Literature: Novels, book reviews, authors, reading guides, and publishing.
- Skincare & Beauty: Skincare products, cosmetics, beauty routines, and grooming.
- Sneakers & Streetwear: Sneaker releases, streetwear fashion, and urban apparel.
- Fashion & Accessories: Clothing, apparel, watches, jewelry, and fashion trends.
- Crime & Policing: Law enforcement, police news, crime reports, legal trials, and security.
- Culture: Cultural history, traditions, heritage, society, and philosophy.
- Education: Schools, universities, learning resources, teaching, and academic research.
- Football: Soccer matches, football clubs, leagues (Premier League, etc.), and players.
- Other Sports: Basketball, tennis, motorsport, golf, swimming, and non-football sports.
- Health & Fitness: Physical health, exercise, nutrition, wellness, and medical advice.
- Home & Garden: Interior design, gardening, plants, furniture, and home organization.
- Politics: Government policy, elections, political debates, and international diplomacy.
- Automotive: Cars, electric vehicles, motorcycles, driving, and auto industry.
- Personal Finance: Budgeting, personal investments, credit cards, taxes, and savings.
- unknown: Anything that does not fit the above categories.

REGION (choose exactly one):
- Japan: Japan, Japanese culture, cities, or events.
- United Kingdom: United Kingdom, UK cities, politics, or events.
- Taiwan: Taiwan, Taiwanese culture, cities, or events.
- Global: Worldwide, multi-national, or non-region-specific subject matter.
- Hong Kong: Hong Kong SAR, local HK news, culture, or events.
- United States: United States, US domestic politics, cities, or events.
- Australia: Australia or Australian events.
- Germany: Germany or German events.
- China: Mainland China domestic news, policy, or events.
- unknown: Unknown or unidentifiable region.

ARTICLE TYPE (choose exactly one):
- Feature: In-depth article, essay, interview, profile, or long-form piece.
- News: Time-sensitive reporting of recent events or developments.
- Announcement: Official statement, company press release, or site notice.
- Product Launch: Introduction of a newly released product or service.
- Review: Critical evaluation of a product, game, movie, or service.
- Opinion: Editorial, column, commentary, or personal viewpoint.
- Sponsored: Paid content, advertorial, or promotional feature.
- Buying Guide: Buying advice, recommendations, product comparisons, or roundups.
- Deal: Price drop, discount code, sale event, or special offer.
- Listing: Event list, real estate listing, or directory entry.
- unknown: Unidentifiable article type.

NOTE ON FEED NAME & AUDIENCE RULE:
The publication tells you which interest-community seeks this article out.
`primary` is that community. The object or subject the article is about belongs
in `secondary`, never in `primary`.

FEW-SHOT EXAMPLES:
- Article: "A temple where volunteers freely carve rakan statues", Published by: "a travel blog" -> primary: "Travel", secondary: ["Temples", "Culture"]
- Article: "St Mary's Cathedral Tokyo, recreating the Lourdes grotto", Published by: "a travel blog" -> primary: "Travel", secondary: ["Architecture", "Religion"]
- Article: "New Era large-size luggage case, release info", Published by: "a streetwear feed" -> primary: "Fashion & Accessories", secondary: ["Luggage", "Product Launch"]
- Article: "Endangered-species rollback on Boing Boing", Published by: "Boing Boing" -> primary: "Politics"
- Article: "Gamer jailed over a username", Published by: "a gaming blog" -> primary: "Gaming"
- Article: "Prenatal-memory piece in ムー", Published by: "ムー" -> primary: "Supernatural"
- Article: "Trump booed at the World Cup final", Published by: "a sports feed" -> primary: "Football"

OUTPUT FORMAT:
Be concise. Output JSON object immediately with key "articles", containing a list of objects with fields:
"id" (must match article id exactly), "primary", "secondary" (list of strings), "region", "type", "confidence" ("high"|"medium"|"low"), "ai_summary" (1 sentence summary).
"""

_DASH_SPLIT = re.compile(r"\s+[-–]\s+")
_PIPE_SPLIT = re.compile(r"\s*[|｜]\s*")


def publication_name(feed_name: str) -> str:
    """Strip a section label off a feed name, leaving the publication.

    The prompt uses the publication to identify the audience community, so a section
    label leaks a topic word into that slot: 'Hypebeast - Travel' made luggage releases
    and a Naruto theme park tag as Travel rather than Fashion and Anime.

    The two separators follow opposite conventions in this corpus, verified against all
    17 section-named feeds: a dash puts the publication first ('Hypebeast - Travel'),
    while a pipe may put it on either side ('Football | The Guardian' vs 'ROOMIE | 旅行').
    For pipes the publication is reliably the longer side, because section labels are
    short generic words.
    """
    if not feed_name:
        return ""
    parts = _DASH_SPLIT.split(feed_name, maxsplit=1)
    if len(parts) == 2 and parts[0].strip():
        return parts[0].strip()
    parts = [p for p in _PIPE_SPLIT.split(feed_name) if p.strip()]
    if len(parts) == 2:
        return max(parts, key=len).strip()
    return feed_name.strip()


def clean_article_body(text: str) -> str:
    if not text:
        return ""
    # Strip script and style blocks
    cleaned = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip HTML tags
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    # Remove translation badge
    cleaned = re.sub(r'🌐\s*Translated by.*$', '', cleaned, flags=re.IGNORECASE)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:600]

_consecutive_failures = 0

def _call_deepseek(payload: list[dict]) -> dict:
    global _consecutive_failures
    api_key = os.getenv("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)
    if not api_key:
        logger.warning("DEEPSEEK_API_KEY is not set")
        return {"articles": []}

    prompt = f"Classify these articles:\n{json.dumps(payload, ensure_ascii=False)}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    body = {
        "model": TAGGING_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "thinking": {"type": TAGGING_THINKING},
        # Headroom for a reasoning pass, kept even with thinking disabled so that
        # re-enabling it does not silently reintroduce the empty-content failure:
        # at 8192 the model ran out mid-thought and returned no JSON at all.
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    }
    resp = None
    try:
        resp = requests.post(DEEPSEEK_URL, headers=headers, json=body, timeout=240)
        if resp.status_code != 200:
            logger.warning(f"DeepSeek HTTP {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        content = (choice["message"].get("content") or "").strip()

        # A reasoning model that exhausts its budget returns HTTP 200 with an empty
        # `content` and the whole answer stranded in `reasoning_content`. Passing that
        # to json.loads yields a useless "Expecting value: line 1 column 1" — name it.
        if not content:
            reasoning = (choice["message"].get("reasoning_content") or "")
            raise ValueError(
                f"empty content (finish_reason={choice.get('finish_reason')}, "
                f"reasoning_chars={len(reasoning)}) — budget exhausted before the JSON"
            )
        if choice.get("finish_reason") == "length":
            raise ValueError("response truncated (finish_reason=length)")
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0].strip()
        result = json.loads(content)
        _consecutive_failures = 0
        return result
    except Exception as e:
        _consecutive_failures += 1
        logger.warning(f"DeepSeek call failed (consecutive failures: {_consecutive_failures}): {e}")
        if _consecutive_failures >= 3:
            try:
                from .scheduler import _log_event
                _log_event("error", "TAGGING", f"DeepSeek API failing ({_consecutive_failures} consecutive errors): {str(e)[:100]}")
            except Exception:
                pass
        return {"articles": []}

def tag_articles_batch(articles: list[dict]) -> dict[int, dict]:
    if not articles:
        return {}

    out = {}
    # 20 exhausted the token budget on every call during the 2026-08-11 dry run
    # (9 of 10 batches returned nothing). 8 leaves headroom for the reasoning pass.
    batch_size = 8

    for i in range(0, len(articles), batch_size):
        if i > 0 and BATCH_PAUSE_SECONDS > 0:
            time.sleep(BATCH_PAUSE_SECONDS)

        chunk = articles[i:i + batch_size]
        prepared = []
        for art in chunk:
            body = art.get("body") or art.get("content") or ""
            prepared.append({
                "id": art["id"],
                "title": art.get("title", ""),
                "body": clean_article_body(body),
                "feed_name": publication_name(art.get("feed_name", ""))
            })

        sent_ids = {p["id"] for p in prepared}
        remaining = list(prepared)
        chunk_results = {}

        for attempt in range(3):
            if not remaining:
                break
            if attempt > 0 and RETRY_BACKOFF_SECONDS > 0:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            # Budget exhaustion is size-dependent and deterministic: re-sending the
            # same payload fails identically. Halve it on each retry instead.
            send = remaining if attempt == 0 else remaining[:max(1, len(remaining) // (2 * attempt))]
            response = _call_deepseek(send)
            returned_list = response.get("articles", [])
            returned_ids = set()

            for item in returned_list:
                if not isinstance(item, dict):
                    continue
                aid = item.get("id")
                if aid in sent_ids and aid not in chunk_results:
                    primary = item.get("primary")
                    if primary not in CLOSED_TOPICS:
                        logger.warning(f"Unknown primary topic '{primary}' for article {aid}, coercing to 'unknown'")
                        primary = "unknown"

                    region = item.get("region")
                    if region not in CLOSED_REGIONS:
                        region = "unknown"

                    art_type = item.get("type")
                    if art_type not in CLOSED_TYPES:
                        art_type = "unknown"

                    sec = item.get("secondary", [])
                    if not isinstance(sec, list):
                        sec = []

                    chunk_results[aid] = {
                        "primary": primary,
                        "secondary": [str(s) for s in sec],
                        "region": region,
                        "type": art_type,
                        "confidence": item.get("confidence", "high"),
                        "ai_summary": str(item.get("ai_summary", ""))
                    }
                    returned_ids.add(aid)

            missing = {p["id"] for p in remaining} - returned_ids
            remaining = [p for p in remaining if p["id"] in missing]

        out.update(chunk_results)

    return out
