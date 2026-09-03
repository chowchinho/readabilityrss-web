import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

import app.routes.reader as reader_module
import app.utils.fever_key as fever_key_module
from app.database import Database
from app.main import app

VALID_API_KEY = hashlib.md5(b"user:pass").hexdigest()

HASH_STORED_1 = "4a3a78b7a1b2c3d4e5f60718293a4b5c"
HASH_STORED_2 = "9e2cf3780123456789abcdef01234567"
HASH_STORED_3 = "f0e1d2c3b4a5968778695a4b3c2d1e0f"
HASH_ABSENT_1 = "11112222333344445555666677778888"
HASH_ABSENT_2 = "aaaabbbbccccddddeeeeffff00001111"


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_focal_points_api.db"))
    asyncio.run(database.init())
    asyncio.run(database.set_fever_auth("user", VALID_API_KEY))
    monkeypatch.setattr(reader_module, "db", database)
    monkeypatch.setattr(fever_key_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client():
    return TestClient(app)


async def seed_focal_points(test_db):
    await test_db.set_focal_point(HASH_STORED_1, 62, 38)
    await test_db.set_focal_point(HASH_STORED_2, 50, 71)
    await test_db.set_focal_point(HASH_STORED_3, 25, 80)


# 1. Missing or wrong api_key -> 401
def test_auth_rejected_on_wrong_or_missing_api_key(test_db, client):
    r1 = client.post("/api/reader/focal-points", json={"hashes": [HASH_STORED_1]})
    assert r1.status_code == 401

    r2 = client.post("/api/reader/focal-points", json={"api_key": "wrong_key", "hashes": [HASH_STORED_1]})
    assert r2.status_code == 401

    r3 = client.post("/api/reader/focal-points", json={"api_key": ""})
    assert r3.status_code == 401


# 2. Valid key, hashes with stored focal points -> exact [x, y] pairs returned
def test_stored_focal_points_returned_as_exact_pairs(test_db, client):
    asyncio.run(seed_focal_points(test_db))

    resp = client.post("/api/reader/focal-points", json={
        "api_key": VALID_API_KEY,
        "hashes": [HASH_STORED_1, HASH_STORED_2, HASH_STORED_3],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "focal" in data
    assert data["focal"][HASH_STORED_1] == [62, 38]
    assert data["focal"][HASH_STORED_2] == [50, 71]
    assert data["focal"][HASH_STORED_3] == [25, 80]


# 3. A requested hash with no stored row is absent from the map (not [50, 50])
def test_missing_hash_is_absent_from_map(test_db, client):
    asyncio.run(seed_focal_points(test_db))

    resp = client.post("/api/reader/focal-points", json={
        "api_key": VALID_API_KEY,
        "hashes": [HASH_STORED_1, HASH_ABSENT_1, HASH_ABSENT_2],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "focal" in data
    assert HASH_STORED_1 in data["focal"]
    assert data["focal"][HASH_STORED_1] == [62, 38]
    assert HASH_ABSENT_1 not in data["focal"]
    assert HASH_ABSENT_2 not in data["focal"]


# 4. 1,001 hashes -> 400. 1,000 hashes -> 200.
def test_hash_count_limits(test_db, client):
    hashes_1001 = [f"{i:032x}" for i in range(1001)]
    r_overflow = client.post("/api/reader/focal-points", json={
        "api_key": VALID_API_KEY,
        "hashes": hashes_1001,
    })
    assert r_overflow.status_code == 400
    assert "1000" in r_overflow.json().get("detail", "")

    hashes_1000 = [f"{i:032x}" for i in range(1000)]
    r_exact = client.post("/api/reader/focal-points", json={
        "api_key": VALID_API_KEY,
        "hashes": hashes_1000,
    })
    assert r_exact.status_code == 200
    assert r_exact.json() == {"focal": {}}


# 5. Empty list -> 200 with an empty map
def test_empty_list_returns_empty_map(test_db, client):
    resp = client.post("/api/reader/focal-points", json={
        "api_key": VALID_API_KEY,
        "hashes": [],
    })
    assert resp.status_code == 200
    assert resp.json() == {"focal": {}}


# 6. Duplicate hashes in the request -> one entry in the response, no error
def test_duplicate_hashes_produces_single_entry(test_db, client):
    asyncio.run(seed_focal_points(test_db))

    resp = client.post("/api/reader/focal-points", json={
        "api_key": VALID_API_KEY,
        "hashes": [HASH_STORED_1, HASH_STORED_1, HASH_STORED_1],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"focal": {HASH_STORED_1: [62, 38]}}


# 7. Malformed / None entries are ignored gracefully
def test_malformed_entries_ignored(test_db, client):
    asyncio.run(seed_focal_points(test_db))

    resp = client.post("/api/reader/focal-points", json={
        "api_key": VALID_API_KEY,
        "hashes": [HASH_STORED_1, "", None],
    })
    assert resp.status_code == 200
    assert resp.json() == {"focal": {HASH_STORED_1: [62, 38]}}
