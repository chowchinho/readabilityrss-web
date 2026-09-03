"""URL safety and SSRF protection helpers."""
import ipaddress
import os
import socket
from urllib.parse import urlparse


def is_safe_ip(ip_str: str) -> bool:
    """Check if an IP string is public and safe (not private, loopback, link-local, reserved, multicast, or unspecified)."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def validate_url_ssrf(url_str: str, allow_private: bool | None = None) -> bool:
    """Validate that a URL uses http(s) and does not resolve to a private/internal IP address.

    If allow_private is None, checks the ALLOW_PRIVATE_IP_FETCH environment variable
    (defaults to False / blocked).
    """
    if allow_private is None:
        allow_private = os.environ.get("ALLOW_PRIVATE_IP_FETCH", "0").lower() in ("1", "true", "yes")

    try:
        parsed = urlparse(url_str)
        if parsed.scheme.lower() not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if allow_private:
            return True
        try:
            ip = ipaddress.ip_address(hostname)
            return is_safe_ip(str(ip))
        except ValueError:
            pass
        addr_info = socket.getaddrinfo(hostname, None)
        if not addr_info:
            return False
        for entry in addr_info:
            sockaddr = entry[4]
            ip_str = sockaddr[0]
            if not is_safe_ip(ip_str):
                return False
        return True
    except Exception:
        return False


# Aliases for backwards compatibility with route/test imports
_is_safe_ip = is_safe_ip
_validate_url_ssrf = validate_url_ssrf
