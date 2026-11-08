"""SSRF guard (connectors/url_guard.py) and every place a connector URL is fetched or saved."""

import asyncio
import socket

import httpx
import pytest

from app.connectors import engine, url_guard
from app.core.models import ConnectorConfig


def resolver(*ips):
    """Fake getaddrinfo: the host resolves to exactly these addresses."""

    def resolve(host, port, proto=0):
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, proto, "", (ip, port)) for ip in ips
        ]

    return resolve


@pytest.mark.parametrize(
    ("ips", "allow_private", "ok"),
    [
        (["93.184.216.34"], False, True),  # public
        (["127.0.0.1"], False, False),
        (["127.0.0.1"], True, True),  # demo: the scripted feed on localhost
        (["::1"], True, True),  # IPv6 loopback is also "reserved" in Python: must still count as loopback
        (["::1"], False, False),
        (["10.1.2.3"], False, False),
        (["::ffff:10.1.2.3"], False, False),  # IPv4-mapped private
        (["169.254.169.254"], True, False),  # cloud metadata: never, even in demo
        (["fe80::1"], True, False),
        (["224.0.0.1"], True, False),  # multicast
        (["93.184.216.34", "10.0.0.1"], False, False),  # any blocked address fails the host
    ],
)
def test_address_rules(ips, allow_private, ok):
    call = lambda: url_guard.check_url("http://feed.example/x", allow_private=allow_private, resolve=resolver(*ips))  # noqa: E731
    if ok:
        assert call() == "http://feed.example/x"
    else:
        with pytest.raises(url_guard.UnsafeUrl):
            call()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "http://", "http://metadata/computeMetadata/v1/"])
def test_bad_schemes_and_hosts(url):
    with pytest.raises(url_guard.UnsafeUrl):
        url_guard.check_url(url, allow_private=True, resolve=resolver("93.184.216.34"))


def test_default_follows_demo_mode(monkeypatch):
    local = resolver("127.0.0.1")
    monkeypatch.setattr(url_guard, "demo_allows_private", lambda: False)
    with pytest.raises(url_guard.UnsafeUrl):
        url_guard.check_url("http://localhost:8765/feed.xml", resolve=local)
    monkeypatch.setattr(url_guard, "demo_allows_private", lambda: True)
    assert url_guard.check_url("http://localhost:8765/feed.xml", resolve=local)


# -- every fetch/save path runs the guard ------------------------------------------------------

INTERNAL = ConnectorConfig(type="rss", url="http://169.254.169.254/latest/meta-data/")


def _never(request):
    raise AssertionError(f"request should have been refused before sending: {request.url}")


def test_poll_fetch_refuses_internal_targets():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_never)) as c:
            await engine.fetch_raw(c, INTERNAL)

    with pytest.raises(url_guard.UnsafeUrl):
        asyncio.run(go())


def test_sync_fetch_refuses_internal_targets():
    with pytest.raises(url_guard.UnsafeUrl), httpx.Client(transport=httpx.MockTransport(_never)) as c:
        engine.fetch_raw_sync(c, INTERNAL)


def test_poll_once_reports_the_refusal_without_raising():
    from app.core.db import Db
    from app.core.models import InputSpec

    inp = InputSpec(id="news", kind="pull", shape="document", tier="stiff", meaning="reports", connector=INTERNAL)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_never)) as c:
            return await engine.poll_once(c, Db(":memory:"), "app", inp, now=0.0)

    res = asyncio.run(go())
    assert res.ok is False and "not allowed" in (res.error or "")


def test_create_connector_refuses_internal_targets():
    from app.core.clock import SimClock
    from app.core.db import Db
    from app.core.models import AppSpec
    from app.engine.tick import InMemoryEventSource, TickEngine
    from app.tools import registry
    from app.tools.context import ToolContext

    db = Db(":memory:")
    spec = AppSpec.model_validate(
        {
            "app_id": "draft-app",
            "intent": "x",
            "grid": {"cell_m": 50, "bbox": {"min_lat": 0, "min_lon": 0, "max_lat": 0.01, "max_lon": 0.01}},
        }
    )
    db.upsert_app(spec, t=0.0)
    ctx = ToolContext(
        app_id="draft-app", db=db, engine=TickEngine(InMemoryEventSource()), agent="compiler", clock=SimClock()
    )
    made = registry.call_tool(
        ctx, "create_input", {"id": "news", "kind": "pull", "shape": "document", "tier": "stiff", "meaning": "reports"}
    )
    assert made["ok"], made
    out = registry.call_tool(
        ctx, "create_connector", {"input_id": "news", "type": "rss", "url": "http://169.254.169.254/x"}
    )
    assert out["ok"] is False and "connector URL refused" in out["error"]
    assert db.get_app("draft-app").inputs[0].connector is None  # nothing saved
