"""Web search for the Scout (Tavily). Returns a compact list (title, url, short snippet), never raw pages.

The key comes from settings (`TAVILY_API_KEY`) and is never logged or returned. Without a key the tool
reports that search isn't configured, so the Scout falls back to sources the dev named. Tests inject
an `httpx.Client` on a MockTransport through `ctx.extra["http_client"]`; no network in unit tests.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.tools.context import ToolContext

TAVILY_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
SNIPPET_CHARS = 300


class WebSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=5, ge=1, le=MAX_RESULTS)


def web_search(ctx: ToolContext, args: WebSearchArgs) -> dict[str, Any]:
    from app.core.config import get_settings

    key = get_settings().tavily_api_key
    if not key:
        raise LookupError("web search is not configured (no search API key); use sources the dev named")
    body = {"api_key": key, "query": args.query, "max_results": args.max_results, "search_depth": "basic"}
    client = ctx.extra.get("http_client")
    if client is not None:
        resp = client.post(TAVILY_URL, json=body, timeout=15.0)
    else:
        with httpx.Client(timeout=15.0) as c:
            resp = c.post(TAVILY_URL, json=body)
    resp.raise_for_status()
    results = [
        {
            "title": str(r.get("title", ""))[:120],
            "url": str(r.get("url", "")),
            "snippet": " ".join(str(r.get("content", "")).split())[:SNIPPET_CHARS],
        }
        for r in (resp.json().get("results") or [])[: args.max_results]
        if r.get("url")
    ]
    return {"query": args.query, "results": results}
