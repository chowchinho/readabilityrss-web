import io
from unittest.mock import patch, AsyncMock
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.routes.reader import (
    ALLOWED_IMAGE_PROXY_WIDTHS,
    _validate_url_ssrf,
    _is_safe_ip,
)

client = TestClient(app)


def test_is_safe_ip():
    assert _is_safe_ip("127.0.0.1") is False
    assert _is_safe_ip("192.168.1.1") is False
    assert _is_safe_ip("10.0.0.1") is False
    assert _is_safe_ip("172.16.0.1") is False
    assert _is_safe_ip("169.254.169.254") is False
    assert _is_safe_ip("0.0.0.0") is False
    assert _is_safe_ip("::1") is False
    assert _is_safe_ip("8.8.8.8") is True
    assert _is_safe_ip("93.184.216.34") is True
    assert _is_safe_ip("invalid-ip") is False


def test_validate_url_ssrf():
    assert _validate_url_ssrf("file:///etc/passwd") is False
    assert _validate_url_ssrf("ftp://example.com/img.jpg") is False
    assert _validate_url_ssrf("http://127.0.0.1/test.jpg") is False
    assert _validate_url_ssrf("http://localhost/test.jpg") is False
    assert _validate_url_ssrf("http://169.254.169.254/latest/meta-data") is False
    assert _validate_url_ssrf("http://192.168.1.100/test.jpg") is False
    assert _validate_url_ssrf("http://10.1.2.3/test.jpg") is False
    assert _validate_url_ssrf("http://93.184.216.34/test.jpg") is True


def test_image_proxy_blocks_private_ip_url():
    resp = client.get("/api/reader/image-proxy?url=http://127.0.0.1/secret.jpg")
    assert resp.status_code == 404
    # No exception oracle / leakage in body
    assert "Failed to fetch image" not in resp.text
    assert "127.0.0.1" not in resp.text


def test_image_proxy_blocks_non_http_scheme():
    resp = client.get("/api/reader/image-proxy?url=file:///etc/passwd")
    assert resp.status_code == 404
    assert "Failed to fetch image" not in resp.text


def test_image_proxy_error_response_has_no_exception_leak():
    with patch("app.routes.reader._validate_url_ssrf", return_value=True), \
         patch("httpx.AsyncClient.stream", side_effect=Exception("Connection reset by peer")):
        resp = client.get("/api/reader/image-proxy?url=http://example.com/nonexistent_image_12345.jpg")
        assert resp.status_code == 404
        assert "Failed to fetch image" not in resp.text
        assert "Connection reset by peer" not in resp.text
        assert "Exception" not in resp.text


def test_image_proxy_snaps_width_to_allowed_list():
    for requested_w, expected_w in [(1, 160), (500, 480), (9999, 1200), (800, 800)]:
        snapped = min(ALLOWED_IMAGE_PROXY_WIDTHS, key=lambda allowed: abs(allowed - requested_w))
        assert snapped == expected_w


def test_image_proxy_rejects_oversized_content_length():
    class DummyStreamResponse:
        status_code = 200
        headers = {"content-length": str(20 * 1024 * 1024)}  # 20 MB

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_bytes(self):
            yield b"x" * 1024

    with patch("app.routes.reader._validate_url_ssrf", return_value=True), \
         patch("httpx.AsyncClient.stream", return_value=DummyStreamResponse()):
        resp = client.get("/api/reader/image-proxy?url=http://example.com/huge.jpg")
        assert resp.status_code == 404
        assert "Failed to fetch image" not in resp.text

