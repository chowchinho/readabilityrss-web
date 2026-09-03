from app.routes.reader import resolve_site_url


def test_strips_feeds_prefix():
    assert resolve_site_url(None, "http://feeds.arstechnica.com/arstechnica/index") == "http://arstechnica.com"


def test_strips_rss_prefix():
    assert resolve_site_url(None, "http://rss.toy-people.com/rss_denden") == "http://toy-people.com"


def test_stored_site_url_wins():
    assert resolve_site_url("https://arstechnica.com", "http://feeds.arstechnica.com/x") == "https://arstechnica.com"


def test_no_prefix_keeps_host():
    assert resolve_site_url(None, "https://www.theverge.com/rss/index.xml") == "https://www.theverge.com"


def test_plain_host_unchanged():
    assert resolve_site_url(None, "https://example.com/feed") == "https://example.com"


def test_does_not_strip_two_label_host():
    # Stripping would leave only a TLD, so keep the host as-is.
    assert resolve_site_url(None, "https://feed.com/rss") == "https://feed.com"
