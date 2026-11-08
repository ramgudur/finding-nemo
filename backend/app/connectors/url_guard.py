"""Server-side request forgery guard for every URL the platform fetches on someone's behalf.

Connector URLs can come from an agent (the Scout, the Compiler), so the server must never be pointed at
itself or its network. Used by `engine.fetch_raw` / `fetch_raw_sync` (every poll and `test_connector`),
`tools.compiler_tools.create_connector` and the Scout's `fetch_url` / `probe_feed`.

- http(s) with a host only.
- Always blocked: link-local (incl. 169.254.169.254 cloud metadata), multicast, unspecified, reserved
  addresses and `metadata` hostnames.
- Private and loopback: blocked unless allowed. The default allows them only in DEMO_MODE, where the
  scripted demo feed is served on localhost.
- Redirects: callers follow at most `MAX_REDIRECTS` hops via `next_hop`, which re-runs this check on
  every hop, so a redirect can't bounce to a blocked address.
Known limit: we resolve, then the HTTP client resolves again, so DNS rebinding between the two isn't
covered. The link-local block and no-redirects cover the main cloud-metadata case.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

_METADATA_HOSTS = {"metadata.google.internal", "metadata"}

Resolver = Callable[..., list[Any]]


MAX_REDIRECTS = 3
REDIRECT_CODES = (301, 302, 303, 307, 308)


class UnsafeUrl(ValueError):
    """The URL may not be fetched."""


def demo_allows_private() -> bool:
    from app.core.config import get_settings

    return bool(get_settings().demo_mode)


def check_url(url: str, *, allow_private: bool | None = None, resolve: Resolver | None = None) -> str:
    """Return `url` if it may be fetched; raise UnsafeUrl otherwise."""
    if allow_private is None:
        allow_private = demo_allows_private()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeUrl("only http(s) URLs with a host can be fetched")
    host = u.hostname.lower()
    if host in _METADATA_HOSTS:
        raise UnsafeUrl("that host is not allowed")
    try:
        infos = (resolve or socket.getaddrinfo)(
            host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as e:
        raise UnsafeUrl(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        # order matters: link-local (169.254/16, fe80::/10) also counts as private, and ::1 as reserved
        if ip.is_link_local:
            raise UnsafeUrl("that address is not allowed")
        if ip.is_loopback or ip.is_private:
            if not allow_private:
                raise UnsafeUrl("private and local addresses can't be fetched (outside demo mode)")
            continue
        if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise UnsafeUrl("that address is not allowed")
    return url


def next_hop(current_url: str, location: str | None, *, allow_private: bool | None = None, hops: int = 0) -> str:
    """The checked URL a redirect points to. Raises UnsafeUrl past MAX_REDIRECTS, on a missing
    Location, or if the target fails `check_url` (same rules as the first request)."""
    from urllib.parse import urljoin

    if hops >= MAX_REDIRECTS:
        raise UnsafeUrl(f"more than {MAX_REDIRECTS} redirects")
    if not location:
        raise UnsafeUrl("redirect without a Location header")
    return check_url(urljoin(current_url, location), allow_private=allow_private)
