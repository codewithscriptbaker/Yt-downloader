from __future__ import annotations

import socket
from urllib.parse import urlparse


def hostname_from_url(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return host or None
    except Exception:
        return None


def host_resolves(hostname: str, port: int = 443, timeout: float = 3.0) -> bool:
    """Return True if DNS lookup for hostname succeeds."""
    if not hostname:
        return False
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


def ensure_host_reachable(url: str) -> None:
    """Raise ConnectionError when the URL host cannot be resolved."""
    host = hostname_from_url(url)
    if not host:
        raise ConnectionError("Invalid URL host")
    if not host_resolves(host):
        raise ConnectionError(
            f"Failed to resolve '{host}' (getaddrinfo failed / could not resolve host)"
        )
