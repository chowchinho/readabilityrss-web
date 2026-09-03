import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

import app.routes.events as events_module
import app.routes.ranking as ranking_module
import app.routes.reader as reader_module
import app.utils.fever_key as fever_key_module
from app.database import Database
from app.main import app

VALID_API_KEY = hashlib.md5(b"user:pass").hexdigest()


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_ranking_api.db"))
    asyncio.run(database.init())
    asyncio.run(database.set_fever_auth("user", VALID_API_KEY))
    monkeypatch.setattr(ranking_module, "db", database)
    monkeypatch.setattr(reader_module, "db", database)
    monkeypatch.setattr(events_module, "db", database)
    monkeypatch.setattr(fever_key_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client():
    return TestClient(app)


async def seed_articles(test_db):
    sid = await test_db.create_feed_source({"name": "Source 1", "url": "http://example.com/rss"})
    await test_db.insert_feed_articles(sid, [
        {"url": "http://example.com/1"},
        {"url": "http://example.com/2"},
    ])
    conn = await test_db._get_db()
    cursor = await conn.execute("SELECT id FROM feed_articles WHERE source_id = ? ORDER BY id ASC", (sid,))
    rows = await cursor.fetchall()
    id1, id2 = rows[0]["id"], rows[1]["id"]

    await test_db.update_article_parsed(id1, "Title 1", "<p>Content 1</p>", "2026-08-10 10:00:00", "")
    await test_db.save_article_tags(id1, {
        "primary": "Consumer Tech", "secondary": ["Samsung"],
        "region": "Global", "type": "News",
        "confidence": "high", "ai_summary": "Summary 1",
    })

    await test_db.update_article_parsed(id2, "Title 2", "<p>Content 2</p>", "2026-08-11 10:00:00", "")
    await test_db.save_article_tags(id2, {
        "primary": "Travel", "secondary": ["Japan"],
        "region": "Japan", "type": "Feature",
        "confidence": "high", "ai_summary": "Summary 2",
    })

    await conn.execute("UPDATE feed_articles SET updated_at = '2026-08-10 10:00:00' WHERE id = ?", (id1,))
    await conn.execute("UPDATE feed_articles SET updated_at = '2026-08-12 10:00:00' WHERE id = ?", (id2,))
    await conn.commit()

    return id1, id2


# 1. A wrong or missing api_key is rejected on both endpoints.
def test_auth_rejected_on_wrong_or_missing_api_key(test_db, client):
    r1 = client.get("/api/reader/ranking/scores")
    assert r1.status_code == 401

    r2 = client.get("/api/reader/ranking/scores?api_key=wrongkey")
    assert r2.status_code == 401

    r3 = client.post("/api/reader/ranking/feedback", json={"api_key": "wrongkey", "votes": []})
    assert r3.status_code == 401

    r4 = client.post("/api/reader/ranking/feedback", json={"votes": []})
    assert r4.status_code == 401


# 2. scores returns every article in the retention window, not a delta.
def test_scores_returns_all_articles_in_retention_window_not_delta(test_db, client):
    id1, id2 = asyncio.run(seed_articles(test_db))

    resp = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}&since=2026-08-13T00:00:00Z")
    assert resp.status_code == 200
    data = resp.json()

    score_ids = [item[0] for item in data["scores"]]
    assert id1 in score_ids
    assert id2 in score_ids
    assert len(score_ids) == 2


# 3. tags with a since watermark returns only articles changed after it.
def test_tags_filters_by_since_watermark(test_db, client):
    id1, id2 = asyncio.run(seed_articles(test_db))

    resp = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}&since=2026-08-11T00:00:00Z")
    assert resp.status_code == 200
    data = resp.json()

    tag_ids = [t["id"] for t in data["tags"]]
    assert id2 in tag_ids
    assert id1 not in tag_ids


# 4. weights contains no label absent from the returned article set.
def test_weights_pruned_contains_no_absent_label(test_db, client):
    id1, id2 = asyncio.run(seed_articles(test_db))

    resp = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}")
    assert resp.status_code == 200
    data = resp.json()
    weights = data["weights"]

    topic_labels = {w["label"] for w in weights["topic"]}
    type_labels = {w["label"] for w in weights["type"]}
    region_labels = {w["label"] for w in weights["region"]}
    secondary_canonicals = {w["canonical"] for w in weights["secondary"]}

    assert topic_labels.issubset({"Consumer Tech", "Travel"})
    assert type_labels.issubset({"News", "Feature"})
    assert region_labels.issubset({"Global", "Japan"})
    assert secondary_canonicals.issubset({"samsung", "japan"})
    assert "Politics" not in topic_labels


# 5. A vote with mark_read: false records the vote and leaves is_read at 0.
def test_vote_mark_read_false_leaves_is_read_0(test_db, client):
    id1, _ = asyncio.run(seed_articles(test_db))

    resp = client.post(f"/api/reader/articles/{id1}/vote", json={"vote": "show_more", "mark_read": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_read"] == 0

    async def _check():
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT is_read FROM feed_articles WHERE id = ?", (id1,))
        r = await cursor.fetchone()
        assert r["is_read"] == 0
        votes = await test_db.get_article_votes()
        assert any(v["article_id"] == id1 and v["vote"] == "show_more" for v in votes)

    asyncio.run(_check())


# 6. A vote with mark_read absent marks the article read (the SPA's existing behaviour).
def test_vote_mark_read_absent_marks_read_1(test_db, client):
    id1, _ = asyncio.run(seed_articles(test_db))

    resp = client.post(f"/api/reader/articles/{id1}/vote", json={"vote": "show_more"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_read"] == 1

    async def _check():
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT is_read FROM feed_articles WHERE id = ?", (id1,))
        r = await cursor.fetchone()
        assert r["is_read"] == 1

    asyncio.run(_check())


# 7. Batched feedback with a duplicate article_id produces exactly one article_votes row.
def test_batched_feedback_duplicate_article_id_produces_one_vote_row(test_db, client):
    id1, _ = asyncio.run(seed_articles(test_db))

    resp = client.post("/api/reader/ranking/feedback", json={
        "api_key": VALID_API_KEY,
        "votes": [
            {"article_id": id1, "vote": "show_more", "mark_read": False},
            {"article_id": id1, "vote": "show_less", "mark_read": False},
        ],
    })
    assert resp.status_code == 200

    async def _check():
        conn = await test_db._get_db()
        cursor = await conn.execute("SELECT COUNT(*) as count, vote FROM article_votes WHERE article_id = ?", (id1,))
        r = await cursor.fetchone()
        assert r["count"] == 1
        assert r["vote"] == "show_less"

    asyncio.run(_check())


# 8. With ai_enabled off, feedback returns 200 and writes nothing.
def test_ai_enabled_off_feedback_returns_200_and_writes_nothing(test_db, client):
    id1, _ = asyncio.run(seed_articles(test_db))

    async def _disable_ai():
        await test_db.update_system_settings({"ai_enabled": False})

    asyncio.run(_disable_ai())

    resp = client.post("/api/reader/ranking/feedback", json={
        "api_key": VALID_API_KEY,
        "votes": [{"article_id": id1, "vote": "show_more", "mark_read": False}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["votes_processed"] == 0

    async def _check():
        votes = await test_db.get_article_votes()
        assert len(votes) == 0

    asyncio.run(_check())


# 9. Reconciliation: extras freshness and pair terms, summed with axis contributions, equal score.
def test_reconciliation_extras_plus_weights_equals_score(test_db, client):
    id1, id2 = asyncio.run(seed_articles(test_db))

    resp = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}")
    assert resp.status_code == 200
    data = resp.json()

    scores_dict = dict(data["scores"])
    extras = data["extras"]
    tags_by_id = {t["id"]: t for t in data["tags"]}
    weights = data["weights"]

    weight_map = {}
    for axis, rows in weights.items():
        weight_map[axis] = {}
        for r in rows:
            key = r.get("canonical", r["label"])
            weight_map[axis][key] = r["effective"]

    from app.services.labels import canonical_label
    from app.services.ranking import DECLARED_WEIGHTS, SECONDARY_FACTOR

    for art_id, score in scores_dict.items():
        tag = tags_by_id[art_id]
        ext = extras[str(art_id)]

        topic_eff = weight_map["topic"].get(tag["primary"], float(DECLARED_WEIGHTS["topic"].get(tag["primary"], 0)))
        type_eff = weight_map["type"].get(tag["type"], float(DECLARED_WEIGHTS["type"].get(tag["type"], 0)))
        region_eff = weight_map["region"].get(tag["region"], float(DECLARED_WEIGHTS["region"].get(tag["region"], 0)))

        raw_sec = 0.0
        for sec in tag["secondary"]:
            csec = canonical_label(sec)
            raw_sec += weight_map["secondary"].get(csec, float(DECLARED_WEIGHTS["secondary"].get(csec, 0)))
        sec_subtotal = SECONDARY_FACTOR * raw_sec
        max_sec = abs(topic_eff)
        if sec_subtotal > max_sec:
            sec_subtotal = max_sec
        elif sec_subtotal < -max_sec:
            sec_subtotal = -max_sec

        pairs_sum = sum(p[1] for p in ext["pairs"])
        freshness = ext["freshness"]
        exposure = ext.get("exposure", 0.0)
        vote_penalty = -4.0 if tag["vote"] == "show_less" else 0.0

        calc_score = round(topic_eff + type_eff + region_eff + sec_subtotal + pairs_sum + freshness + exposure + vote_penalty, 2)
        assert abs(calc_score - score) <= 0.02


def test_gzip_decompression_matches_uncompressed(test_db, client):
    asyncio.run(seed_articles(test_db))

    res_raw = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}")
    assert res_raw.status_code == 200

    res_gzip = client.get(
        f"/api/reader/ranking/scores?api_key={VALID_API_KEY}",
        headers={"Accept-Encoding": "gzip"},
    )
    assert res_gzip.status_code == 200
    assert res_gzip.headers.get("content-encoding") == "gzip"

    # `generated_at` is wall-clock at second precision, so two sequential requests
    # straddling a second boundary differ on it and only on it. Comparing whole bodies
    # made this test fail roughly one run in four for a reason unrelated to gzip.
    raw, gzipped = res_raw.json(), res_gzip.json()
    assert raw.pop("generated_at") is not None
    assert gzipped.pop("generated_at") is not None
    assert gzipped == raw


def test_cors_headers_present_on_gzip_request(test_db, client):
    res = client.get(
        f"/api/reader/ranking/scores?api_key={VALID_API_KEY}",
        headers={"Origin": "http://localhost:5173", "Accept-Encoding": "gzip"},
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") in [
        "http://localhost:5173",
        "*",
    ]


def test_since_id_and_max_id_bounds(test_db, client):
    id1, id2 = asyncio.run(seed_articles(test_db))

    # Test since_id
    res_since = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}&since_id={id1}")
    assert res_since.status_code == 200
    data_since = res_since.json()
    score_ids_since = [item[0] for item in data_since["scores"]]
    assert score_ids_since == [id2]
    assert list(data_since["extras"].keys()) == [str(id2)]
    assert [t["id"] for t in data_since["tags"]] == [id2]

    # Test max_id
    res_max = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}&max_id={id2}")
    assert res_max.status_code == 200
    data_max = res_max.json()
    score_ids_max = [item[0] for item in data_max["scores"]]
    assert score_ids_max == [id1]
    assert list(data_max["extras"].keys()) == [str(id1)]
    assert [t["id"] for t in data_max["tags"]] == [id1]


def test_weights_pruned_to_bounded_articles(test_db, client):
    _id1, id2 = asyncio.run(seed_articles(test_db))

    # Article 1 has topic Consumer Tech, Travel is only on Article 2
    res = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}&max_id={id2}")
    assert res.status_code == 200
    data = res.json()
    topic_labels = {w["label"] for w in data["weights"]["topic"]}
    assert "Consumer Tech" in topic_labels
    assert "Travel" not in topic_labels


def test_exposure_in_extras_for_article_with_impressions(test_db, client):
    id1, id2 = asyncio.run(seed_articles(test_db))

    # Record impressions for id1
    async def _add_impressions():
        await test_db.insert_events([
            {"article_id": id1, "event_type": "impression"},
            {"article_id": id1, "event_type": "impression"},
        ])
    asyncio.run(_add_impressions())

    res = client.get(f"/api/reader/ranking/scores?api_key={VALID_API_KEY}")
    assert res.status_code == 200
    data = res.json()

    extras = data["extras"]
    assert str(id1) in extras
    assert str(id2) in extras

    # id1 has impressions so exposure should be < 0
    assert extras[str(id1)]["exposure"] < 0.0
    # id2 has no impressions so exposure should be 0.0
    assert extras[str(id2)]["exposure"] == 0.0

