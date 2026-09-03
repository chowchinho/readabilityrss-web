import asyncio
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import app
import app.routes.reader as reader_module
import app.routes.events as events_module

@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_ranking.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(reader_module, "db", database)
    monkeypatch.setattr(events_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def article_id(test_db):
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Feed R", "url": "http://example.com/r/rss"})
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/r/1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        aid = (await cursor.fetchone())["id"]
        await test_db.update_article_parsed(aid, "R1", "<p>Body</p>", "2026-08-08", "")
        await test_db.save_article_tags(aid, {
            "primary": "Consumer Tech", "secondary": ["Samsung"],
            "region": "Global", "type": "News",
            "confidence": "high", "ai_summary": "Summary",
        })
        return aid
    return asyncio.run(_seed())

def test_sort_smart_returns_same_article_count_as_latest(test_db):
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Feed A", "url": "http://example.com/rss"})
        await test_db.insert_feed_articles(sid, [
            {"url": "http://example.com/1"},
            {"url": "http://example.com/2"},
            {"url": "http://example.com/3"},
        ])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        rows = await cursor.fetchall()
        for r in rows:
            art_id = r["id"]
            await test_db.update_article_parsed(art_id, f"Title {art_id}", "Content", "2026-08-08", "")
            await test_db.save_article_tags(art_id, {
                "primary": "Travel",
                "secondary": ["Japan"],
                "region": "Japan",
                "type": "Feature",
                "confidence": "high",
                "ai_summary": "Summary"
            })
        return sid

    sid = asyncio.run(_seed())
    client = TestClient(app)

    resp_latest = client.get(f"/api/reader/articles?source_id={sid}&sort=latest")
    assert resp_latest.status_code == 200
    latest_data = resp_latest.json()

    resp_smart = client.get(f"/api/reader/articles?source_id={sid}&sort=smart")
    assert resp_smart.status_code == 200
    smart_data = resp_smart.json()

    latest_arts = latest_data["articles"]
    smart_arts = smart_data["articles"]

    assert len(smart_arts) == len(latest_arts)
    assert set(a["id"] for a in smart_arts) == set(a["id"] for a in latest_arts)

    for art in smart_arts:
        assert "topics" in art
        assert "ai_summary" in art
        assert "recommendation_reason" in art
        assert art["topics"]["primary"] == "Travel"

def test_events_endpoint_copies_labels_onto_the_event(test_db):
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Feed B", "url": "http://example.com/rss2"})
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/item1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        r = await cursor.fetchone()
        art_id = r["id"]
        await test_db.update_article_parsed(art_id, "Item 1", "Content", "2026-08-08", "")
        await test_db.save_article_tags(art_id, {
            "primary": "Gaming",
            "secondary": ["Retro"],
            "region": "Global",
            "type": "Review",
            "confidence": "high",
            "ai_summary": "Game review"
        })
        return art_id

    art_id = asyncio.run(_seed())
    client = TestClient(app)

    payload = {
        "events": [
            {
                "article_id": art_id,
                "event_type": "open",
                "dwell_seconds": 15
            }
        ]
    }
    resp = client.post("/api/reader/events", json=payload)
    assert resp.status_code == 200

    events = asyncio.run(test_db.get_events())
    assert len(events) >= 1
    ev = [e for e in events if e["article_id"] == art_id and e["event_type"] == "open"][0]
    assert ev["primary_topic"] == "Gaming"
    assert ev["region"] == "Global"
    assert ev["article_type"] == "Review"


def test_every_sort_mode_returns_the_same_articles(test_db):
    """All three modes are orderings. None of them may drop or invent an article."""
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Feed C", "url": "http://example.com/rss3"})
        await test_db.insert_feed_articles(
            sid, [{"url": f"http://example.com/s/{i}"} for i in range(12)])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        for i, r in enumerate(await cursor.fetchall()):
            await test_db.update_article_parsed(r["id"], f"T{i}", "Content", "2026-08-08", "")
            # Deliberately mixed labels so smart has a real ordering to produce.
            await test_db.save_article_tags(r["id"], {
                "primary": "Travel" if i % 2 else "Politics",
                "secondary": [], "region": "Japan" if i % 3 else "United States",
                "type": "Feature" if i % 2 else "Deal",
                "confidence": "high", "ai_summary": "s",
            })
        return sid

    sid = asyncio.run(_seed())
    client = TestClient(app)

    results = {}
    for mode in ("smart", "latest", "random"):
        resp = client.get(f"/api/reader/articles?source_id={sid}&sort={mode}")
        assert resp.status_code == 200, mode
        results[mode] = [a["id"] for a in resp.json()["articles"]]

    baseline = set(results["latest"])
    assert len(baseline) == 12
    for mode, ids in results.items():
        assert len(ids) == 12, f"{mode} changed the article count"
        assert set(ids) == baseline, f"{mode} dropped or invented an article"

    # An unknown value must fall back to chronological, never to an empty page.
    resp = client.get(f"/api/reader/articles?source_id={sid}&sort=nonsense")
    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()["articles"]] == results["latest"]


def test_random_is_stable_for_the_same_page(test_db):
    """Paging must not reshuffle underneath the reader between identical requests."""
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Feed D", "url": "http://example.com/rss4"})
        await test_db.insert_feed_articles(
            sid, [{"url": f"http://example.com/r/{i}"} for i in range(10)])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        for i, r in enumerate(await cursor.fetchall()):
            await test_db.update_article_parsed(r["id"], f"R{i}", "Content", "2026-08-08", "")
        return sid

    sid = asyncio.run(_seed())
    client = TestClient(app)
    url = f"/api/reader/articles?source_id={sid}&sort=random"
    first = [a["id"] for a in client.get(url).json()["articles"]]
    second = [a["id"] for a in client.get(url).json()["articles"]]
    assert first == second


def test_single_article_endpoint_returns_its_tags(test_db):
    """The list endpoint returned tags but the detail endpoint did not, so opening
    an article showed 'unknown' even when it had been tagged hours earlier."""
    async def _seed():
        sid = await test_db.create_feed_source({"name": "Feed E", "url": "http://example.com/rss5"})
        await test_db.insert_feed_articles(sid, [{"url": "http://example.com/detail/1"}])
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ?", (sid,))
        art_id = (await cursor.fetchall())[0]["id"]
        await test_db.update_article_parsed(art_id, "Detail", "<p>Body</p>", "2026-08-08", "")
        await test_db.save_article_tags(art_id, {
            "primary": "Anime & Manga", "secondary": ["Figures"],
            "region": "Japan", "type": "Product Launch",
            "confidence": "high", "ai_summary": "A summary.",
        })
        return art_id

    art_id = asyncio.run(_seed())
    body = TestClient(app).get(f"/api/reader/articles/{art_id}").json()

    assert body["topics"]["primary"] == "Anime & Manga"
    assert body["topics"]["region"] == "Japan"
    assert body["topics"]["type"] == "Product Launch"
    assert body["topics"]["secondary"] == ["Figures"]
    assert body["ai_summary"] == "A summary."
    assert "Anime & Manga" in body["recommendation_reason"]
    assert isinstance(body["score"], (int, float))


def test_articles_carry_their_stored_vote(client, test_db, article_id):
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_less"})
    listed = client.get("/api/reader/articles").json()["articles"]
    row = next(a for a in listed if a["id"] == article_id)
    assert row["vote"] == "show_less"


def test_unvoted_articles_report_a_null_vote(client, test_db, article_id):
    listed = client.get("/api/reader/articles").json()["articles"]
    assert all(a["vote"] is None for a in listed)


def test_a_disliked_article_scores_lower_than_before(client, test_db, article_id):
    before = next(a for a in client.get("/api/reader/articles").json()["articles"]
                  if a["id"] == article_id)["score"]
    client.post(f"/api/reader/articles/{article_id}/vote", json={"vote": "show_less"})
    after = next(a for a in client.get("/api/reader/articles").json()["articles"]
                 if a["id"] == article_id)["score"]
    assert after < before
