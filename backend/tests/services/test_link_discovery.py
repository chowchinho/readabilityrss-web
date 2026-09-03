import pytest
from app.services.link_discovery import LinkDiscovery

def test_discover_articles():
    ld = LinkDiscovery()
    html = """
    <html>
        <body>
            <nav><a href="/login">Login</a></nav>
            <div class="article-list">
                <div class="article"><h2 class="title"><a href="/article/1">Article One Title</a></h2></div>
                <div class="article"><h2 class="title"><a href="/article/2">Article Two Title</a></h2></div>
                <div class="article"><h2 class="title"><a href="/article/3">Article Three Title</a></h2></div>
            </div>
            <footer><a href="/about">About</a></footer>
        </body>
    </html>
    """
    res = ld.discover(html, "https://example.com")
    assert len(res['links']) == 3
    assert res['links'][0]['url'] == "https://example.com/article/1"
    assert "h2" in res['selector_used']

def test_discover_filter_social():
    ld = LinkDiscovery()
    html = """
    <html>
        <body>
            <div class="links">
                <h3><a href="https://facebook.com/share">Share</a></h3>
                <h3><a href="https://twitter.com/share">Tweet</a></h3>
                <h3><a href="/real-article-1">Real Article 1</a></h3>
                <h3><a href="/real-article-2">Real Article 2</a></h3>
                <h3><a href="/real-article-3">Real Article 3</a></h3>
            </div>
        </body>
    </html>
    """
    res = ld.discover(html, "https://example.com")
    assert len(res['links']) == 3
    assert "facebook.com" not in [l['url'] for l in res['links']]

def test_discover_rss_feed():
    ld = LinkDiscovery()
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>My News Site</title>
            <link>https://example.com</link>
            <item>
                <title>First Article</title>
                <link>https://example.com/article/1</link>
            </item>
            <item>
                <title>Second Article</title>
                <link>https://example.com/article/2</link>
            </item>
            <item>
                <title>Third Article</title>
                <link>https://example.com/article/3</link>
            </item>
        </channel>
    </rss>
    """
    res = ld.discover(xml, "https://example.com/rss.xml")
    assert len(res['links']) == 3
    assert res['links'][0]['title'] == "First Article"
    assert res['links'][0]['url'] == "https://example.com/article/1"
    assert 'RSS 2.0' in res['selector_used']
    assert res['site_name'] == "My News Site"

def test_discover_atom_feed():
    ld = LinkDiscovery()
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
        <title>Atom Blog</title>
        <entry>
            <title>Post One</title>
            <link href="https://blog.example.com/post/1"/>
        </entry>
        <entry>
            <title>Post Two</title>
            <link href="https://blog.example.com/post/2"/>
        </entry>
    </feed>
    """
    res = ld.discover(xml, "https://blog.example.com/feed")
    assert len(res['links']) == 2
    assert res['links'][0]['url'] == "https://blog.example.com/post/1"
    assert 'Atom' in res['selector_used']

def test_rss_feed_captures_channel_site_url():
    ld = LinkDiscovery()
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
        <channel>
            <title>Ars Technica</title>
            <atom:link href="http://feeds.arstechnica.com/arstechnica/index" rel="self"/>
            <link>https://arstechnica.com</link>
            <item><title>One</title><link>https://arstechnica.com/a/1</link></item>
            <item><title>Two</title><link>https://arstechnica.com/a/2</link></item>
        </channel>
    </rss>
    """
    res = ld.discover(xml, "http://feeds.arstechnica.com/arstechnica/index")
    assert res['site_url'] == "https://arstechnica.com"


def test_atom_feed_captures_alternate_site_url():
    ld = LinkDiscovery()
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
        <title>Atom Blog</title>
        <link href="http://feeds.example.com/atom" rel="self"/>
        <link href="https://blog.example.com" rel="alternate"/>
        <entry><title>Post</title><link href="https://blog.example.com/p/1"/></entry>
    </feed>
    """
    res = ld.discover(xml, "http://feeds.example.com/atom")
    assert res['site_url'] == "https://blog.example.com"


def test_discover_with_selector():
    ld = LinkDiscovery()
    html = """
    <html>
        <body>
            <div class="custom-card"><a href="/test1">Test 1</a></div>
            <div class="custom-card"><a href="/test2">Test 2</a></div>
        </body>
    </html>
    """
    res = ld.discover_with_selector(html, "https://example.com", ".custom-card", "a")
    assert len(res['links']) == 2
    assert res['links'][0]['url'] == "https://example.com/test1"


# --- Exclusion pattern tests ---

def test_derive_exclusion_patterns_external_domains():
    ld = LinkDiscovery()
    included = ["https://bbc.co.uk/news/article-1", "https://bbc.co.uk/news/article-2"]
    excluded = ["https://tiktok.com/bbc", "https://tiktok.com/bbc2", "https://soundcloud.com/bbc"]
    patterns = ld.derive_exclusion_patterns("https://bbc.co.uk", included, excluded)
    domain_patterns = [p for p in patterns if p["type"] == "domain"]
    domains = {p["value"] for p in domain_patterns}
    assert "tiktok.com" in domains
    assert "soundcloud.com" in domains


def test_derive_exclusion_patterns_path_prefix():
    ld = LinkDiscovery()
    included = ["https://bbc.co.uk/news/article-1", "https://bbc.co.uk/news/article-2"]
    excluded = ["https://bbc.co.uk/sounds/play/123", "https://bbc.co.uk/sounds/play/456"]
    patterns = ld.derive_exclusion_patterns("https://bbc.co.uk", included, excluded)
    prefix_patterns = [p for p in patterns if p["type"] == "path_prefix"]
    assert any(p["value"] == "/sounds/" for p in prefix_patterns)


def test_derive_patterns_safety_check():
    """A pattern must never match any included URL."""
    ld = LinkDiscovery()
    # Both included and excluded share /sport/ prefix — pattern should NOT be emitted
    included = ["https://bbc.co.uk/sport/football/article-1"]
    excluded = ["https://bbc.co.uk/sport/cricket/results"]
    patterns = ld.derive_exclusion_patterns("https://bbc.co.uk", included, excluded)
    # /sport/ prefix should NOT appear since it would match the included URL
    prefix_patterns = [p for p in patterns if p["type"] == "path_prefix" and p["value"] == "/sport/"]
    assert len(prefix_patterns) == 0


def test_apply_exclusion_patterns():
    links = [
        {"url": "https://bbc.co.uk/news/article-1", "title": "News 1"},
        {"url": "https://tiktok.com/bbc", "title": "TikTok"},
        {"url": "https://bbc.co.uk/sounds/play/123", "title": "Sounds"},
        {"url": "https://bbc.co.uk/sport/results", "title": "Sport"},
        {"url": "https://bbc.co.uk/news/article-2", "title": "News 2"},
    ]
    patterns = [
        {"type": "domain", "value": "tiktok.com"},
        {"type": "path_prefix", "value": "/sounds/"},
        {"type": "path_contains", "value": "/sport/"},
    ]
    result = LinkDiscovery.apply_exclusion_patterns(links, patterns)
    urls = [l["url"] for l in result]
    assert len(result) == 2
    assert "https://bbc.co.uk/news/article-1" in urls
    assert "https://bbc.co.uk/news/article-2" in urls


def test_apply_empty_patterns():
    links = [
        {"url": "https://example.com/a", "title": "A"},
        {"url": "https://example.com/b", "title": "B"},
    ]
    result = LinkDiscovery.apply_exclusion_patterns(links, [])
    assert len(result) == 2
