from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_reader_articles_limit_lower_bound():
    # limit=-1 must be rejected with 422 Unprocessable Entity
    resp = client.get("/api/reader/articles?limit=-1")
    assert resp.status_code == 422


def test_reader_articles_limit_zero_rejected():
    # limit=0 must be rejected with 422 Unprocessable Entity
    resp = client.get("/api/reader/articles?limit=0")
    assert resp.status_code == 422


def test_reader_articles_offset_negative_rejected():
    # offset=-1 must be rejected with 422 Unprocessable Entity
    resp = client.get("/api/reader/articles?offset=-1")
    assert resp.status_code == 422


def test_reader_articles_limit_upper_bound():
    # limit=1001 must be rejected with 422 Unprocessable Entity
    resp = client.get("/api/reader/articles?limit=1001")
    assert resp.status_code == 422
