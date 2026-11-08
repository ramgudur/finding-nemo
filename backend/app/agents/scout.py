"""`agents/scout.py`: the Scout -- finds, configures and tests outside sources for pull inputs.

Runs **on request only** (CLAUDE.md agents table): the Compiler's `request_scout` tool writes a
TraceEntry (agent=compiler, action=request_scout, saw=<input_id>, why=<instructions>), and
`ScoutRunner` picks up requests that no later Scout run has handled. A dev request
(`POST /v1/apps/{app}/scout`) goes through `ScoutRunner.enqueue`, which writes the same kind of
trace entry (agent=scout, action=requested), so both reach the Scout by one path.
Connectors it creates keep polling on their own (connectors/engine.py).

`extract_article` is the per-article step: EXTRACT_MODEL with reasoning off turns one document
into an `ArticleExtract` (relevant? where? how confident?), validated against the Pydantic model
with one REPAIR_MODEL retry (CLAUDE.md output validation). It never sees raw event arrays, only
one document's text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents.llm import LlmRouter, RateLimited
from app.agents.loop import _repair, run_agent
from app.core.db import Db
from app.core.models import TraceEntry
from app.engine.tick import TickEngine
from app.tools.context import ToolContext

log = logging.getLogger(__name__)

MAX_ITERATIONS = 8
MAX_ARTICLE_CHARS = 4000  # the extractor sees one document, truncated
MAX_REQUEST_CHARS = 500  # a dev's plain-text Scout request (trace is authority-only; still capped)
DEV_REQUEST_ACTION = "requested"

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "scout.md").read_text(encoding="utf-8")

EXTRACT_SYSTEM = (
    "You read one document for a geo heat-map app and return ONLY a JSON object: "
    '{"relevant": bool, "lat": number|null, "lon": number|null, "place": string|null, '
    '"confidence": number 0..1, "summary": string}. relevant: does it report what the input means, '
    "inside the app's area? Give lat/lon only if the document states or clearly implies a location; "
    "otherwise give the place name and leave lat/lon null. Never guess coordinates. summary: one "
    "neutral sentence."
)


class ArticleExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant: bool
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    place: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=500)

    @model_validator(mode="after")
    def _location(self) -> ArticleExtract:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon go together")
        if self.relevant and self.lat is None and not self.place:
            raise ValueError("a relevant document needs lat/lon or a place name")
        return self


def _trace(ctx: ToolContext, **fields) -> None:
    ctx.db.add_trace(TraceEntry(t=ctx.now_dt(), app_id=ctx.app_id, agent="scout", **fields))


async def extract_article(
    ctx: ToolContext, title: str, text: str, input_meaning: str, llm: LlmRouter | None = None
) -> ArticleExtract | None:
    """One document -> ArticleExtract, or None (rate-limited or invalid twice; both traced)."""
    llm = llm or LlmRouter()
    body = f"{title}\n\n{text}"[:MAX_ARTICLE_CHARS]
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {
            "role": "user",
            "content": f"App intent: {ctx.spec.intent}\nInput means: {input_meaning}\n\nDocument:\n{body}",
        },
    ]
    try:
        resp = await llm.chat("extract", messages, enable_thinking=False)
    except RateLimited as e:
        _trace(ctx, action="skipped", skipped_reason="rate budget", why=f"extract: {e}")
        return None
    content = resp.choices[0].message.content or ""
    try:
        out = ArticleExtract.model_validate_json(content)
    except ValidationError as e:
        out = await _repair(ctx, llm, "scout", ArticleExtract, content, str(e))
        if out is None:
            return None
        _trace(ctx, action="extract", result=json.dumps(out.model_dump())[:500], why="validated after repair")
        return out
    _trace(ctx, action="extract", result=json.dumps(out.model_dump())[:500], why="validated")
    return out


def task_prompt(ctx: ToolContext, input_id: str, instructions: str) -> str:
    spec = ctx.spec
    inp = next(i for i in spec.inputs if i.id == input_id)
    b = spec.grid.bbox
    fields = ", ".join(f"{f.name} ({f.type})" for f in inp.fields) or "none declared"
    return (
        f"App intent: {spec.intent}\n"
        f"Area (bbox): lat {b.min_lat:.4f}..{b.max_lat:.4f}, lon {b.min_lon:.4f}..{b.max_lon:.4f}\n"
        f"Input to source: {inp.id} ({inp.shape}); it means: {inp.meaning}\n"
        f"Declared fields: {fields}\n"
        f"Instructions: {instructions or 'find and configure a working source'}"
    )


async def run_scout(
    ctx: ToolContext, input_id: str, instructions: str = "", llm: LlmRouter | None = None
) -> str | None:
    """One Scout episode for one pull input. Always marks the request handled (traced), so a
    failed run is not retried in a loop; a new request_scout asks again."""
    if not any(i.id == input_id and i.kind == "pull" for i in ctx.spec.inputs):
        _trace(ctx, action="skipped", saw=input_id, skipped_reason="not a pull input of this app")
        return None
    result = await run_agent(
        ctx,
        agent="scout",
        model_field="scout",
        system=SYSTEM_PROMPT,
        task=task_prompt(ctx, input_id, instructions),
        llm=llm,
        max_iterations=MAX_ITERATIONS,
        enable_thinking=True,
    )
    _trace(ctx, action="request_handled", saw=input_id, result=(result or "")[:500] if isinstance(result, str) else "")
    return result if isinstance(result, str) else None


def _is_request(e: TraceEntry) -> bool:
    return (e.agent == "compiler" and e.action == "request_scout") or (
        e.agent == "scout" and e.action == DEV_REQUEST_ACTION
    )


def request_instructions(e: TraceEntry) -> str:
    """The Scout's instructions from a request entry: the Compiler's `why`, or a dev request's
    text (kept in `hypothesis`; its `why` is the fixed "dev request")."""
    return e.hypothesis if e.agent == "scout" else e.why


def pending_requests(db: Db, app_id: str) -> list[TraceEntry]:
    """Scout requests (Compiler `request_scout` or dev `requested`) newer than the last Scout run
    that handled the same input."""
    trace = db.list_trace(app_id)
    handled: dict[str, float] = {}
    for e in trace:
        if e.agent == "scout" and e.action == "request_handled":
            handled[e.saw] = max(handled.get(e.saw, 0.0), e.t.timestamp())
    latest: dict[str, TraceEntry] = {}
    for e in trace:
        if _is_request(e) and e.t.timestamp() > handled.get(e.saw, -1.0):
            latest[e.saw] = e  # one run per input covers every pending request for it
    return list(latest.values())


class ScoutRunner:
    """Runs the Scout for pending requests across all apps in `engine`. On request only: no
    periodic re-evaluation (stubbed in CLAUDE.md scope)."""

    def __init__(self, db: Db, engine: TickEngine, llm: LlmRouter | None = None, poll_s: float = 5.0) -> None:
        self.db = db
        self.engine = engine
        self.llm = llm or LlmRouter()
        self.poll_s = poll_s
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def enqueue(self, app_id: str, input_id: str, request: str) -> str:
        """Queue a dev request for one input; returns a job id. Writes the request as a trace entry
        (the same queue `request_scout` uses), so it never blocks: the run loop picks it up, and an
        empty rate budget becomes the Scout's usual skipped trace."""
        job_id = uuid.uuid4().hex
        self.db.add_trace(
            TraceEntry(
                t=datetime.fromtimestamp(self.engine.clock.now(), UTC),
                app_id=app_id,
                agent="scout",
                action=DEV_REQUEST_ACTION,
                saw=input_id,
                hypothesis=request[:MAX_REQUEST_CHARS],
                result=f"job {job_id}",
                why="dev request",
            )
        )
        return job_id

    async def check_once(self) -> None:
        for app_id in list(self.engine.apps):
            for req in pending_requests(self.db, app_id):
                ctx = ToolContext(app_id=app_id, db=self.db, engine=self.engine, agent="scout", clock=self.engine.clock)
                try:
                    await run_scout(ctx, req.saw, request_instructions(req), self.llm)
                except Exception:  # noqa: BLE001 - one request must not stop the others
                    log.exception("scout run failed for app %s input %s", app_id, req.saw)

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)
            except TimeoutError:
                pass

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None


def article_extractor(db: Db, engine: TickEngine, llm: LlmRouter | None = None):
    """Adapter for `connectors.engine.make_extract_locator`: (spec, input, title, text) ->
    ArticleExtract | None, running `extract_article` in the app's context."""
    router = llm or LlmRouter()

    async def extract(spec, inp, title: str, text: str) -> ArticleExtract | None:
        ctx = ToolContext(app_id=spec.app_id, db=db, engine=engine, agent="scout", clock=engine.clock)
        return await extract_article(ctx, title, text, inp.meaning, router)

    return extract
