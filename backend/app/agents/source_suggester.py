"""`agents/source_suggester.py`: "Add a source" in the Builder -- infer how a described source connects.

The dev describes one source in plain words. One structured call to EXTRACT_MODEL (Lightning,
reasoning off, low effort) infers its `kind` (push or pull), its `shape`, and a ranked list of
connection options drawn only from `catalogue()`. The answer validates against
`_ModelSuggestion`, with one REPAIR_MODEL retry (CLAUDE.md output validation). Code, not the
model, fills in option labels, keeps only catalogue presets, drops any URL that is not in the
dev's own words, and sets `meaning` to the description verbatim.

On any failure (rate budget empty, provider error, invalid twice) the result is the deterministic
fallback: every catalogue option, unranked, with a neutral default kind/shape the dev overrides.
It is a suggestion only; nothing here writes app state. Every step writes a `TraceEntry` under the
Compiler, whose pre-compile step this is.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents.llm import LlmRouter, RateLimited
from app.agents.loop import _repair
from app.connectors.presets import PRESETS
from app.core.models import InputKind, InputShape, SourceOption, SourceOptionType, SourceSuggestion, TraceEntry
from app.tools.context import ToolContext

log = logging.getLogger(__name__)

AGENT = "compiler"
MODEL_FIELD = "extract"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "source_suggester.md").read_text(encoding="utf-8")

OPTION_KIND: dict[SourceOptionType, InputKind] = {
    "post_api": "push",
    "webhook": "push",
    "rss": "pull",
    "json_api": "pull",
}
FALLBACK_KIND: InputKind = "push"
FALLBACK_SHAPE: InputShape = "point"


def catalogue() -> list[SourceOption]:
    """Every option the platform offers now, push first. Presets come from the connector catalogue."""
    out = [
        SourceOption(
            type="post_api",
            label="POST API",
            why="Your app posts each item to a generated endpoint with its ingest key.",
        ),
        SourceOption(
            type="webhook",
            label="Webhook URL",
            why="The same generated endpoint, given to a service that calls a webhook.",
        ),
        SourceOption(
            type="rss",
            label="RSS / Atom feed",
            why="The platform polls a feed of documents; the Scout can find one on request.",
        ),
    ]
    for name, preset in sorted(PRESETS.items()):
        fields = ", ".join(preset.get("fields", [])) or "values"
        out.append(
            SourceOption(
                type="json_api",
                label=f"JSON API preset: {name}",
                why=f"A ready-made JSON API config that returns {fields}.",
                config={"preset": name},
            )
        )
    out.append(
        SourceOption(type="json_api", label="JSON API (custom URL)", why="The platform polls a JSON endpoint you name.")
    )
    return out


def _key(o: SourceOption | _ModelOption) -> tuple[str, str | None]:
    preset = (o.config or {}).get("preset")
    return (o.type, preset if isinstance(preset, str) else None)


class _ModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SourceOptionType
    why: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _config_shape(self) -> _ModelOption:
        cfg = self.config or {}
        if set(cfg) - {"preset", "url"}:
            raise ValueError("config may only hold 'preset' or 'url'")
        if "preset" in cfg:
            if self.type != "json_api":
                raise ValueError("only json_api options take a preset")
            if cfg["preset"] not in PRESETS:
                raise ValueError(f"unknown preset {cfg['preset']!r}; use one from the catalogue")
        if "url" in cfg and not isinstance(cfg["url"], str):
            raise ValueError("config.url must be a string")
        return self


class _ModelSuggestion(BaseModel):
    """What the model must return; `meaning` and labels are added by code."""

    model_config = ConfigDict(extra="forbid")

    kind: InputKind
    shape: InputShape
    options: list[_ModelOption] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _options_match_kind(self) -> _ModelSuggestion:
        wrong = [o.type for o in self.options if OPTION_KIND[o.type] != self.kind]
        if wrong:
            raise ValueError(f"options {wrong} do not fit kind {self.kind!r}")
        return self


def fallback(description: str) -> SourceSuggestion:
    """The full catalogue, unranked (both kinds), with the neutral default kind/shape."""
    return SourceSuggestion(kind=FALLBACK_KIND, shape=FALLBACK_SHAPE, options=catalogue(), meaning=description)


def build_suggestion(raw: _ModelSuggestion, description: str) -> SourceSuggestion:
    """Model answer -> contract: catalogue labels, deduped, URLs kept only if the dev wrote them,
    then the rest of that kind's catalogue (default why) so the dev can still pick any of them."""
    by_key = {_key(o): o for o in catalogue()}
    seen: set[tuple[str, str | None]] = set()
    options: list[SourceOption] = []
    for o in raw.options:
        k = _key(o)
        if k in seen:
            continue
        seen.add(k)
        base = by_key[k] if k in by_key else by_key[(o.type, None)]
        config: dict[str, Any] | None = None
        if OPTION_KIND[o.type] == "pull":
            cfg = dict(o.config or {})
            url = cfg.pop("url", None)
            if isinstance(url, str) and url and url in description and "preset" not in cfg:
                cfg["url"] = url
            config = cfg or None
        options.append(SourceOption(type=o.type, label=base.label, why=o.why, config=config))
    for k, base in by_key.items():
        if k not in seen and OPTION_KIND[base.type] == raw.kind:
            options.append(base)
    return SourceSuggestion(kind=raw.kind, shape=raw.shape, options=options, meaning=description)


def task_prompt(intent: str, description: str) -> str:
    lines = [
        f"- type={o.type}"
        + (f" preset={o.config['preset']}" if o.config and "preset" in o.config else "")
        + f" (kind {OPTION_KIND[o.type]}): {o.label}. {o.why}"
        for o in catalogue()
    ]
    return f"App intent: {intent}\n\nSource, in the developer's words:\n{description}\n\nCatalogue:\n" + "\n".join(
        lines
    )


def _trace(ctx: ToolContext, **fields: Any) -> None:
    ctx.db.add_trace(TraceEntry(t=ctx.now_dt(), app_id=ctx.app_id, agent=AGENT, **fields))


async def suggest_source(ctx: ToolContext, intent: str, description: str, llm: LlmRouter) -> SourceSuggestion:
    """One structured call; never raises for model trouble -- returns `fallback()` instead."""
    model_id = llm.settings.agent_model(MODEL_FIELD).model_id
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task_prompt(intent, description)},
    ]
    saw = description[:200]
    try:
        resp = await llm.chat(
            MODEL_FIELD, messages, enable_thinking=False, low_effort=True, response_format={"type": "json_object"}
        )
    except RateLimited as e:
        _trace(ctx, model=model_id, action="skipped", saw=saw, skipped_reason="rate budget", why=f"suggest_source: {e}")
        return fallback(description)
    except Exception as e:  # noqa: BLE001 - a provider error must not break the Builder; fall back
        log.warning("suggest_source call failed: %s", type(e).__name__)
        _trace(ctx, model=model_id, action="fallback", saw=saw, why=f"suggest_source call failed: {type(e).__name__}")
        return fallback(description)

    content = resp.choices[0].message.content or ""
    why = "validated"
    try:
        raw: _ModelSuggestion | None = _ModelSuggestion.model_validate_json(content)
    except ValidationError as e:
        raw = await _repair(ctx, llm, AGENT, _ModelSuggestion, content, str(e))
        why = "validated after repair"
    if raw is None:
        _trace(ctx, model=model_id, action="fallback", saw=saw, why="suggest_source: unranked catalogue")
        return fallback(description)
    out = build_suggestion(raw, description)
    _trace(
        ctx,
        model=model_id,
        action="suggest_source",
        saw=saw,
        result=json.dumps(out.model_dump(exclude={"meaning"}))[:500],
        why=why,
    )
    return out
