"""Scout tools for vetting sources: fetch_url, probe_feed, geocode, set_source_reliability.

- URLs come from the model, so `fetch_url` and `probe_feed` guard against server-side request forgery:
  http(s) only; link-local and cloud-metadata addresses always blocked; private and loopback
  addresses blocked unless DEMO_MODE (the demo serves its scripted feed on localhost). Size and
  time are capped and only text is returned, so a tool call can't pull a large or binary body.
- `probe_feed` parses with the connector parsers (rss / json) and returns a compact summary.
- `set_source_reliability` sets an rss source's trust, which `event_points` uses as reliability. It
  only lowers (with a reason), through the same trust guard as `set_user_trust`; raising needs the
  Critic's backtest path.
Tests inject an `httpx.Client` on a MockTransport through `ctx.extra["http_client"]`.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.connectors import rss
from app.tools import geo_tools, guards
from app.tools.context import ToolContext
from app.tools.guards import GuardError

MAX_BYTES = 512_000
FETCH_TIMEOUT_S = 10.0
TEXT_CHARS = 4000
TEXT_TYPES = ("text/", "application/xml", "application/rss", "application/atom", "application/json", "+xml", "+json")
_METADATA_HOSTS = {"metadata.google.internal", "metadata"}


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UrlArgs(_Args):
    url: str = Field(min_length=8, max_length=2000)


class GeocodeArgs(_Args):
    place: str = Field(min_length=2, max_length=200, description="A place name from a source, e.g. a street or venue.")


class SourceReliabilityArgs(_Args):
    input_id: str = Field(description="A pull input whose connector produced the source.")
    reliability: float = Field(ge=0, le=1, description="Must be lower than the current value.")
    reason: str


def _demo_mode() -> bool:
    from app.core.config import get_settings

    return bool(get_settings().demo_mode)


def check_url(url: str, *, allow_private: bool) -> str:
    """Raise GuardError unless `url` is http(s) and resolves only to addresses we may contact."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise GuardError("only http(s) URLs with a host can be fetched")
    host = u.hostname.lower()
    if host in _METADATA_HOSTS:
        raise GuardError("that host is not allowed")
    try:
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise GuardError(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise GuardError("that address is not allowed")
        if (ip.is_private or ip.is_loopback) and not allow_private:
            raise GuardError("private and local addresses can't be fetched (outside demo mode)")
    return url


def _get(ctx: ToolContext, url: str) -> httpx.Response:
    check_url(url, allow_private=ctx.extra.get("allow_private_urls", _demo_mode()))
    client = ctx.extra.get("http_client")
    own = client is None
    client = client or httpx.Client(timeout=FETCH_TIMEOUT_S, follow_redirects=False)
    try:
        with client.stream("GET", url, timeout=FETCH_TIMEOUT_S, headers={"Accept": "*/*"}) as resp:
            if 300 <= resp.status_code < 400:
                raise GuardError("redirects are not followed; fetch the final URL directly")
            ctype = resp.headers.get("content-type", "").lower()
            if ctype and not any(t in ctype for t in TEXT_TYPES):
                raise GuardError(f"not a text response ({ctype.split(';')[0]})")
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise GuardError(f"response is larger than {MAX_BYTES // 1000} KB")
            return httpx.Response(resp.status_code, headers=resp.headers, content=body, request=resp.request)
    finally:
        if own:
            client.close()


def _text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return " ".join(re.sub(r"(?s)<[^>]+>", " ", html).split())


def fetch_url(ctx: ToolContext, args: UrlArgs) -> dict[str, Any]:
    resp = _get(ctx, args.url)
    raw = resp.text
    text = _text(raw) if "html" in resp.headers.get("content-type", "") else " ".join(raw.split())
    return {"url": args.url, "status": resp.status_code, "chars": len(text), "text": text[:TEXT_CHARS]}


def probe_feed(ctx: ToolContext, args: UrlArgs) -> dict[str, Any]:
    """Is this a usable feed? RSS/Atom: item counts and sample titles. JSON: top-level shape."""
    resp = _get(ctx, args.url)
    if resp.status_code >= 400:
        return {"url": args.url, "kind": "error", "status": resp.status_code}
    raw = resp.text
    try:
        items = rss.parse_items(raw, ctx.now(), include_unlocated=True)
    except Exception:  # noqa: BLE001 - not a feed: try JSON next
        items = None
    if items:
        located = sum(1 for i in items if i.get("lat") is not None)
        return {
            "url": args.url,
            "kind": "rss",
            "items": len(items),
            "located_items": located,
            "sample_titles": [str(i.get("title", ""))[:100] for i in items[:3]],
        }
    try:
        data = json.loads(raw)
    except ValueError:
        return {"url": args.url, "kind": "unknown", "chars": len(raw)}
    shape = (
        sorted(data)[:15]
        if isinstance(data, dict)
        else f"list of {len(data)}"
        if isinstance(data, list)
        else type(data).__name__
    )
    return {"url": args.url, "kind": "json", "top_level": shape}


def geocode(ctx: ToolContext, args: GeocodeArgs) -> dict[str, Any]:
    """A place name -> a point inside this app's area, or nothing. Never a guess outside the area."""
    client = ctx.extra.get("nominatim") or geo_tools.NOMINATIM
    hit = client.geocode(args.place, ctx.spec.grid.bbox.as_geojson())
    return {"place": args.place, "found": hit is not None, **(hit or {})}


def set_source_reliability(ctx: ToolContext, args: SourceReliabilityArgs) -> dict[str, Any]:
    reason = guards.require_reason(args.reason)
    inp = next((i for i in ctx.spec.inputs if i.id == args.input_id), None)
    if inp is None or inp.kind != "pull" or inp.connector is None:
        raise GuardError(f"{args.input_id!r} is not a pull input with a connector")
    if inp.connector.type != "rss":
        raise GuardError("reliability applies to document sources (rss); json_api events carry no source id")
    source_id = str(inp.connector.config.get("source_id", "rss"))  # same default as connectors/engine.py
    before, _ = guards.current_trust(ctx, source_id)
    if args.reliability > before:
        raise GuardError(
            f"set_source_reliability only lowers ({before:.2f} -> {args.reliability:.2f} is an increase); "
            "raising a source's reliability goes through the Critic's backtest"
        )
    c = guards.apply_trust(ctx, source_id, args.reliability, reason=reason, backtest_id=None)
    return {
        "input_id": args.input_id,
        "source_id": source_id,
        "before": before,
        "after": args.reliability,
        "change_id": c.id,
    }
