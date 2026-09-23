"""The global default translator: what on-demand uses, and a per-feed fallback."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import app.routes.settings as settings_module
from app.database import Database
from app.main import app


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    database = Database(db_path=str(tmp_path / "test_default_tr.db"))
    asyncio.run(database.init())
    monkeypatch.setattr(settings_module, "db", database)
    yield database
    asyncio.run(database.close())


@pytest.fixture
def client(test_db):
    return TestClient(app)


class SavedSettings:
    def __init__(self, db):
        self._db = db

    def __getitem__(self, key):
        return asyncio.run(self._db.get_system_settings())[key]


@pytest.fixture
def saved_settings(test_db):
    return SavedSettings(test_db)


def _valid_settings_payload():
    return {
        "max_articles_per_feed": 50,
        "feed_refresh_interval_hours": 1.0,
        "image_max_dimension": 1200,
        "image_jpeg_quality": 70,
    }


def test_settings_default_is_qwen(client):
    body = client.get("/api/settings").json()
    assert body["default_translator"] == "qwen"


def test_settings_accepts_a_valid_translator(client, saved_settings):
    payload = dict(_valid_settings_payload(), default_translator="google")
    assert client.put("/api/settings", json=payload).status_code == 200
    assert saved_settings["default_translator"] == "google"


def test_settings_rejects_an_unknown_translator(client):
    payload = dict(_valid_settings_payload(), default_translator="babelfish")
    assert client.put("/api/settings", json=payload).status_code == 400


def test_omitting_the_field_keeps_qwen(client, saved_settings):
    assert client.put("/api/settings", json=_valid_settings_payload()).status_code == 200
    assert saved_settings["default_translator"] == "qwen"
