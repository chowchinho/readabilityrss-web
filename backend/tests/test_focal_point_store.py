import pytest
import pytest_asyncio

from app.database import Database


@pytest_asyncio.fixture
async def focal_db(tmp_path):
    database = Database(db_path=str(tmp_path / "feeds.db"))
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_set_and_get_round_trip(focal_db):
    await focal_db.set_focal_point("hash_round_trip", 30, 70)
    result = await focal_db.get_focal_points(["hash_round_trip"])
    assert result["hash_round_trip"] == (30, 70)


@pytest.mark.asyncio
async def test_set_is_idempotent_and_overwrites(focal_db):
    await focal_db.set_focal_point("hash_overwrite", 10, 10)
    await focal_db.set_focal_point("hash_overwrite", 80, 20)
    result = await focal_db.get_focal_points(["hash_overwrite"])
    assert result["hash_overwrite"] == (80, 20)


@pytest.mark.asyncio
async def test_missing_hashes_are_absent_not_defaulted(focal_db):
    """Callers apply the 50/50 default; the store must not invent rows."""
    result = await focal_db.get_focal_points(["definitely_not_stored"])
    assert "definitely_not_stored" not in result


@pytest.mark.asyncio
async def test_empty_input_returns_empty_dict(focal_db):
    assert await focal_db.get_focal_points([]) == {}


@pytest.mark.asyncio
async def test_delete_removes_rows(focal_db):
    await focal_db.set_focal_point("hash_to_delete", 25, 25)
    removed = await focal_db.delete_focal_points(["hash_to_delete"])
    assert removed == 1
    assert await focal_db.get_focal_points(["hash_to_delete"]) == {}


@pytest.mark.asyncio
async def test_bulk_get_exceeds_sqlite_variable_limit(focal_db):
    """SQLite caps host parameters near 999 — the query must chunk."""
    hashes = [f"bulk_{i}" for i in range(1200)]
    for h in hashes[:3]:
        await focal_db.set_focal_point(h, 11, 22)
    result = await focal_db.get_focal_points(hashes)
    assert result["bulk_0"] == (11, 22)
    assert len(result) == 3
