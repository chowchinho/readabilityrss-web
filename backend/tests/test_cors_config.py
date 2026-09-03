"""CORS origins come from config, not from hardcoded personal hostnames."""
import os
from unittest.mock import patch

from app.main import DEFAULT_CORS_ORIGINS, _cors_origins


def test_defaults_cover_local_dev_and_capacitor():
    env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
    with patch.dict(os.environ, env, clear=True):
        origins = _cors_origins()
    assert "http://localhost:3010" in origins
    assert "http://localhost:5173" in origins
    assert "capacitor://localhost" in origins


def test_defaults_are_local_only():
    """Guards the release: the shipped default list must name no external host.

    Anything beyond loopback and the Capacitor bundle belongs in CORS_ORIGINS,
    set per deployment, not baked into source.
    """
    allowed_hosts = {"localhost", "127.0.0.1"}
    for origin in DEFAULT_CORS_ORIGINS:
        host = origin.split("://", 1)[1].split(":", 1)[0]
        assert host in allowed_hosts, f"non-local origin in defaults: {origin}"


def test_env_var_replaces_defaults():
    with patch.dict(os.environ, {"CORS_ORIGINS": "https://a.example,https://b.example"}):
        origins = _cors_origins()
    assert origins == ["https://a.example", "https://b.example"]


def test_env_var_tolerates_whitespace_and_blanks():
    with patch.dict(os.environ, {"CORS_ORIGINS": " https://a.example , , https://b.example "}):
        origins = _cors_origins()
    assert origins == ["https://a.example", "https://b.example"]


def test_empty_env_var_falls_back_to_defaults():
    with patch.dict(os.environ, {"CORS_ORIGINS": "   "}):
        origins = _cors_origins()
    assert origins == DEFAULT_CORS_ORIGINS
