"""Tests for URL safety and SSRF validation helpers."""
import os
import pytest
from app.utils.url_safety import is_safe_ip, validate_url_ssrf


def test_is_safe_ip():
    # Loopback
    assert is_safe_ip("127.0.0.1") is False
    assert is_safe_ip("::1") is False
    # Private
    assert is_safe_ip("10.0.0.1") is False
    assert is_safe_ip("172.16.0.1") is False
    assert is_safe_ip("192.168.1.1") is False
    # Link local
    assert is_safe_ip("169.254.169.254") is False
    # Public
    assert is_safe_ip("93.184.216.34") is True
    assert is_safe_ip("8.8.8.8") is True
    # Invalid
    assert is_safe_ip("not-an-ip") is False


def test_validate_url_ssrf_schemes():
    assert validate_url_ssrf("file:///etc/passwd") is False
    assert validate_url_ssrf("ftp://example.com/file") is False
    assert validate_url_ssrf("javascript:alert(1)") is False
    assert validate_url_ssrf("data:text/plain,hello") is False


def test_validate_url_ssrf_private_ips():
    assert validate_url_ssrf("http://127.0.0.1/test") is False
    assert validate_url_ssrf("http://localhost/test") is False
    assert validate_url_ssrf("http://10.0.0.1/test") is False
    assert validate_url_ssrf("http://192.168.1.50/test") is False
    assert validate_url_ssrf("http://169.254.169.254/latest/meta-data") is False


def test_validate_url_ssrf_bypass():
    # Explicit bypass parameter
    assert validate_url_ssrf("http://127.0.0.1/test", allow_private=True) is True
    assert validate_url_ssrf("http://192.168.1.50/test", allow_private=True) is True
    
    # Environment variable bypass
    orig = os.environ.get("ALLOW_PRIVATE_IP_FETCH")
    try:
        os.environ["ALLOW_PRIVATE_IP_FETCH"] = "1"
        assert validate_url_ssrf("http://127.0.0.1/test") is True
        os.environ["ALLOW_PRIVATE_IP_FETCH"] = "0"
        assert validate_url_ssrf("http://127.0.0.1/test") is False
    finally:
        if orig is None:
            os.environ.pop("ALLOW_PRIVATE_IP_FETCH", None)
        else:
            os.environ["ALLOW_PRIVATE_IP_FETCH"] = orig
