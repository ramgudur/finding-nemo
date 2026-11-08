"""Scout source tools: web_search, fetch_url, probe_feed, geocode, set_source_reliability. No network."""

import asyncio
import json

import httpx
import pytest
from sim import campus_alert as ca
from sim.common import load_spec

from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec
from app.engine.tick import InMemoryEventSource, TickEngine
from app.tools import geo_tools, registry
from app.tools.connector_tools import check_url
from app.tools.context import ToolContext
from app.tools.guards import GuardError

T0 = 1_800_000_000.0
PUBLIC = "http://93.184.216.34"  # a literal public IP: no DNS lookup in tests


@pytest.fixture
def ctx():
    prev = clock_mod.set_clock(SimClock(start=T0))
    db = Db(":memory:")
    eng = TickEngine(InMemoryEventSource())
    spec = AppSpec.model_validate({**load_spec("campus-alert"), "status": "active"})
    db.upsert_app(spec, t=T0)
    eng.add_app(spec)
    yield ToolContext(app_id=spec.app_id, db=db, engine=eng, agent="scout")
    clock_mod.set_clock(prev)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def call(ctx, name, **args):
    return registry.call_tool(ctx, name, args)


# -- web_search ---------------------------------------------------------------------------


def test_web_search_without_a_key_says_so(ctx, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.get_settings(), "tavily_api_key", "")
    out = call(ctx, "web_search", query="local feeds")
    assert out["ok"] is False and "not configured" in out["error"]


def test_web_search_returns_compact_results_and_never_the_key(ctx, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.get_settings(), "tavily_api_key", "tvly-secret")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Feed", "url": "https://x.example/feed", "content": "word " * 200},
                    {"title": "no url"},
                ]
            },
        )

    ctx.extra["http_client"] = _client(handler)
    out = call(ctx, "web_search", query="local feeds", max_results=3)
    assert out["ok"] and seen["body"]["max_results"] == 3
    res = out["result"]["results"]
    assert [r["url"] for r in res] == ["https://x.example/feed"]  # results without a URL are dropped
    assert len(res[0]["snippet"]) <= 300
    assert "tvly-secret" not in json.dumps(out)


# -- fetch_url / SSRF guard --------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://93.184.216.34/x", "http://169.254.169.254/latest/meta-data", "http://metadata/x"],
)
def test_unsafe_urls_are_always_rejected(url):
    with pytest.raises(GuardError):
        check_url(url, allow_private=True)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765/feed.xml", "http://10.0.0.5/x", "http://192.168.1.2/x"])
def test_private_addresses_only_in_demo_mode(url):
    with pytest.raises(GuardError, match="private"):
        check_url(url, allow_private=False)
    assert check_url(url, allow_private=True) == url


def test_fetch_url_returns_capped_text(ctx):
    html = "<html><script>x()</script><body><h1>Title</h1>" + "<p>word</p>" * 2000 + "</body></html>"
    ctx.extra["http_client"] = _client(lambda r: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    out = call(ctx, "fetch_url", url=f"{PUBLIC}/page")["result"]
    assert out["text"].startswith("Title word") and "x()" not in out["text"]
    assert len(out["text"]) <= 4000


@pytest.mark.parametrize(
    ("resp", "msg"),
    [
        (httpx.Response(302, headers={"location": "http://127.0.0.1/"}), "redirects"),
        (httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}), "not a text"),
        (httpx.Response(200, content=b"a" * 600_000, headers={"content-type": "text/plain"}), "larger than"),
    ],
)
def test_fetch_url_refuses_redirects_binary_and_huge_bodies(ctx, resp, msg):
    ctx.extra["http_client"] = _client(lambda r: resp)
    out = call(ctx, "fetch_url", url=f"{PUBLIC}/x")
    assert out["ok"] is False and msg in out["error"]


# -- probe_feed -------------------------------------------------------------------------------


def test_probe_feed_summarizes_rss(ctx):
    sc = ca.build(7)
    xml = ca.rss_xml(sc, T0, 200.0)  # after the scripted article is out
    ctx.extra["http_client"] = _client(
        lambda r: httpx.Response(200, text=xml, headers={"content-type": "application/rss+xml"})
    )
    out = call(ctx, "probe_feed", url=f"{PUBLIC}/campus-news.xml")["result"]
    assert out["kind"] == "rss" and out["items"] == 1 and out["located_items"] == 1
    assert out["sample_titles"] and "[SCRIPTED DEMO]" in out["sample_titles"][0]


def test_probe_feed_describes_json(ctx):
    ctx.extra["http_client"] = _client(lambda r: httpx.Response(200, json={"current": {}, "latitude": 1}))
    out = call(ctx, "probe_feed", url=f"{PUBLIC}/api")["result"]
    assert out == {"url": f"{PUBLIC}/api", "kind": "json", "top_level": ["current", "latitude"]}


# -- geocode ---------------------------------------------------------------------------------


def test_geocode_is_bounded_cached_and_rate_limited(ctx):
    asked = []

    def search(params):
        asked.append(params)
        return [{"lat": "32.7795", "lon": "-96.7600", "display_name": "A Hall, Somewhere"}]

    client = geo_tools.NominatimClient(search=search, min_interval_s=0.0)
    ctx.extra["nominatim"] = client
    out = call(ctx, "geocode", place="A Hall")["result"]
    assert out["found"] and out["lat"] == pytest.approx(32.7795)
    assert asked[0]["bounded"] == "1" and asked[0]["viewbox"].startswith("-96.762137,")
    call(ctx, "geocode", place="  a   hall ")  # same place, normalized: cache hit
    assert client.requests == 1


def test_geocode_place_for_the_connector_path():
    spec = AppSpec.model_validate(load_spec("campus-alert"))
    client = geo_tools.NominatimClient(search=lambda p: [], min_interval_s=0.0)
    assert asyncio.run(geo_tools.geocode_place("nowhere", spec, client)) is None
    hit = geo_tools.NominatimClient(search=lambda p: [{"lat": "32.78", "lon": "-96.76"}], min_interval_s=0.0)
    assert asyncio.run(geo_tools.geocode_place("somewhere", spec, hit)) == (32.78, -96.76)


# -- set_source_reliability -----------------------------------------------------------------------


def test_source_reliability_only_lowers_and_records_a_change(ctx):
    out = call(
        ctx, "set_source_reliability", input_id="local_news", reliability=0.5, reason="scripted feed, unverified"
    )
    assert out["ok"] and out["result"]["source_id"] == "rss" and out["result"]["after"] == 0.5
    changes = ctx.db.list_changes(ctx.app_id, kind="trust")
    assert changes and changes[-1].target == "rss" and changes[-1].reason == "scripted feed, unverified"
    up = call(ctx, "set_source_reliability", input_id="local_news", reliability=0.9, reason="looks fine")
    assert up["ok"] is False and "only lowers" in up["error"]
    push = call(ctx, "set_source_reliability", input_id="panic", reliability=0.1, reason="x")
    assert push["ok"] is False and "pull input" in push["error"]


def test_scout_tools_are_scout_only(ctx):
    critic_names = {t["function"]["name"] for t in registry.tool_schemas("critic")}
    assert not {"web_search", "fetch_url", "probe_feed", "geocode", "set_source_reliability"} & critic_names
