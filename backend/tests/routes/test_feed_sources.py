import pytest
import pytest_asyncio
from app.database import Database
import app.database as database_mod
import app.routes.categories as categories_mod
import app.routes.feed_sources as feed_sources_mod

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

@pytest_asyncio.fixture
async def test_db(tmp_path, monkeypatch):
    db_inst = Database(db_path=str(tmp_path / "test_feed_sources.db"))
    await db_inst.init()
    monkeypatch.setattr(database_mod, "db", db_inst)
    monkeypatch.setattr(categories_mod, "db", db_inst)
    monkeypatch.setattr(feed_sources_mod, "db", db_inst)
    yield db_inst
    await db_inst.close()

@pytest.mark.asyncio
async def test_crud_category_and_feed_source(test_db):
    # Create category
    response = client.post("/api/categories", json={"name": "Test News"})
    assert response.status_code == 200
    cat_id = response.json()["id"]

    # Create feed source (will try to check URL, which will fail — that's OK)
    response = client.post("/api/feed-sources", json={
        "category_id": cat_id,
        "name": "My News Source",
        "url": "https://example.com/news",
        "item_selector": ".article",
        "link_selector": "a"
    })
    assert response.status_code == 200
    source_id = response.json()["id"]

    # List feed sources
    response = client.get("/api/feed-sources")
    assert response.status_code == 200
    sources = response.json()
    assert len(sources) == 1
    assert sources[0]["name"] == "My News Source"

    # Delete feed source
    response = client.delete(f"/api/feed-sources/{source_id}")
    assert response.status_code == 200

    # Delete category
    response = client.delete(f"/api/categories/{cat_id}")
    assert response.status_code == 200

    # Verify empty
    response = client.get("/api/categories")
    assert response.status_code == 200
    assert len(response.json()) == 0


@pytest.mark.asyncio
async def test_toggle_feed_source_enabled_nonexistent_returns_404(test_db):
    response = client.post("/api/feed-sources/99999/toggle-enabled")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_feed_source_invalid_column_rejected(test_db):
    with pytest.raises(ValueError, match="Invalid column names"):
        await test_db.create_feed_source({"name": "Test", "non_existent_column": "bad"})


