import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app

client = TestClient(app)

@patch('app.routes.discover.fetch_html')
def test_discover_links(mock_fetch):
    mock_fetch.return_value = '''
    <html>
        <body>
            <div class="article"><h2 class="title"><a href="/article/1">Article One Title</a></h2></div>
            <div class="article"><h2 class="title"><a href="/article/2">Article Two Title</a></h2></div>
            <div class="article"><h2 class="title"><a href="/article/3">Article Three Title</a></h2></div>
        </body>
    </html>
    '''
    response = client.post("/api/discover-links", json={"url": "https://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["total_found"] == 3
    assert len(data["links"]) == 3
    assert data["links"][0]["url"] == "https://example.com/article/1"

@patch('app.routes.discover.fetch_html')
def test_discover_links_with_selector(mock_fetch):
    mock_fetch.return_value = '''
    <html>
        <body>
            <div class="custom-card"><a href="/test1">Test 1</a></div>
            <div class="custom-card"><a href="/test2">Test 2</a></div>
        </body>
    </html>
    '''
    response = client.post("/api/discover-links-with-selector", json={
        "url": "https://example.com",
        "item_selector": ".custom-card",
        "link_selector": "a"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["total_found"] == 2
    assert len(data["links"]) == 2
    assert data["links"][0]["url"] == "https://example.com/test1"

@patch('app.routes.discover.fetch_html')
def test_discover_links_with_rss_placeholder(mock_fetch):
    mock_fetch.return_value = '''<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>Test Feed</title>
            <item>
                <title>Feed Item 1</title>
                <link>https://example.com/feed/1</link>
            </item>
            <item>
                <title>Feed Item 2</title>
                <link>https://example.com/feed/2</link>
            </item>
        </channel>
    </rss>
    '''
    # This selector is what caused the crash before the fix
    response = client.post("/api/discover-links-with-selector", json={
        "url": "https://example.com/rss",
        "item_selector": "item > link (RSS 2.0)",
        "link_selector": "a"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["total_found"] == 2
    assert len(data["links"]) == 2
    assert data["links"][0]["url"] == "https://example.com/feed/1"
    assert data["selector_used"] == "item > link (RSS 2.0)"


def test_discover_links_invalid_scheme_returns_400():
    response = client.post("/api/discover-links", json={"url": "javascript:alert(1)"})
    assert response.status_code == 400
    assert "Invalid URL" in response.json()["detail"]


def test_discover_links_private_ip_returns_400():
    response = client.post("/api/discover-links", json={"url": "http://127.0.0.1/admin"})
    assert response.status_code == 400
    assert "Invalid URL" in response.json()["detail"]


