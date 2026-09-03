"""Tests for RSS 2.0 feed and OPML export routes."""
import asyncio
import xml.etree.ElementTree as ET
import pytest
from fastapi.testclient import TestClient

import app.routes.feeds as feeds_module
from app.database import Database
from app.main import app


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_feeds_routes.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(feeds_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client():
    return TestClient(app)


def test_rss_feed_not_found(test_db, client):
    resp = client.get("/feed/99999/rss")
    assert resp.status_code == 404


def test_rss_feed_structure_and_enclosure(test_db, client):
    async def _setup():
        cat_id = await test_db.create_category("Technology")
        sid = await test_db.create_feed_source({
            "name": "Tech News",
            "url": "https://tech.example.com",
            "category_id": cat_id,
        })
        await test_db.insert_feed_articles(sid, [
            {
                "url": "https://tech.example.com/article1",
                "title": "Quantum Computing Advances",
                "pub_date": "2026-08-20",
                "main_image": "/api/reader/article-image/abc123456",
            },
            {
                "url": "https://tech.example.com/article2",
                "title": "AI in Astronomy",
                "pub_date": "2026-08-21",
                "main_image": "https://cdn.example.com/star.jpg",
            }
        ])
        articles = await test_db.get_pending_articles(sid)
        images = ["/api/reader/article-image/abc123456", "https://cdn.example.com/star.jpg"]
        for idx, a in enumerate(articles):
            await test_db.update_article_parsed(
                a["id"],
                f"Article Title {idx+1}",
                f"<p>Full content for {a['url']}</p>",
                "2026-08-20",
                images[idx % len(images)],
            )
        return sid

    sid = asyncio.run(_setup())

    resp = client.get(f"/feed/{sid}/rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["content-type"]

    root = ET.fromstring(resp.content)
    assert root.tag == "rss"
    assert root.get("version") == "2.0"

    channel = root.find("channel")
    assert channel is not None
    assert channel.find("title").text == "Tech News"
    assert channel.find("link").text == "https://tech.example.com"

    items = channel.findall("item")
    assert len(items) == 2

    first_item = items[0]
    assert first_item.find("title").text is not None
    assert first_item.find("link").text.startswith("https://tech.example.com/")
    assert first_item.find("guid").text.startswith("https://tech.example.com/")

    # Check content:encoded
    encoded = first_item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    assert encoded is not None
    assert "<p>Full content for" in encoded.text

    # Check enclosures
    enclosures = [item.find("enclosure") for item in items]
    assert all(enc is not None for enc in enclosures)
    assert any("abc123456" in enc.get("url") for enc in enclosures)
    assert any("star.jpg" in enc.get("url") for enc in enclosures)


def test_export_opml_format_and_grouping(test_db, client):
    async def _setup():
        cat_tech = await test_db.create_category("Tech")
        cat_sci = await test_db.create_category("Science")
        cat_other = await test_db.create_category("Other")

        s1 = await test_db.create_feed_source({
            "name": "Ars Technica",
            "url": "https://arstechnica.com",
            "category_id": cat_tech,
        })
        s2 = await test_db.create_feed_source({
            "name": "Nature News",
            "url": "https://nature.com/news",
            "category_id": cat_sci,
        })
        s3 = await test_db.create_feed_source({
            "name": "Disabled Feed",
            "url": "https://disabled.example.com",
            "category_id": cat_other,
        })
        await test_db.update_feed_source(s3, {"enabled": 0})
        return s1, s2, s3

    asyncio.run(_setup())

    resp = client.get("/feed/opml")
    assert resp.status_code == 200
    assert "text/x-opml" in resp.headers["content-type"]
    assert 'attachment; filename="readabilityrss.opml"' in resp.headers["content-disposition"]

    root = ET.fromstring(resp.content)
    assert root.tag == "opml"
    assert root.get("version") == "2.0"

    body = root.find("body")
    assert body is not None

    category_outlines = body.findall("outline")
    category_names = {cat.get("text") for cat in category_outlines}
    assert "Tech" in category_names
    assert "Science" in category_names
    # Disabled source was excluded
    assert "Other" not in category_names

    feed_outlines = body.findall(".//outline[@type='rss']")
    assert len(feed_outlines) == 2
    feed_names = {f.get("text") for f in feed_outlines}
    assert "Ars Technica" in feed_names
    assert "Nature News" in feed_names


def test_activity_log_and_refresh_status(test_db, client):
    resp = client.get("/api/activity-log?since=0")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data

    status_resp = client.get("/api/refresh-status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert "refreshing" in status_data
    assert "parse_done" in status_data
    assert "parse_total" in status_data
