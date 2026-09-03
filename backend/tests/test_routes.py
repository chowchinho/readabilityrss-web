"""Integration tests for API routes."""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """Fixture providing a TestClient for the FastAPI app."""
    return TestClient(app)


def test_health_check(client):
    """Test that health check endpoint works."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_parse_endpoint_missing_url(client):
    """Test that /parse requires a URL."""
    response = client.post("/api/parse", json={})
    assert response.status_code == 422  # Validation error


def test_parse_endpoint_rejects_url_without_protocol(client):
    """Test that /parse rejects URLs without protocol."""
    response = client.post("/api/parse", json={"url": "example.com"})
    # Should reject URL without http:// or https://
    assert response.status_code != 200
    detail = response.json()["detail"]
    assert "Invalid URL" in detail or "must start with http://" in detail


def test_parse_endpoint_validates_url_format(client):
    """Test that /parse validates URL format."""
    response = client.post("/api/parse", json={"url": "not-a-valid-url"})
    # Invalid URL should be rejected
    assert response.status_code != 200
    detail = response.json()["detail"]
    assert "Invalid URL" in detail or "must start with http://" in detail
