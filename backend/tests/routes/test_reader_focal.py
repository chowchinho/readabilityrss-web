import asyncio

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import app
import app.routes.reader as reader_module

IMAGE_HASH = "abc123def456"


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Isolated DB holding one source and one article with a cached main image."""
    database = Database(db_path=str(tmp_path / "feeds.db"))

    async def _seed():
        await database.init()
        conn = await database._get_db()
        await conn.execute(
            "INSERT INTO feed_sources (id, name, url) VALUES (1, 'Test Feed', 'https://e.com/f')"
        )
        await conn.execute(
            "INSERT INTO feed_articles (id, source_id, url, title, content, main_image, parse_status) "
            "VALUES (1, 1, 'https://e.com/a', 'Title', '<p>Body</p>', ?, 'success')",
            (f"/api/reader/cached-image/{IMAGE_HASH}.jpg",),
        )
        await conn.commit()

    asyncio.run(_seed())
    monkeypatch.setattr(reader_module, "db", database)
    yield database
    asyncio.run(database.close())


def test_articles_include_focal_fields(seeded_db):
    """Every article carries focal fields, defaulting to centre when unset."""
    client = TestClient(app)
    response = client.get("/api/reader/articles?limit=5")
    assert response.status_code == 200
    articles = response.json()["articles"]
    assert articles, "fixture should have seeded one article"
    for article in articles:
        assert isinstance(article["focal_x"], int)
        assert isinstance(article["focal_y"], int)
        assert 0 <= article["focal_x"] <= 100
        assert 0 <= article["focal_y"] <= 100


def test_defaults_to_centre_when_no_focal_stored(seeded_db):
    client = TestClient(app)
    article = client.get("/api/reader/articles?source_id=1").json()["articles"][0]
    assert (article["focal_x"], article["focal_y"]) == (50, 50)


def test_stored_focal_point_reaches_the_payload(seeded_db):
    """A stored focal point must appear, not be overwritten by the default."""
    asyncio.run(seeded_db.set_focal_point(IMAGE_HASH, 20, 80))
    client = TestClient(app)
    article = client.get("/api/reader/articles?source_id=1").json()["articles"][0]
    assert (article["focal_x"], article["focal_y"]) == (20, 80)
