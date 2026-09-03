import os
import pytest
from fastapi.testclient import TestClient
from app.main import app

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
READER_INDEX = os.path.join(PROJECT_ROOT, "reader", "build", "index.html")
MANAGE_INDEX = os.path.join(PROJECT_ROOT, "frontend", "build", "index.html")

client = TestClient(app)


def test_health_still_responds():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_api_path_returns_json_404_not_html():
    r = client.get("/api/definitely-not-a-real-route")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_unknown_feed_path_returns_404_not_html():
    r = client.get("/feed/definitely-not-real")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


@pytest.mark.skipif(not os.path.exists(READER_INDEX), reason="reader not built")
def test_reader_deep_link_serves_reader_index():
    r = client.get("/87-bbc-news/1234-some-article")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


@pytest.mark.skipif(not os.path.exists(READER_INDEX), reason="reader not built")
def test_service_worker_is_not_cacheable():
    """A CDN caching the worker pins a stale copy after every deploy."""
    r = client.get("/service-worker.js")
    assert r.status_code == 200
    cache_control = r.headers.get("cache-control", "")
    assert "no-store" in cache_control
    assert "no-cache" in cache_control


@pytest.mark.skipif(not os.path.exists(READER_INDEX), reason="reader not built")
def test_hashed_assets_stay_cacheable():
    """Only stable filenames are exempted; hashed bundles must remain cacheable."""
    r = client.get("/")
    assert r.status_code == 200
    assert "no-store" not in r.headers.get("cache-control", "")


@pytest.mark.skipif(not os.path.exists(MANAGE_INDEX), reason="dashboard not built")
def test_manage_root_serves_dashboard_index():
    r = client.get("/manage/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


@pytest.mark.skipif(not os.path.exists(MANAGE_INDEX), reason="dashboard not built")
def test_manage_unknown_path_falls_back_to_dashboard_index():
    r = client.get("/manage/no-such-page")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
