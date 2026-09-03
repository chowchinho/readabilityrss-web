import hashlib
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app

client = TestClient(app)

# Test credentials
TEST_USER = "testuser"
TEST_PASS = "testpass"
TEST_API_KEY = hashlib.md5(f"{TEST_USER}:{TEST_PASS}".encode()).hexdigest()

MOCK_CATEGORIES = [
    {"id": 1, "name": "Tech", "source_count": 2},
    {"id": 2, "name": "News", "source_count": 1},
]

MOCK_SOURCES = [
    {"id": 1, "name": "TechBlog", "url": "https://techblog.com", "category_id": 1,
     "last_fetch_at": "2026-03-21 10:00:00", "updated_at": "2026-03-21 10:00:00"},
    {"id": 2, "name": "DevNews", "url": "https://devnews.com", "category_id": 1,
     "last_fetch_at": "2026-03-21 09:00:00", "updated_at": "2026-03-21 09:00:00"},
    {"id": 3, "name": "WorldNews", "url": "https://world.com", "category_id": 2,
     "last_fetch_at": None, "updated_at": "2026-03-20 12:00:00"},
]

MOCK_ARTICLES = [
    {"id": 10, "source_id": 1, "url": "https://techblog.com/a1", "title": "Article 1",
     "content": "<p>Hello</p>", "pub_date": "2026-03-21T10:00:00", "main_image": None,
     "is_read": 0, "is_saved": 0, "created_at": "2026-03-21 10:00:00"},
    {"id": 11, "source_id": 1, "url": "https://techblog.com/a2", "title": "Article 2",
     "content": "<p>World</p>", "pub_date": "2026-03-21T11:00:00", "main_image": None,
     "is_read": 1, "is_saved": 1, "created_at": "2026-03-21 11:00:00"},
]


def _fever_post(params="?api", api_key=TEST_API_KEY, extra_data=None):
    """Helper to POST to the Fever endpoint."""
    data = {"api_key": api_key}
    if extra_data:
        data.update(extra_data)
    return client.post(f"/fever/{params}", data=data)


# --- Auth management endpoint tests ---

@patch('app.routes.fever.db')
def test_get_fever_auth_disabled(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value=None)
    resp = client.get("/api/fever-auth")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "username": ""}


@patch('app.routes.fever.db')
def test_get_fever_auth_enabled(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": "bob", "api_key": "abc123"})
    resp = client.get("/api/fever-auth")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "username": "bob"}


@patch('app.routes.fever.db')
def test_set_fever_auth(mock_db):
    mock_db.set_fever_auth = AsyncMock()
    resp = client.post("/api/fever-auth", json={"username": TEST_USER, "password": TEST_PASS})
    assert resp.status_code == 200
    mock_db.set_fever_auth.assert_called_once_with(TEST_USER, TEST_API_KEY)


@patch('app.routes.fever.db')
def test_delete_fever_auth(mock_db):
    mock_db.delete_fever_auth = AsyncMock()
    resp = client.delete("/api/fever-auth")
    assert resp.status_code == 200
    assert resp.json() == {"success": True}


# --- Fever protocol tests ---

@patch('app.routes.fever.db')
def test_auth_failure(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    resp = _fever_post(api_key="wrong_key")
    data = resp.json()
    assert data["auth"] == 0
    assert data["api_version"] == 3


@patch('app.routes.fever.db')
def test_auth_failure_no_credentials(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value=None)
    resp = _fever_post()
    assert resp.json()["auth"] == 0


@patch('app.routes.fever.db')
def test_auth_success(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=1711000000)
    resp = _fever_post()
    data = resp.json()
    assert data["auth"] == 1
    assert data["api_version"] == 3
    assert data["last_refreshed_on_time"] == 1711000000


@patch('app.routes.fever.db')
def test_groups(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_categories = AsyncMock(return_value=MOCK_CATEGORIES)
    mock_db.get_feed_sources = AsyncMock(return_value=MOCK_SOURCES)

    resp = _fever_post("?api&groups")
    data = resp.json()
    assert data["auth"] == 1
    assert len(data["groups"]) == 2
    assert data["groups"][0] == {"id": 1, "title": "Tech"}
    assert len(data["feeds_groups"]) >= 2


@patch('app.routes.fever.db')
def test_groups_with_uncategorized(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_categories = AsyncMock(return_value=[{"id": 1, "name": "Tech", "source_count": 1}])
    sources_with_uncategorized = MOCK_SOURCES + [
        {"id": 4, "name": "Random", "url": "https://random.com", "category_id": None,
         "last_fetch_at": None, "updated_at": "2026-03-20 12:00:00"}
    ]
    mock_db.get_feed_sources = AsyncMock(return_value=sources_with_uncategorized)

    resp = _fever_post("?api&groups")
    data = resp.json()
    group_ids = [g["id"] for g in data["groups"]]
    assert 0 in group_ids  # Uncategorized group exists


@patch('app.routes.fever.db')
def test_feeds(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_feed_sources = AsyncMock(return_value=MOCK_SOURCES)

    resp = _fever_post("?api&feeds")
    data = resp.json()
    assert len(data["feeds"]) == 3
    feed = data["feeds"][0]
    assert feed["id"] == 1
    assert feed["title"] == "TechBlog"
    assert feed["site_url"] == "https://techblog.com"
    assert feed["is_spark"] == 0
    assert "feeds_groups" in data


@patch('app.routes.fever.db')
def test_items(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_fever_items = AsyncMock(return_value=MOCK_ARTICLES)
    mock_db.get_total_item_count = AsyncMock(return_value=2)

    resp = _fever_post("?api&items")
    data = resp.json()
    assert data["total_items"] == 2
    assert len(data["items"]) == 2
    item = data["items"][0]
    assert item["id"] == 10
    assert item["feed_id"] == 1
    assert item["title"] == "Article 1"
    assert item["html"] == "<p>Hello</p>"
    assert item["author"] == ""


@patch('app.routes.fever.db')
def test_items_since_id(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_fever_items = AsyncMock(return_value=[MOCK_ARTICLES[1]])
    mock_db.get_total_item_count = AsyncMock(return_value=2)

    resp = _fever_post("?api&items&since_id=10")
    data = resp.json()
    mock_db.get_fever_items.assert_called_once_with(since_id=10)
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == 11


@patch('app.routes.fever.db')
def test_items_with_ids(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_fever_items = AsyncMock(return_value=MOCK_ARTICLES)
    mock_db.get_total_item_count = AsyncMock(return_value=2)

    resp = _fever_post("?api&items&with_ids=10,11")
    data = resp.json()
    mock_db.get_fever_items.assert_called_once_with(with_ids=[10, 11])


@patch('app.routes.fever.db')
def test_unread_item_ids(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_unread_item_ids = AsyncMock(return_value=[10, 12, 15])

    resp = _fever_post("?api&unread_item_ids")
    data = resp.json()
    assert data["unread_item_ids"] == "10,12,15"


@patch('app.routes.fever.db')
def test_saved_item_ids(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.get_saved_item_ids = AsyncMock(return_value=[11])

    resp = _fever_post("?api&saved_item_ids")
    data = resp.json()
    assert data["saved_item_ids"] == "11"


@patch('app.routes.fever.db')
def test_favicons(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)

    resp = _fever_post("?api&favicons")
    data = resp.json()
    assert len(data["favicons"]) == 1
    assert data["favicons"][0]["id"] == 0
    assert "base64" in data["favicons"][0]["data"]


@patch('app.routes.fever.db')
def test_links_empty(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)

    resp = _fever_post("?api&links")
    data = resp.json()
    assert data["links"] == []


@patch('app.routes.fever.db')
def test_mark_item_read(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.mark_item_read = AsyncMock()

    resp = _fever_post("?api", extra_data={"mark": "item", "as": "read", "id": "10"})
    assert resp.json()["auth"] == 1
    mock_db.mark_item_read.assert_called_once_with(10)


@patch('app.routes.fever.db')
def test_mark_item_saved(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.mark_item_saved = AsyncMock()

    resp = _fever_post("?api", extra_data={"mark": "item", "as": "saved", "id": "11"})
    mock_db.mark_item_saved.assert_called_once_with(11)


@patch('app.routes.fever.db')
def test_mark_item_unsaved(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.mark_item_unsaved = AsyncMock()

    resp = _fever_post("?api", extra_data={"mark": "item", "as": "unsaved", "id": "11"})
    mock_db.mark_item_unsaved.assert_called_once_with(11)


@patch('app.routes.fever.db')
def test_mark_feed_read(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.mark_feed_read = AsyncMock()

    resp = _fever_post("?api", extra_data={"mark": "feed", "as": "read", "id": "1", "before": "1711000000"})
    mock_db.mark_feed_read.assert_called_once_with(1, 1711000000)


@patch('app.routes.fever.db')
def test_mark_group_read(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.mark_group_read = AsyncMock()

    resp = _fever_post("?api", extra_data={"mark": "group", "as": "read", "id": "1", "before": "1711000000"})
    mock_db.mark_group_read.assert_called_once_with(1, 1711000000)


@patch('app.routes.fever.db')
def test_fever_malformed_int_parameters_does_not_500(mock_db):
    mock_db.get_fever_auth = AsyncMock(return_value={"username": TEST_USER, "api_key": TEST_API_KEY})
    mock_db.get_last_refreshed_on_time = AsyncMock(return_value=0)
    mock_db.mark_item_read = AsyncMock()
    mock_db.get_fever_items = AsyncMock(return_value=[])
    mock_db.get_total_item_count = AsyncMock(return_value=0)

    # Non-integer mark id / before
    resp = _fever_post("?api", extra_data={"mark": "item", "as": "read", "id": "not-an-int", "before": "bad"})
    assert resp.status_code == 200
    assert resp.json()["auth"] == 1
    mock_db.mark_item_read.assert_called_once_with(0)

    # Non-integer items query params
    resp2 = _fever_post("?api&items&since_id=abc&with_ids=1,bad,2", extra_data={})
    assert resp2.status_code == 200
    assert resp2.json()["auth"] == 1

