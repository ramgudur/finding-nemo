"""Demo backend for `tools/demo <app> --live`: the real app, with its compile cache seeded from a
recorded clean Compiler run, so `POST /v1/apps/<app>/compile` answers at once (a cache hit:
`finalize_app` guards and a "cache hit, no LLM" Compiler trace entry, no model call).

    cd backend && DEMO_SEED_COMPILE=flyway python -m uvicorn --factory --app-dir ../tools demo_server:app

The recorded run is backend/tests/fixtures/llm/compiler_<app>.json (its `finalize_app` spec); the
cache key is the dev's request in specs/compile/<app>.json, exactly what tools/demo POSTs. Live
Nemotron compiles vary run to run (30-100 s, and the operators they pick), so the table demo
replays a clean one; `tools/demo <app> --live-llm` skips this and really calls the model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Run from backend/ (as tools/demo does), so the `app` package is importable.
from app.agents.compiler import CompilerCache
from app.core.models import AppSpec, CompileRequest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "llm"
REQUESTS = ROOT / "specs" / "compile"


def recorded_spec(app_id: str) -> AppSpec:
    """The spec the recorded Compiler run passed to `finalize_app`, as it lands: pending_confirmation."""
    script = json.loads((FIXTURES / f"compiler_{app_id.replace('-', '_')}.json").read_text(encoding="utf-8"))
    calls = [tc for s in script["steps"] for tc in (s.get("response") or {}).get("tool_calls") or []]
    final = next(tc for tc in reversed(calls) if tc["name"] == "finalize_app")
    spec = AppSpec.model_validate(final["arguments"]["spec"])
    return spec.model_copy(update={"status": "pending_confirmation"})


def seed(cache: CompilerCache, app_id: str) -> CompileRequest:
    req = CompileRequest.model_validate_json((REQUESTS / f"{app_id}.json").read_text(encoding="utf-8"))
    # The same normalization api/apps.py applies before it looks the request up in the cache.
    cache.put(
        req.intent,
        [i.model_dump(mode="json") for i in req.inputs],
        recorded_spec(app_id),
    )
    return req


def seeded_cache(app_ids: list[str]) -> CompilerCache:
    cache = CompilerCache()
    for app_id in app_ids:
        seed(cache, app_id)
    return cache


def app():
    from app.main import create_app

    app_ids = [a for a in os.environ.get("DEMO_SEED_COMPILE", "").split(",") if a]
    return create_app(compiler_cache=seeded_cache(app_ids))
