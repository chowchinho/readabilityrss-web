"""The database directory must be overridable so a container can mount a volume.

These tests deliberately do NOT reload app.database. Reloading it builds a fresh
Database class whose _instances and _live_connections registries are empty, which
orphans every aiosqlite worker thread opened before the reload and hangs the process
at interpreter exit. resolve_db_dir() exists so the override can be tested directly.
"""
import os
from unittest.mock import patch

from app import database


def test_defaults_to_backend_data_when_unset():
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"}
    with patch.dict(os.environ, env, clear=True):
        resolved = database.resolve_db_dir()
    assert resolved.replace("\\", "/").endswith("backend/data")


def test_env_var_overrides_location(tmp_path):
    with patch.dict(os.environ, {"DATA_DIR": str(tmp_path)}):
        assert database.resolve_db_dir() == str(tmp_path)


def test_blank_env_var_falls_back_to_default():
    """An empty DATA_DIR must not resolve the database to the current directory."""
    with patch.dict(os.environ, {"DATA_DIR": ""}):
        resolved = database.resolve_db_dir()
    assert resolved.replace("\\", "/").endswith("backend/data")


def test_module_level_paths_agree_with_the_resolver():
    assert database.DB_DIR == database.resolve_db_dir()
    assert database.DB_PATH == os.path.join(database.DB_DIR, "feeds.db")
