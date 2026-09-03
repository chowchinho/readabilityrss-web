"""Download real article pages spanning diverse parser failure modes.

The parity corpus validates that parser optimizations produce byte-identical output
across real-world HTML structures: lazy-loaded images, <noscript> fallbacks,
structured data, non-Latin scripts (Japanese, Traditional Chinese), galleries,
and large component-heavy DOMs.

HTML files are saved to backend/tests/fixtures/parity_corpus/ (gitignored).
Expectation files are stored in backend/tests/fixtures/parity/ (committed).

Usage, from backend/:
    python scripts/fetch_parity_corpus.py
"""

import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = os.path.join(HERE, "tests", "fixtures", "parity_corpus")

CORPUS_ENTRIES = [
    {
        "id": "p1_funq",
        "url": "https://www.funq.jp/randonnee/article/1068247/",
        "desc": "Japanese outdoor article with rich image galleries and lazy loading",
        "failure_modes": ["non_latin", "galleries", "lazy_images"],
    },
    {
        "id": "p2_hobby_watch",
        "url": "https://hobby.watch.impress.co.jp/docs/news/2112227.html",
        "desc": "Impress Watch Japanese figure hobby news with multiple product photos",
        "failure_modes": ["non_latin", "galleries", "image_recovery"],
    },
    {
        "id": "p3_internet_watch",
        "url": "https://internet.watch.impress.co.jp/docs/ranking/mousepro/2111089.html",
        "desc": "Japanese tech news with structured product specifications and tables",
        "failure_modes": ["non_latin", "structured_data"],
    },
    {
        "id": "p4_love_walker",
        "url": "https://lovewalker.jp/elem/000/004/385/4385643/",
        "desc": "Kadokawa Love Walker Japanese lifestyle and travel article",
        "failure_modes": ["non_latin", "custom_cms"],
    },
    {
        "id": "p5_kaelife",
        "url": "https://kaelife.hondaaccess.jp/entry/20260526_01",
        "desc": "Honda Kaelife lifestyle blog with JavaScript-driven lazy loading",
        "failure_modes": ["non_latin", "lazy_images", "js_rendered"],
    },
    {
        "id": "p6_transit",
        "url": "https://transit.jp/sponsored/newzealandairline_christchurch/",
        "desc": "Transit.jp Japanese travel magazine layout with picture/srcset responsive images",
        "failure_modes": ["non_latin", "galleries", "picture_srcset"],
    },
    {
        "id": "p7_kaden_watch",
        "url": "https://kaden.watch.impress.co.jp/docs/news/2112195.html",
        "desc": "Kaden Watch Japanese consumer electronics article",
        "failure_modes": ["non_latin", "standard_article"],
    },
    {
        "id": "p8_toypeople",
        "url": "https://www.toy-people.com/?p=113464",
        "desc": "Toy People News Traditional Chinese hobby figures and product gallery",
        "failure_modes": ["non_latin", "traditional_chinese", "galleries", "image_recovery"],
    },
    {
        "id": "p9_theverge",
        "url": "https://www.theverge.com/ai-artificial-intelligence/983502/limewire-ai-music-generator",
        "desc": "The Verge (Vox Media, 368 KB) with JSON-LD structured data and picture/srcset",
        "failure_modes": ["json_ld", "picture_srcset", "modern_cms"],
    },
    {
        "id": "p10_bbc",
        "url": "https://www.bbc.co.uk/news/articles/c5y4j92rv24o?at_medium=RSS",
        "desc": "BBC News (512 KB) component-heavy DOM triggering <article> auto-fallback",
        "failure_modes": ["article_fallback", "large_page", "component_dom"],
    },
    {
        "id": "p11_skysports",
        "url": "https://www.skysports.com/football/video/19540/13575653/arsenal-vs-coventry-gary-neville-believes-arsenal-still-lack-a-world-class-attacker",
        "desc": "Sky Sports video & sports report (264 KB) with video embeds and social markup",
        "failure_modes": ["video_embeds", "rich_media"],
    },
    {
        "id": "p12_gizmodo",
        "url": "https://gizmodo.com/the-odyssey-comes-home-in-november-2000854483",
        "desc": "Gizmodo (G/O Media, 214 KB) with noscript fallbacks and lead media embeds",
        "failure_modes": ["noscript", "media_embeds"],
    },
    {
        "id": "p13_futurism",
        "url": "https://futurism.com/health-medicine/startup-radiation-exposure-klarna-nuclear-armageddon",
        "desc": "Futurism science and tech reporting (168 KB) with author bio blocks",
        "failure_modes": ["metadata", "boilerplate_filtering"],
    },
    {
        "id": "p14_yankodesign",
        "url": "https://www.yankodesign.com/2026/08/21/bevel-is-set-to-become-the-tallest-mass-timber-office-building-in-north-america/",
        "desc": "Yanko Design WordPress layout (133 KB) with image galleries and lazy plugins",
        "failure_modes": ["wordpress_plugins", "galleries", "lazy_images"],
    },
    {
        "id": "p15_roomietw",
        "url": "https://www.roomie.tw/posts/168874",
        "desc": "ROOMIE Taiwan Traditional Chinese lifestyle article with product photography",
        "failure_modes": ["non_latin", "traditional_chinese", "product_images"],
    },
    {
        "id": "p16_arstechnica",
        "url": "https://arstechnica.com/tech-policy/2026/08/class-action-accuses-brokers-of-hiding-zillow-listings-driving-up-nyc-rents/",
        "desc": "Ars Technica (Condé Nast, 155 KB) with inline figures, captions and noscript tags",
        "failure_modes": ["noscript", "inline_figures", "conde_nast"],
    },
    {
        "id": "p17_popeye",
        "url": "https://popeyemagazine.jp/post-293671/",
        "desc": "POPEYE Magazine Japanese lifestyle with custom data-srcset attributes",
        "failure_modes": ["non_latin", "data_srcset", "lazy_images"],
    },
    {
        "id": "p18_asciijp",
        "url": "https://ascii.jp/limit/group/ida/elem/000/004/427/4427351/?rss",
        "desc": "ASCII.jp Japanese IT news with Kadokawa metadata date formats",
        "failure_modes": ["non_latin", "date_formats", "kadokawa"],
    },
    {
        "id": "p19_hk01",
        "url": "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%9C%8B%E9%9A%9B/60382536/tiktok%E8%A2%AB%E6%8E%A7%E9%81%95%E5%8F%8D%E5%85%92%E7%AB%A5%E9%9A%B1%E7%A7%81-%E7%A0%B831%E5%84%84%E8%88%87%E7%BE%8E%E5%9C%8B%E5%8F%B8%E6%B3%95%E9%83%A8%E9%81%94%E6%88%90%E5%92%8C%E8%A7%A3",
        "desc": "HK01 (Hong Kong 01) Traditional Chinese Next.js news layout (485 KB)",
        "failure_modes": ["non_latin", "traditional_chinese", "nextjs", "large_page"],
    },
    {
        "id": "p20_theguardian",
        "url": "https://www.theguardian.com/football/2026/aug/18/chelsea-todd-boehly-mark-walter-clearlake-capital-ownership",
        "desc": "The Guardian (337 KB) rich media sports report with structured metadata",
        "failure_modes": ["structured_metadata", "rich_media", "guardian_cms"],
    },
]


def fetch_all():
    os.makedirs(CORPUS_DIR, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8,zh-TW;q=0.7",
    }

    fetched = skipped = failed = 0
    for entry in CORPUS_ENTRIES:
        filepath = os.path.join(CORPUS_DIR, f"{entry['id']}.html")
        meta_path = os.path.join(CORPUS_DIR, f"{entry['id']}.meta.json")

        if os.path.exists(filepath):
            skipped += 1
            continue

        try:
            req = urllib.request.Request(entry["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read()

            with open(filepath, "wb") as f:
                f.write(content)

            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2, ensure_ascii=False)

            kb = len(content) // 1024
            print(f"  FETCHED {entry['id']} ({kb} KB) from {entry['url'][:50]}")
            fetched += 1
        except Exception as exc:
            failed += 1
            print(f"  FAIL {entry['id']}: {exc} (URL: {entry['url']})")

    print(f"\nParity Corpus: fetched={fetched}, skipped={skipped}, failed={failed}, total={len(CORPUS_ENTRIES)}")
    return failed == 0


if __name__ == "__main__":
    success = fetch_all()
    if not success:
        sys.exit(1)
