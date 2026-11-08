@AGENTS.md

# Geo heat-map platform: Claude Code guide

This is the build spec for the repo. If AGENTS.md and this file disagree, stop and ask. Keep this file in sync whenever layout, commands or contracts change.

## What this is

A platform for building **intelligent geo heat-map apps** ("anything as an input").

A developer names an app, describes its intent in plain English and declares its inputs:
- **Push inputs:** the dev's own app POSTs to an endpoint we generate.
- **Pull inputs:** the platform collects the data itself, e.g., "find local news sources that report on this".

The platform:
1. Turns each input into a grid layer with a deterministic **operator**.
2. Combines the layers into a live score map, hotspots and **POI labels**.
3. Optionally gives end users route **instructions** (toward destinations, away from high scores).
4. Serves all of it over REST and WebSocket.

NVIDIA Nemotron runs four agents on top of that deterministic core:

| Agent | Job |
|---|---|
| **Compiler** | Turns the dev's intent and inputs into a *proposed* `AppSpec`. The dev confirms it. |
| **Scout** | Finds, configures and tests outside data sources (connectors). |
| **Critic** | Tunes layer weights and per-user trust. Every weight change must pass a held-out backtest. |
| **POI Labeler** | For **any** app, decides whether the data contains places worth naming, then names them with evidence. |

Framing used in code, UI and docs: a **mixture of experts at the system level**. Operators are the experts, and Nemotron is the gate deciding how much each one counts, within locked rules.

## Non-negotiable rules

If a task seems to require breaking one of these, stop and ask.

1. **Nemotron proposes, code decides.**
   - Agents never write state directly. Every change goes through a tool in `backend/app/tools/`, validated in `guards.py`.
   - A weight change needs a `backtest_id` whose **held-out** score passed. The guards re-check the candidate before applying it.
2. **No LLM in the hot path.**
   - The engine tick (every 2 s) is pure numpy. Agents run asynchronously.
   - If the rate budget is empty, the agent skips its run and writes a trace entry. It never blocks the tick.
3. **Platform code is app-agnostic and domain-neutral ("one size fits all").**
   - No app-specific IDs, coordinates, label names, thresholds or "if campus/birds" branches in `backend/`.
   - Nothing in `backend/` assumes what the entities or data are (people, animals, vehicles, sensors, weather, news). Operator names, params, defaults, docstrings and the descriptions the Compiler reads use neutral words: "entities", "vector field", "documents", "authority fixed points".
   - Any physical limit or threshold that depends on the domain (speeds, distances, hotspot thresholds) comes from the `AppSpec`, with a neutral default. Fixed distances scale with `cell_m`.
   - Domain knowledge enters only through the spec, which the Compiler writes from the dev's words. Demo material lives in `sim/` and `specs/`.
   - **How strict:** neutrality is for platform *logic* and *Compiler-facing text* (code, params, defaults, docstrings, schema descriptions). It isn't absolute: catalogue data such as connector presets (e.g. `open_meteo`) may name real sources, as long as no code depends on a specific one. Specs, simulators and scenario tests use scenario words freely (birds, wind, panic buttons).
4. **Locked stays locked.** The Compiler only *proposes* tiers, verification methods and their priority. They take effect only after the dev confirms the spec. After that, nothing an agent does can change verification methods, input tiers or locked weights.
5. **Tier and trust bounds** (enforced in `engine/tiers.py`, `engine/trust.py` and `tools/guards.py`):

   | Tier | Used for | Weight changes allowed |
   |---|---|---|
   | `locked` | authority inputs | never |
   | `stiff` | news, official sensors | at most ±10% per hour, and only when `authority_confirmation` is the backtest's deciding method |
   | `flexible` | crowd inputs | at most ±25% per step. Flexible weights are renormalized so their sum stays at the value set at creation. Stiff and locked weights are never moved by renormalization. |

   - **Trust:** agents may lower a user's trust freely (with a reason). Increases need a passing backtest (`run_backtest` → `apply_changes`) and are capped at **+0.1 per user per 5 minutes** of Clock time. `set_user_trust` only lowers.
   - **Cold start:** reject flexible weight changes until **at least two** verification methods have data. Return a clear reason so it shows in the trace.
   - Stiffness comes from the tier set at creation, never from the weight's current value.
6. **Authority overrides always win.** They are applied after the weighted sum.
7. **POIs come only from code-generated candidates.** Agents never invent coordinates. Labels are outputs only and never feed back into scores.
8. **Sensitive data stays in the authority view.** Raw inputs, sensitive POIs, the trace and other users' locations never reach a request that lacks the app's authority key. Public end users get **instructions only**.
9. **Simulator ground truth never reaches the engine or the agents.** Only `api/debug.py` reads it, and only when `DEMO_MODE=1`.
10. **Connectors are config, not code.** Never run LLM-generated code.
11. **Everything is traced and reversible.**
    - Every agent step writes a `TraceEntry`.
    - Every state change writes a `Change` with before/after values, so the dev can roll it back.
    - The dev can pin any weight.
12. **Never claim Nemotron's internal MoE routes our inputs.** Our "mixture of experts" is system-level.
13. **We code the platform, never the demos.** The demo apps (Wildfire, Bird tracking) are built in the video by **using the platform we are coding**: Builder, Add a source, live compile, confirm.
    - Don't write demo app specs, demo-specific code, cached or replayed compiles, or prompt text aimed at a demo.
    - Don't build, compile or rehearse a demo app ahead of the video, not even through the platform.
    - Test platform features with neutral examples that aren't the demo apps.
    - If something a demo will need doesn't work, fix it as a general platform feature.
    - Data simulators in `sim/` are allowed: they stand in for real users and devices, which can't be filmed. They only post through the public ingest API, and they never create or configure the app.

## Architecture

```
dev's app / simulator ──POST events (ingest key)──► Ingest API ──► Event store (SQLite log + in-memory numpy)
pull connectors (RSS / JSON config, Open-Meteo) ──────────────────►      │
                                                                         ▼
  Engine tick (2 s): operators → normalized layers (noise floor) → Σ wᵢ·layerᵢ (trust inside operators)
  → authority overrides → score grid + hotspots; candidate generators → POI candidates
  → layer-stack cache [T, L, H, W] for backtests
                 ▲ gated changes only                   │ summaries only
                 │                                      ▼
     Agents (Nemotron, tiered models): Compiler · Scout · Critic · POI Labeler ◄──► tool layer + guards
                                                        │
  Output API, filtered by visibility: map · hotspots · POIs · raw · trace · WS stream · instructions
  Dev console (authority view) · end-user page (public instructions only)
```

## Repo layout

```
backend/
  app/
    main.py                 FastAPI app; mounts routers; starts the engine and agent schedulers
    core/
      config.py             settings from env (pydantic-settings), including per-agent model config
      models.py             SOURCE OF TRUTH for contracts (Pydantic v2)
      db.py                 SQLite: apps, inputs, events, connectors, weights_history, trust, pois,
                            trace, changes, backtests, keys
      clock.py              Clock (wall or sim time); the engine and guards read time only from here
      access.py             ingest key and authority key checks; visibility filtering helpers
    api/                    apps.py (compile, confirm, activate), ingest.py, outputs.py, ws.py,
                            instructions.py, webhooks.py (501 stub), debug.py (DEMO_MODE only)
    engine/
      tick.py, grid.py, combine.py, tiers.py, trust.py
      verification.py       authority_confirmation, predictive_check (leave-one-input-out), cross_input_agreement
      layer_cache.py        ring buffer of normalized layer stacks + override masks per tick
      backtest.py           single-candidate (re-runs operators) and batch (vectorized; numpy or CuPy)
      routing.py            grid A* for end-user instructions
      operators/            registry.py, flow_source.py, density.py, dwell.py, field.py, event_points.py, override.py
      candidates/           registry.py, dwell_clusters.py, flow_endpoints.py, event_clusters.py,
                            persistent_hotspots.py
    agents/
      llm.py                OpenAI-compatible clients per provider, per-provider token buckets, retry/backoff
      loop.py               generic tool-calling loop; writes TraceEntries; caps iterations
      compiler.py, scout.py, critic.py, poi_labeler.py
      prompts/              one prompt file per agent
    tools/                  registry.py (JSON schemas), guards.py, scoring_tools.py, poi_tools.py,
                            connector_tools.py, geo_tools.py (Nominatim, Overpass, Open-Meteo),
                            search_tools.py (Tavily), message_guard.py
    connectors/             engine.py (runs configs), rss.py, json_api.py, presets/ (ready-made json_api configs, e.g. open_meteo)
  tests/                    plus tests/fixtures/ (recorded LLM and HTTP responses)
frontend/                   React + Vite + TypeScript; Leaflet
  src/pages/                Builder.tsx (compile → review tiers → confirm), AppPage.tsx, EndUser.tsx
  src/components/           Map, LayerToggles, WeightsPanel (shows total flexible weight), AgentTrace,
                            PoiPin, VerificationScores, ErrorChart, SimControls
  src/api/types.ts          mirrors core/models.py; update both together
sim/                        simulators; post ONLY through the public ingest API
  ground_truth/             read only by api/debug.py
specs/                      campus-alert.json, flyway.json
data/                       SQLite db, flywheel/critic.jsonl (gitignored)
```

## Commands

```bash
# backend
cd backend && pip install -r requirements.txt     # cupy is optional; installed only on GPU boxes
uvicorn app.main:app --reload --port 8000
pytest -q                                          # no network, no live LLM calls, fixed seeds
ruff check . && ruff format .

# frontend
cd frontend && npm install && npm run dev          # http://localhost:5173, proxies /v1 to :8000

# simulators
python -m sim.campus_alert --api http://localhost:8000 --app campus-alert --speed 1
python -m sim.flyway --api http://localhost:8000 --app flyway --replay sim/replays/flyway_short.jsonl
```

## Environment (`backend/.env`; never commit it)

```
NVIDIA_API_KEY=
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# per-agent models, format "<provider>::<model-id>". Verify exact IDs on first run; log them at startup.
COMPILER_MODEL=nvidia::nvidia/nemotron-3.5-super-vl-120b-a12b
CRITIC_MODEL=nvidia::nvidia/nemotron-3.5-super-vl-120b-a12b
SCOUT_MODEL=nvidia::nvidia/nemotron-3.5-super-vl-120b-a12b
EXTRACT_MODEL=nvidia::nvidia/nemotron-3.5-lightning-30b-a3b
POI_MODEL=nvidia::nvidia/nemotron-3.5-lightning-30b-a3b
REPAIR_MODEL=nvidia::nvidia/nemotron-3.5-lightning-30b-a3b
SAFETY_MODEL=nvidia::nvidia/llama-3.1-nemotron-safety-guard-8b-v3   # used only if instruction rephrasing is on

NVIDIA_RPM=35              # free tier is ~40 RPM, shared across all models
OPENROUTER_RPM=18          # free models: 20 RPM; daily cap of 50 unless the account has bought >= $10 of credits ever
TAVILY_API_KEY=
NOMINATIM_USER_AGENT=      # required by Nominatim policy; generic app name + team contact
SQLITE_PATH=./data/app.db
COMPILE_MIN_GAP_S=10       # optional: minimum seconds between compiles of one app (/compile returns 429 + Retry-After)
DEMO_MODE=0                # 1 enables /v1/debug/* (ground-truth error chart)
```

Ingest keys and authority keys are generated per app at activation and stored in the DB, not in env.

## Core contracts

`core/models.py` is authoritative. When it changes, update `frontend/src/api/types.ts`, `specs/*.json` and this section in the same commit.

**AppSpec:**
- `app_id`, `intent`, `status` (`draft` | `pending_confirmation` | `active`)
- `grid`: `{cell_m, bbox: {min_lat, min_lon, max_lat, max_lon}}`. Named fields in the spec; `bbox.as_geojson()` gives the `(min_lon, min_lat, max_lon, max_lat)` tuple the engine `Grid` takes.
- `inputs[]`, each with:
  - `id`, `kind` (`push` | `pull`), `shape` (`point` | `track` | `fixed_point` | `field` | `document`)
  - `tier`
  - `visibility` (default `authority`)
  - `meaning` (the dev's words, verbatim)
  - `fields[]`: `{name, type: number|integer|string|boolean, required, description}`
  - `connector?`: `{type: rss|json_api, preset?, url?, poll_s, config{}}`. Push inputs may not have one. `preset` names a ready-made `json_api` config (e.g. `open_meteo`); connector types stay generic.
  - `plausibility?` (tracks/points): `{max_speed_mps?, jitter_m?, flag_constant_speed?, flag_straight_line?}`. The spoof checks read these per input; the Compiler proposes them from `meaning` (a bird moves differently from a person). Defaults are neutral: no speed limit, and the constant-speed and straight-line checks are off. Only checks that hold for any entity run without a spec value (duplicate coordinates across accounts, bursts of new IDs, shared device/IP). Speed and shape checks run only when the spec sets them.
  - `operators[]`: `{op, weight, noise_floor?, layer_id?, ...params}`. `layer_id` defaults to `<input_id>.<op>`; `OperatorConfig.params()` returns what `registry.run` takes.
- `verification[]`: `{method, priority}`. Priority 1 is highest; methods and priorities are unique.
- `outputs.layers[]`: `{id, visibility}`. `id` is a layer id or `score`. Visibility defaults to `authority` everywhere. `score` may be `public` only if every weighted layer is public (it's derived from all of them); `validate_spec` rejects it otherwise (rule 8, same principle as hotspot visibility).
- `outputs.hotspot_threshold`: score at or above which connected cells form a hotspot (default 0.5).
- All datetimes are timezone-aware and normalized to UTC (`UtcDatetime`).
- `outputs.poi_labels`: `{mode: auto|on|off, label_set: [{label, sensitive, destination}], min_confidence}`. A `sensitive` label is authority-only. A `destination` label is a routing target and must not also be `sensitive`.
- `routing`: `{enabled, destination_points[{lat, lon, label}], impassable_score, alpha, fallback_text (≤ 360 chars), rephrase: false}`

**Building an app (dev, no key until activation):**
- `GET /v1/operators` (no key): the operator catalogue, `OperatorInfo[] {name, description, shapes, role, params}` (params is a JSON schema). It holds no app data.
- `POST /v1/apps/{app}/sources/suggest`, body `{intent, description}`, returns `SourceSuggestion {kind, shape, options[{type, label, why, config?}], meaning}`. A model (EXTRACT, structured output with one repair retry) infers push or pull, the shape and ranked options from the dev's description. `meaning` is the description verbatim. It always returns 200, falling back to the full unranked catalogue. Option types: `post_api`, `webhook` (the same endpoint), `rss`, `json_api` presets and custom `json_api`; no HTML scraping. SMS comes later.
- `POST /v1/apps/{app}/compile`, body `CompileRequest {intent, grid, inputs: CompileInput[], destination_points?}`. `CompileInput` is `{id, kind, shape, meaning, fields, connector?}`; `connector` is pull only, checked by the URL guard. Connectors and `destination_points` are the dev's words: `finalize_app` copies them verbatim (a connector writes a `Change`, kind `connector`, actor `dev`). Returns the `AppSpec` at `pending_confirmation`; `409` if the app is active, `422` if it never finalizes, `503` on rate budget.
- `PUT /v1/apps/{app}/spec`: the dev edits a `pending_confirmation` spec (full `AppSpec`; `409` otherwise, `422` with every error). It writes a `Change`, kind `spec_edit`, actor `dev`.
- `POST /v1/apps/{app}/activate` is the only dev confirmation.
- `GET /v1/apps/{app}/compile/status` (public, no trace text or tool args): `CompileStatus {running, step, elapsed_s, last_validate?: {errors, warnings}}`, 404 once the app is active. A compile that never finalizes returns `422` with `CompileFailure {reason, errors[], warnings[]}`; `503` carries `Retry-After`.

**Running app (authority key):**
- `POST /v1/apps/{app}/changes/{id}/revert`: applies the Change's `before` through the same tools and guards as `actor=dev`, and writes a new Change with `reverts=<id>`. Dev reverts are exempt from the agent rate caps (trust +0.1 per 5 min, stiff ±10%/h), but not from the flexible ±25% per-step limit or any hard bound. `409` if the target changed since; `422` for `spec_confirm` or a guard failure.
- `GET /v1/apps/{app}/label-proposals`, which returns `LabelProposal[] {id, label, sensitive, destination, why, status: open|accepted|dismissed, t}`.
- `POST /v1/apps/{app}/label-proposals/{id}/accept`, optional body `{sensitive?, destination?}`, which returns `{proposal, label_set}`. It adds the label to the running spec and writes a `spec_edit` Change.
- `POST /v1/apps/{app}/label-proposals/{id}/dismiss`.
- `POST /v1/apps/{app}/scout`, body `ScoutRequest {input_id, request (1..500 chars)}`, returns `202 ScoutJobAccepted {job_id, app_id, input_id}`. 409 if the app isn't active; 422 for a push input. The request text goes only to the authority trace.
- The POI Labeler proposes labels with the `propose_label` tool (at most 5 open per app). `why` is agent text and is authority-only.

**Event (push):** `POST /v1/apps/{app}/inputs/{input}/events`
- Header `X-Ingest-Key`; body `{user_id?, lat, lon, ts, ...input fields}`.
- Validated against the input's `fields`. Rejected unless the app is `active`.

**Other contracts:**
- **Candidate:** `{id, lat, lon, radius_m, source, stats, contributing_layers, persisted_ticks}`
- **POI:** `{id, candidate_id, lat, lon, radius_m, label, confidence, evidence[], visibility, status, created_at, updated_at}`
- **Backtest:** `{id, kind: single|batch, candidates_tested, candidates_valid, train_scores, holdout_scores, passed, device: cpu|gpu, elapsed_ms, t}`
- **TraceEntry:** `{t, app_id, agent, model, saw, hypothesis, tested, result, action, why, tool_calls[], skipped_reason?}`
- **Change:** `{id, app_id, kind: weight|trust|poi|connector|spec_edit|spec_confirm, target, before, after, reason, backtest_id?, actor: agent|dev, reverts?, t}`
- **Instruction:** `{text, destination{lat, lon, label}, route[[lat, lon]], distance_m, computed_at, guard{passed, checks[]}}`
- **Hotspot:** `{id, lat, lon, radius_m, score, mean, area_m2, n_cells, persisted_ticks, contributing_layers[], visibility}`. `score` is the peak cell score; `radius_m = sqrt(area_m2 / π)`. The tick produces it; `/hotspots` and the Critic/POI tools read it.
- **MapResponse** (`GET /map?format=geojson`): `{app_id, t, cell_m, bbox: [min_lon, min_lat, max_lon, max_lat], layers: {<layer_id> | "score": FeatureCollection}}`. One Polygon feature per cell with `properties: {value, row, col}` (a single `value` field). Cells with `value < 0.01` are omitted to keep the payload small. Without the authority key, only `public` layers are included.
- **HotspotsResponse** (`GET /hotspots`): `{app_id, t, hotspots: Hotspot[]}`, filtered per item by `visibility`.

`Hotspot`, `MapResponse` and `HotspotsResponse` are Pydantic models in `core/models.py`, mirrored in `types.ts`, like every other contract.

**Outputs.** Without the `X-Authority-Key` header, responses contain only `public` items. This applies to REST and WebSocket alike. Browsers can't set WebSocket headers, so the WS client sends `{type: "auth", key}` as its first frame; until a valid key arrives the stream is public-only. Never echo or log the key.

- **WS frames:** server replies `{type: "auth_ok", authority: bool}` to an auth frame (`false` for a bad key; the socket stays public-only). Each tick sends `{type: "tick", t, map: MapResponse, hotspots: HotspotsResponse}`, filtered like REST. A malformed or unknown inbound frame gets `{type: "error", reason}` (a fixed reason such as `"malformed frame"`, never echoing the frame's content), and the socket stays open with its current visibility.
- **Key checks:** every key comparison (ingest and authority, REST and WS) goes through `core/db.py`, is constant-time, and treats anything that isn't a non-empty string as a wrong key. It never raises, whatever the caller sends.
- **REST keys:** no `X-Authority-Key` → public data. A wrong key → `401`.

| Endpoint | Visibility |
|---|---|
| `GET /v1/apps/{app}/map?format=geojson` | public layers; all layers with the key |
| `GET /v1/apps/{app}/hotspots` | filtered per item |
| `GET /v1/apps/{app}/pois` | filtered per item |
| `GET /v1/apps/{app}/inputs/{input}/raw?since=` | authority only |
| `GET /v1/apps/{app}/trace` | authority only |
| `WS /v1/apps/{app}/stream` | filtered by key |
| `POST /v1/apps/{app}/instruction` | public |
| `/v1/apps/{app}/webhooks` | stub returning 501 |
| `GET /v1/debug/...` | `DEMO_MODE=1` only |

`POST /v1/apps/{app}/instruction` takes body `{lat, lon}` (the caller's own position). The server never looks up a position by user ID, so the endpoint can't be used to find anyone else.

## Scoring engine

**Operators** (`engine/operators/`) are pure functions `(events, params, trust, clock, grid) → layer grid` plus optional `extras` (point estimates, kind). Each declares a default `noise_floor` in its params model in `registry.py`.

| Operator | For | Computes |
|---|---|---|
| `flow_source` | tracks | Divergence over a ~20 s window as the heat layer. **Point estimate:** weighted least-squares intersection of back-extended movement lines, with robust reweighting. Reports `source` (entities moving away) vs `sink` (converging). Needs tracks, not single taps. Reference tests: ~13–29 m median error in a 300 m area with 40+ phones. |
| `density` | point events | Smoothed density with half-life decay, trust weights and duplicate removal. |
| `dwell` | tracks | Stop detection (individual within R meters for ≥ T, both from the spec) → stationary clusters. |
| `field` | gridded or station data | Sampled onto the grid. Optionally reads a **vector field** from two of the input's fields (magnitude + direction, with a spec-set direction convention `from`/`to`) and carries another layer along it (advection, optional diffusion). Domain-neutral: wind, water current or crowd drift are all just vector fields chosen by the spec; no names or conventions in the operator are specific to one domain. |
| `event_points` | geocoded documents | Confidence × source reliability, decaying over time. |
| `override` | authority fixed points | max(score, 1.0) within the radius until cleared. |

**Combining layers:**
- `norm(layer) = layer / max(p99(layer), noise_floor)`. An all-zero layer returns zeros; never produce NaN.
- `score = clamp(Σ wᵢ · norm(layerᵢ), 0, 1)`, then apply overrides.
- Trust is applied inside operators, so normalized layers don't depend on weights.
- `layer_cache.py` stores each tick's normalized stack `[L, H, W]` and override mask for the replay window.

**Candidates** (`engine/candidates/`): four generators cover apps with or without tracks. They output candidates, never labels.
- `dwell_clusters` (from `dwell`), `flow_endpoints` (from `flow_source`) and `event_clusters` (from `event_points`' located documents) read operator extras.
- `persistent_hotspots` reads the tick's hotspots, so any app gets candidates where the combined score stays high. It is skipped where an operator candidate already covers the place, and followed across ticks by position.

**Verification** (`engine/verification.py`):
- `authority_confirmation`: when an authority input arrives, how early and how close each layer's peak was.
- `predictive_check` (**leave-one-input-out**): for each input with new events in (t, t+60 s], recompute the score map with that input's layers removed, then measure the share of its new events that land in the top 10% of cells (its hit rate). The value is the **weight-share-weighted** mean of the hit rates (share = the input's weight / total weight), so inputs the others corroborate gain value as they gain weight. With equal weights it equals the plain mean. An input's events are never scored against a map that includes it, which stops crowd events from "predicting" themselves (#211).
- **Weight floor:** no flexible weight may go below 10% of its creation-time value, whoever moves it (#211).
- `cross_input_agreement`: rank correlation between layers from **different inputs**. It counts as "having data" only when at least two independent inputs have non-zero layers.

**Backtests** (`engine/backtest.py`):
- **Window:** the last min(10 min, available history), split **70/30 by time** into train and holdout. Reject with "insufficient holdout data" if the holdout has fewer than `MIN_HOLDOUT_EVENTS` (default 20).
- **Acceptance:** the highest-priority method with data (the deciding method) must improve on the **holdout**, and no other method with data may get worse on the holdout by more than ε. A candidate that makes a higher-priority method lose its data is rejected. Store both train and holdout scores.
- **Single** (`run_backtest`): used for trust changes. Re-runs the operators.
- **Batch** (`run_backtest_batch(search_spec)`), for weight changes:
  1. Input example: `{"layers": {"crowd_density": [0.6, 1.1], "flow": [0.9, 1.4]}, "samples": 256}`. Max samples is 2048.
  2. Sample candidates, renormalize the flexible weights, and **discard invalid candidates before scoring** (tier bounds, pins, locked weights).
  3. Score on the **train** segment, vectorized and in chunks of 256: `clamp(xp.einsum('kl,tlhw->kthw', W, layers), 0, 1)` → overrides → verification metrics.
  4. Take the top 5 by train score and evaluate **only those** on the holdout. Never rank on the holdout.
  5. Return the passing candidates plus stats: tested, valid, elapsed_ms, device.
  6. Backend selection: `try: import cupy as xp` / `except ImportError: import numpy as xp`. The same code path runs on CPU in tests. Report `device` honestly: the UI says "on GPU" only when it actually ran on a GPU.

**Spoof features** (`engine/trust.py`, exposed via the `get_suspicious_users` tool):
- Flag:
  - exact-duplicate coordinates across different accounts
  - impossible speed (above the input's `plausibility.max_speed_mps`, after subtracting `jitter_m`)
  - bursts of new user IDs
  - identical speeds or perfectly straight tracks, only where the input's `plausibility` says that is unnatural
  - shared device or IP, if provided
- **Never** flag sources just for being close together. Real clusters are real signal.
- **Generality rule:** nothing in `backend/` assumes what the entities are (people, animals, vehicles, sensors). Operator names, params, defaults and the descriptions the Compiler reads stay domain-neutral; domain knowledge enters only through the spec.

The engine reads time only from `core/clock.py`. Use fixed seeds in tests.

## Agents and Nemotron

- **Clients:** one OpenAI-SDK client per provider, chosen per agent from `*_MODEL` config. Agent code never hardcodes a model.
- **Rate limits:** a token bucket per provider (`NVIDIA_RPM`, `OPENROUTER_RPM`), with backoff and retries on 429/5xx.
- **Reasoning switch:** `extra_body={"chat_template_kwargs": {"enable_thinking": bool, "low_effort"?: bool}}`. When on, reasoning comes back in `message.reasoning_content`. Check on the first call that each provider honors this.
- **Inputs to the model:** never send raw event arrays. Tools return compact summaries.
- **Output validation:** agents change state only through tool calls, and every tool call's arguments are validated (Pydantic, extra keys rejected) before guards run. An agent's closing message is a free-text summary for the trace. When an agent's final answer *is* the structured output (e.g. the Scout's per-article extraction), it must validate against a Pydantic model; on failure retry once with `REPAIR_MODEL`, then log and give up.
- **Trigger cooldown:** triggered agent runs (new hotspot, override, verification drop) are at least 15 s apart per app, on top of the scheduled interval, so a moving hotspot can't burn the rate budget.

| Agent | Model | When it runs | Max iterations | Tools | Output |
|---|---|---|---|---|---|
| Compiler | COMPILER (Super, reasoning on) | app creation | 8 | `list_operators`, `validate_spec`, `create_input`, `create_connector`, `test_connector`, `request_scout`, `finalize_app` | spec in `pending_confirmation` |
| Scout | SCOUT (Super) for planning; EXTRACT (Lightning, reasoning off) for per-article extraction | **on request only** | 8 | `web_search`, `fetch_url`, `probe_feed`, `create_connector`, `test_connector`, `set_source_reliability`, `geocode` | connectors (they keep polling on their own) |
| Critic | CRITIC (Super, reasoning on) | on triggers (authority input, new hotspot, verification drop), plus at most every 60 s | 5 | `get_map_summary`, `get_layer_stats`, `get_verification_report`, `get_suspicious_users`, `run_backtest`, `run_backtest_batch`, `apply_changes`, `set_user_trust`, `annotate` | gated weight and trust changes |
| POI Labeler | POI (Lightning, low effort) | every ~60 s, or when a candidate persists 3+ ticks | 6 | `get_poi_candidates`, `find_candidates`, `osm_features_near`, `input_values_at`, `get_layer_stats_at`, `get_time_pattern`, `create_poi`, `update_poi`, `retire_poi` | POIs (from candidates only) |

- **Compiler:**
  - Picks operators from each input's `meaning`: the same GPS input becomes `density` or `flow_source` depending on what the dev says a ping means.
  - Proposes tiers, verification methods, the label set (with `sensitive`/`destination` flags), visibility and routing settings.
  - Proposes `plausibility` for every `point`/`track` input from its `meaning` (how fast and how regularly these entities really move). `validate_spec` warns when a `flexible` point/track input has no `plausibility`, and the builder shows the values for the dev to confirm, so weak spoof checks are never silent.
  - `finalize_app` sets `pending_confirmation`. The builder shows each proposed tier and visibility, and the dev confirms. Confirmation writes a `Change` with `actor=dev` and activates the spec.
  - Compiled specs are cached by a hash of (intent, inputs).
  - **The dev's words are pinned (#164):** `finalize_app` copies the dev's inputs (`id`, `kind`, `shape`, `meaning`, `fields`), intent, grid, connectors and destination points verbatim, and rejects a proposal that adds or drops an input.
  - **Verification coverage:** propose every method the inputs can feed (`predictive_check` with any weighted layer, `cross_input_agreement` with two or more weighted inputs, `authority_confirmation` first with any locked input). Cold start needs two methods with data, so `validate_spec` warns when one is left out.
  - **POI labels:** when the intent asks where something is, happens, gathers or gets stuck, propose a `label_set` for those places (mode `auto` or `on`); `off` only when the intent asks about no places.
  - The operator catalogue is sent in the task, so a compile is usually 3 model calls: `validate_spec`, `finalize_app`, closing summary.
- **Critic:** prefers `run_backtest_batch` for weight changes. It picks one passing candidate, explains why, and calls `apply_changes(backtest_id)`. Each run appends to `data/flywheel/critic.jsonl`: `{t, app_id, summary, search_spec, best_train_delta, best_holdout_delta, accepted}`. The trace shows "N configs backtested in X ms on <device>".
- **POI Labeler, `auto` mode:** if no candidate is worth labeling given the app's intent, it creates nothing and logs why. It uses the app's `label_set`. It may *propose* a new label type, which appears in the trace for the dev to accept.
- **Skipped runs:** if a provider's bucket is empty, write a `TraceEntry` with `skipped_reason: "rate budget"` and return.

## End-user instructions

1. **Routing** (`engine/routing.py`):
   - Grid A* with 8 neighbors. Step cost = `step_m × (1 + alpha × score(cell))`. Cells with score ≥ `impassable_score` are blocked.
   - **Start:** the caller's posted position.
   - **Destinations:** active POIs whose label has `destination: true`, plus `routing.destination_points`. Pick the lowest-cost reachable one.
   - If nothing is reachable, return `routing.fallback_text`.
2. **Text:** code builds the instruction from the route (direction, distance, destination label, avoid zone). It describes only what the grid knows: no stairwells, floors or room-level directions. The client recomputes on each WebSocket update.
3. **Message guard** (`tools/message_guard.py`). Every instruction passes all of these:
   - the destination exists and is not inside a high-score cell
   - the route is passable on the current grid
   - no other users' coordinates and no sensitive POI labels in the text
   - ≤ 360 characters
4. **Rephrasing:** only when `routing.rephrase` is true may `EXTRACT_MODEL` reword or translate the text. The reworded text must also pass `SAFETY_MODEL` and the checks above.
5. **On any failure,** send the code-built text instead. Log every check as a `TraceEntry`.

## Scope

- **Stubbed:**
  - webhooks (501)
  - HTML connector (the config type is rejected)
  - Scout periodic re-evaluation (off; connectors still poll)
- **Not building now** (pitch as future/production paths only):
  - cuOpt (future: dispatching responders across several shelter-in-place groups)
  - NVIDIA OpenShell as the production agent sandbox
  - fine-tuning a proposal model on `critic.jsonl`
  - Nemotron-Personas-driven simulation
  - a model usage panel

## Build order

Finish each step end to end before starting the next.

1. Contracts, core loop and console map, with a hand-written `specs/campus-alert.json` and the sim posting events.
2. Visibility and authority keys, before anything is exposed publicly.
3. Critic with the single backtest, holdout, cold start and guards; trace panel; error chart with a marker per applied `Change` (`DEMO_MODE`).
4. Compiler with dev confirmation; Scout (RSS); POI Labeler.
5. Batch backtest (CPU first); per-agent model config.
6. Routing, message guard and the end-user page.
7. Flyway spec, live compile and a short replay with the POI Labeler running live.

**Definition of done for any change:**
- `pytest` passes (no network, fixed seeds).
- No app-specific constants in `backend/`.
- Limits are enforced in tools and guards, not prompts.
- Trace and Change entries are written.
- Contracts are updated everywhere (models.py, types.ts, specs, this file).

**Required tests:**
- a sparse layer with 1 event stays under its noise-floor-scaled value; an all-zero layer returns zeros
- the flexible weight sum holds after changes, and stiff/locked weights don't move
- `apply_changes` rejects backtests whose holdout failed
- the batch path ranks on train only and discards invalid candidates before scoring; CPU results are deterministic with a fixed seed
- `predictive_check` excludes the scored input
- cold-start rejection includes a reason
- the trust increase cap is enforced
- locked and tier changes by agents are rejected; a spec isn't active without dev confirmation
- requests without the authority key never get raw events, the trace or authority-visibility POIs (REST and WebSocket)
- instructions never contain other users' coordinates or sensitive labels

## How to extend

- **Operator:** add `engine/operators/<name>.py` (pure function + params model with `noise_floor`), register it with a one-line description (the Compiler sees it via `list_operators`), and add a synthetic-scenario test.
- **Candidate generator:** add it to `engine/candidates/` and register it. Output `Candidate` only.
- **Tool:** add the schema and handler in `tools/registry.py`, put validation in `guards.py`, and give it only to the agents that need it.
- **Connector type:** extend `connectors/engine.py` with a config schema and a `test_connector` path that must parse at least one item.

## Conventions and gotchas

- Python 3.11, type hints, Pydantic v2, ruff. TypeScript strict.
- Units: meters, seconds, UTC ISO-8601.
- **Coordinate order:** GeoJSON is `[lon, lat]`; Leaflet is `[lat, lon]`. Convert only at the boundaries (`engine/grid.py`, frontend map helpers).
- **OpenRouter free endpoints:** 20 RPM and a daily request cap (50/day unless the account has ever bought ≥ $10 of credits). Failed requests can still count, so cap retries.
- **Free Nemotron 3 Ultra** doesn't enforce JSON output (no `response_format`), and its availability varies. Don't use it for the Compiler.
- Nominatim allows at most 1 request/s and requires a real User-Agent; cache geocodes. Cache Overpass by rounded (lat, lon, radius).
- Unit tests use recorded fixtures in `backend/tests/fixtures/`, never live APIs.
- The dev console has no user login. It uses the app's authority key, which is shown once at activation.
- Never print or log API keys. Never commit `.env` or `data/`.

## Demo apps

**Rule 13: we code the platform, never the demos.** The demos (planned: a wildfire app and a bird-tracking app) are scripted and built by **humans after the platform is finished**, by using it. That is also the test of whether the platform works. Nothing in this repo scripts, configures or rehearses them: no demo runbook, no demo launcher, no demo specs. `specs/*.json` are test fixtures only. Data simulators in `sim/` only post through the public ingest API.

Simulator ground truth stays in `sim/ground_truth/` and is read only by `api/debug.py` when `DEMO_MODE=1`.

## Decisions log

Only the lead (`@ramgudur`) edits this section. Newest last. If a board thread disagrees with an entry here, this log wins; raise it with `NEEDS-LEAD`.

- **2026-11-07:** Areas assigned up front (table in AGENTS.md section 4). Kida seat b owns the scoring model as physics-informed / physics-based ML. Allen owns token-heavy work (scaffold, frontend, sim). The lead's machine only routes and checks scope.
- **2026-11-07:** Escalation rules adopted (AGENTS.md section 5). `CLAUDE.md` is the single source of truth for the idea.
- **2026-11-07:** Contracts (`core/models.py`, `types.ts`, `specs/`) change only with lead approval. Allen writes the first draft in the scaffold.
- **2026-11-07:** The lead's `flowsource.py` lives at `reference/flowsource.py` as a reference only (its `simulate()` holds demo values, so it stays out of `backend/`). Kida seat b ports it into `engine/operators/flow_source.py`.
- **2026-11-07:** Line endings are LF for everyone via `.gitattributes`.
- **2026-11-07:** Operator signature is `(events, params, trust, clock, grid)` (adds `grid`, PR #11). Grid and clock interfaces are agreed between Kida's seats: grid exposes `cell_m`, shape `(H, W)` and `to_xy`/`to_latlon` in local meters from the SW corner; `clock.now()` returns float seconds.
- **2026-11-07:** Contracts in PR #14 approved and merged; the Core contracts section reflects them.
- **2026-11-07:** Output contracts set: one combined `Hotspot` (Allen's map fields + Kida A's tick stats), `MapResponse` with a single `value` per cell and near-zero cells omitted, `HotspotsResponse`. Navmi adds them to `models.py` in #9; Allen mirrors them in `types.ts` (no separate `outputs.ts`); `tick.py` emits the `Hotspot` model.
- **2026-11-07:** Campus Alert: classroom buttons use `override`, like the police marker (rule 6: authority overrides always win).
- **2026-11-07:** Campus Alert runs on a generic, non-school location, not a real recognizable campus.
- **2026-11-07:** The engine `Grid` takes `bbox` as a tuple in GeoJSON order `(min_lon, min_lat, max_lon, max_lat)` (PR #12). The `Tier` type is defined once, in `core/models.py`; `engine/tiers.py` imports it and does not define its own.
- **2026-11-07:** WebSocket auth is a first-frame `{type: "auth", key}` message (option a); public-only until a valid key arrives.
- **2026-11-07:** Backtest ε applies to every non-deciding method with data (lower priority); a candidate that loses a higher-priority method's data is rejected (PR #31).
- **2026-11-07:** WS frames `auth_ok` / `tick` approved as proposed by Allen; REST with a wrong authority key returns 401, no key returns public data.
- **2026-11-07:** Kida seat b files and takes `trust.py` spoof features (step 3) next, then the batch backtest (step 5).
- **2026-11-07:** Trust increases need a passing backtest; `set_user_trust` only lowers. Stiff weight changes pass only when `authority_confirmation` is the deciding method. Storing tested candidates in `Backtest` is declined for now (after a restart the Critic re-runs the backtest). (PR #39)
- **2026-11-07:** The `field` operator is a neutral vector field, not wind: params like `vector_speed_field` / `vector_dir_field` / `vector_dir_convention` (`from` | `to`) replace the `wind_*` names, and docs say "along the vector field", not "downwind". Wind is one use, set by the spec. Rule: operators never name a domain (wind, birds, police); the spec does.
- **2026-11-07:** Generalization pass ("one size fits all"): spoof limits move into `inputs[].plausibility` (Compiler proposes them); operator descriptions read by the Compiler become domain-neutral; `outputs.hotspot_threshold` replaces the fixed 0.5; POI tool `weather_at` becomes `input_values_at`; `open_meteo` becomes a `json_api` preset, not a connector type. Contract changes: `plausibility`, `hotspot_threshold`, `connector.preset`.
- **2026-11-07:** Hotspot visibility: a hotspot is `public` only if `contributing_layers` is non-empty and every one of them is public in `spec.outputs.layers`; otherwise `authority`. Set in `tick.py` (Kida A), filtered in `outputs.py`.
- **2026-11-07:** `specs/campus-alert.json`: the `panic` input carries its own on-foot `plausibility` (`max_speed_mps: 12`, `flag_constant_speed: true`, `flag_straight_line: true`), so the demo keeps flagging spoofers after #45 makes the platform defaults neutral (Allen).
- **2026-11-07:** Spoof defaults are neutral: with no `plausibility`, no speed limit and no constant-speed/straight-line checks; entity-agnostic checks still run. Guard against silent weak detection: the Compiler proposes `plausibility` for every point/track input, `validate_spec` warns when a flexible point/track input lacks it, and the builder shows it at confirmation. Campus Alert pins its own values (#62).
- **2026-11-07:** WS: malformed or unknown frames get `{type: "error", reason}` with a fixed reason, and the socket stays open. Key checks never raise: non-string, empty or non-ASCII keys are just wrong keys (fixed once in `db.py`). Hotspots stay authority-only until #67 lands the contributor fix (rule 8 fail-safe).
- **2026-11-07:** `flow_source` extras gain `uncertainty_m` (1-sigma semi-major axis of the point estimate, from the WLS covariance; calibration checked in the #84 benchmark). Internal extras, not a `models.py` contract. Debug API shapes in PR #90 approved (DEMO_MODE only).
- **2026-11-07:** Agent output validation: state changes only via validated tool calls; a structured final answer (e.g. Scout extraction) still validates with one repair retry; closing messages are free text (PR #92). Triggered runs are at least 15 s apart per app.
- **2026-11-07:** Console contracts approved (PR #98): authority-only `GET /trace`, `/changes`, `/weights` (`WeightsView`), `/verification` (`VerificationView`), and `POST /weights/{layer_id}/pin` (`PinRequest`, via the `pin_weight` tool as dev).
- **2026-11-07:** Neutrality scope: platform logic and Compiler-facing text stay neutral; presets may name real sources (data, not logic); specs, sims and scenario tests use scenario words. `score` may be public only if every weighted layer is public; `validate_spec` enforces it. Flyway (PR #99) approved with `score` authority-only.
- **2026-11-08:** Routing and message guard (PR #109) approved: one Dijkstra search from the caller to the lowest-cost usable destination (equivalent to A* here, with several targets); destinations must score below min(`impassable_score`, `hotspot_threshold`). POI tools and guards (PR #111) approved. A POI whose label is a `destination` label is `public` (the dev made it a routing target, so the public route reveals it anyway; destination labels can never be sensitive); other POIs keep the hotspot visibility rule.
- **2026-11-08:** `specs/campus-alert.json`: `local_news` declares optional `doc_id` (string) and `confidence` (number) fields, so RSS connector events pass ingest field validation (found by Navmi in #112). Connector polling stamps time from `core/clock.py`, not wall time; the Compiler cache-hit path goes through `finalize_app` (never overwrites an active app) and writes a TraceEntry.
- **2026-11-08:** Allen is back. He owns `frontend/`, `sim/`, `specs/`, fixtures, `api/debug.py`, `api/trace.py` and the end-user path: #107 (`api/instructions.py` + `EndUser.tsx`) and #108 (step-7 live path), moved from the lead's machine, which goes back to routing and review only. #86 (CuPy check) moves from seat b to seat a, so seat b can finish #102 and #106.
- **2026-11-08:** GPU fact for the demo: only Ram's laptop has an NVIDIA GPU (RTX 3050 6GB, CUDA 12.5); Kida A, Kida B and Allen are CPU-only. CuPy is optional and never in `requirements.txt`; on a GPU box install `pip install "cupy-cuda12x[ctk]"` (plain `cupy-cuda12x` fails without CUDA headers). The batch backtest reports `device` honestly, so a live "on GPU" moment needs the demo backend running on Ram's laptop; anywhere else the demo says CPU. #86 (Ram) verifies CPU/GPU equivalence and records timings for the pitch.
- **2026-11-08:** Nemotron runs on build.nvidia.com (NVIDIA API catalog) with the lead's key; Brev and OpenRouter are optional. Live check on that account: `nemotron-3-super-120b-a12b`, `nemotron-nano-3-30b-a3b` and `nemotron-3.5-content-safety` are listed but return 404 (not enabled for the account). Working: `nemotron-3.5-super-vl-120b-a12b` (Compiler/Critic/Scout: tool calls, `enable_thinking` honored), `nemotron-3.5-lightning-30b-a3b` (Extract/POI/Repair: tools, `low_effort`, JSON mode), `llama-3.1-nemotron-safety-guard-8b-v3` (Safety; it flagged a plain route instruction as "unsafe", so tune its prompt before rephrasing goes live). All agents now use the `nvidia` provider, so there is no OpenRouter daily cap. Defaults updated in `config.py`, `.env.example` and the Environment section.
- **2026-11-08:** Compile API approved (PR #127): `POST /v1/apps/{app}/compile` with `CompileRequest {intent, grid, inputs: CompileInput[]}` (`CompileInput` = `{id, kind, shape, meaning, fields}`: no tier, operators or plausibility, which the Compiler proposes); returns the `AppSpec` at `pending_confirmation`; `409` if active, `422` if the Compiler never finalizes, `503` on rate budget. `/activate` stays the only dev confirmation. All agents share one `LlmRouter` (one rate budget). Like create/activate, compile is unauthenticated for now (the console has no login).
- **2026-11-08:** Navmi finishes #127 and her pending reviews, then takes no new tasks. Her areas move: `api/`, `main.py`, `core/db.py`/`config.py`/`access.py` and `connectors/` to Allen; `agents/` and `tools/compiler_tools.py` to Kida seat b. New work goes mostly to Allen and Kida seat b. Scout (#105) moves to seat b.
- **2026-11-08:** Grid cap: `GridSpec` rejects grids over 250,000 cells (H x W, e.g. 500 x 500). It is a resource limit, so domain-neutral (Allen, with #132). Scout tools split: seat a builds `web_search`, `fetch_url`, `probe_feed`, `geocode` and `set_source_reliability` in `tools/` (scout only); seat b owns `agents/scout.py` and, by Allen's handoff, the RSS no-georss path (extract, then geocode, then ingest) in `connectors/`. Instructions API (#130) approved for rule 8.
- **2026-11-08:** All new tasks go to Allen; Kida A, Kida B and Navmi take no new tasks (usage limits). Open issues #105, #137, #86 and #108 moved to Allen, who also took over #124 and #130 merges. Lead-level reviews (rules 1, 7, 8, 10, contracts, external calls) stay with Ram's machine, which also reviews any main.py change Allen makes to work he took over.
- **2026-11-08:** Pitch and demo, for the 12:00 PM CT deadline. The story is the platform: anyone can build an app that protects their local community on its "worst day" and customize every input (State Farm framing). Campus Alert is one such app, built live through the Builder; catastrophe-scale statistics set the stakes, with sources cited. There is no GPU in the pitch: Nemotron runs on build.nvidia.com, and #86 part 3 (CuPy speed-up) is dropped as future work. Allen's order: #138 fix, then ScoutRunner, then #139, then a real-phone check, then the video; a light #108 (Flyway compile, cached if slow); the `api/webhooks.py` 501 stub. If the Scout is not live by 10:00 AM CT, the pitch says three agents run live and the Scout runs on request.
- **2026-11-08:** Demo apps are built LIVE through the platform in the video (Builder: intent and inputs, then live Nemotron compile, review, confirm, keys). No pre-built demo apps, no demo compile cache, no replayed fixtures. `specs/*.json` stay as test fixtures and an emergency fallback only. The simulators stand in for a dev's own app and users, posting through the public ingest API with the keys the Builder showed. #108 is now the live build path and rehearsal (Allen). This replaces the cached-compile plan in the entry before.
- **2026-11-08:** `CompileRequest` gets optional `destination_points: DestinationPoint[]` (the dev picks fixed destinations on the Builder map). The Compiler copies them verbatim into `routing.destination_points` and never invents or moves them; they count as a usable destination for `validate_spec`. The sim posts to the input ids the dev types in the Builder (`--input-map` as a backup); `tools/demo` is Builder-first, with `--from-spec` as the emergency fallback (Allen, #108).
- **2026-11-08:** Demo apps: **Wildfire** replaces Campus Alert, and Bird tracking stays. Both are built live through the platform in the video, and neither is hardcoded; gaps found while building them are fixed in the platform. **Add a source** is essential: the dev describes a source in plain words, a model call infers push or pull, the shape and the source options **before** any technical choice is shown (`POST /v1/apps/{app}/sources/suggest`, `SourceSuggestion`, #152). Options now: POST API or webhook URL (push); RSS, or a JSON API preset or custom URL (pull). Receiving by phone number (SMS) comes later; the HTML scraper stays out (rule 10). All work goes to Allen and Ram; the others come back later. Issues: #152 Add a source (Allen), #153 wildfire sim (Allen), #154 live build rehearsals (Ram's machine).
- **2026-11-08:** Rule 13 added: we code the platform, never the demos. Wildfire and Bird tracking are built in the video by using the platform. No demo specs, demo-specific code, cached compiles or rehearsal builds ahead of time; test with neutral examples; fix needs as general platform features. #154 (rehearsals) is closed.
- **2026-11-08:** Rule 13 clarified: data simulators in `sim/` are allowed. They only post through the public ingest API and never create or configure the app; #153 continues. The next platform work on Ram's machine is POI candidates: apps without tracks currently get no candidates, so register general generators for them.
- **2026-11-08:** Demo scripting is a human task after the platform is finished; it doubles as the test of the platform. Removed `tools/demo` and `docs/DEMO.md`, and cut the per-demo plans from the Demo apps section. #108 closed; #153 (wildfire sim) on hold until humans script the demos. `tools/phone_check.py` stays: it checks the platform's end-user page at phone width.
- **2026-11-08:** POI candidates for every app (#160): `persistent_hotspots` (from the tick's hotspots) and `event_clusters` (from `event_points` documents, which now carry a capped `docs` list of positions in their extras) are registered. An app without tracks used to get no candidates, so the POI Labeler could name nothing. Hotspot candidates are skipped where an operator candidate already covers the place, and are tracked by position. Scope updated.
- **2026-11-08:** The demo and Devpost (video, write-up, judging pitch) belong to the humans, after the platform works. Agents work on the platform only and don't raise demo or Devpost questions. Remaining platform work: #152 Add a source (Allen); Compiler reliability (Ram's machine; tested with neutral examples); edit-before-confirm, since the review screen can only recompile (Allen, after #152).
- **2026-11-08:** Contracts approved: `POST /sources/suggest` (`SourceSuggestion`, #165), `CompileInput.connector` (pull only, copied verbatim, URL-guarded, #165), and `PUT /v1/apps/{app}/spec` with Change kind `spec_edit` (#166). They are written into Core contracts.
- **2026-11-08:** Navmi owns `frontend/` alone (including `types.ts`), working on her own. Nobody else edits it, and agents don't tag her. She mirrors contracts from Core contracts. Allen's frontend items are dropped from his queue: the Builder edit UI in #166 (backend `PUT /spec` stays his), #167 (Builder progress) and #170 (console). Kida A and Kida B may resume platform tasks when they ask.
- **2026-11-08:** Compiler reliability (#164), measured live on neutral apps:
  - Before the fix: 9 of 9 finalized in 4 calls, median 40 s, but verification often left out methods (so the Critic could never act) and POI labels were `off` for "where" intents.
  - After the fix: 12 of 12 finalized in 3 calls, labels named from the intent in 12 of 12, two or more methods wherever the inputs allow.
  - Latency is dominated by the provider (27–108 s); `low_effort` didn't help, so reasoning stays on.
  - The dev's inputs, intent and grid are pinned at finalize.
  - Add a source (#189) checked live: 16 of 16 correct, 1 repair (was 9), 0 fallbacks.
- **2026-11-08:** A dev revert (#193) is exempt from the agent rate caps (trust +0.1 per 5 min, stiff ±10% per hour), so undoing a wrong agent change is one step (rule 11). Hard bounds stay: locked never moves, pins hold, the flexible sum holds, trust stays in [0,1], a stale target gives 409.
- **2026-11-08:** Kida A and Kida B are back on platform tasks: #191 (stiff cap leak) for Kida A, #194 (broad field layers flood the score) for Kida B.
- **2026-11-08:** Navmi is paused, so Allen takes over `frontend/` from her branch `frontend-aquatic-rebuild`, following `frontend/PLAN.md` and `frontend/HANDOFF.md`. He asks his human for the original design image before building visuals, to keep Navmi's vision. Allen's backend issues move: #190 (label proposals) and #169 (hardening) to Kida A; #181 (land PR #187) and #183 (dev-requested Scout) to Kida B. #153 stays on hold, unassigned.
- **2026-11-08:** Allen owns all of `frontend/` with full authority: design, new dependencies, deleting files and merging, with no lead approval needed for frontend-only changes. Backend contracts, API routes and env vars still go through the lead.
- **2026-11-08:** Live checks of Nemotron agents on neutral apps:
  - The Critic ran no backtest before deciding. Its prompt now runs a batch backtest once out of cold start (live: 256 configs tested).
  - The POI Labeler ran out of steps calling one tool at a time. It now batches evidence calls (live: named "problem spot").
  - The Compiler gives a single-location field weight 0, or uses it as a vector field (live: 4 of 4).
  - Dev reverts keep the flexible ±25% per-step limit (Ram: devs rarely set weights by hand).
  - Core contracts now cover compile status/failure, revert, and label proposals.
- **2026-11-08:** How weights work (Ram):
  - The Compiler (Nemotron) picks the factors (operators) and their starting weights.
  - The scoring engine combines them in code.
  - The Critic (Nemotron) corrects the weights, gated by held-out backtests.
- **2026-11-08:** #211: `predictive_check` is weight-share-weighted, so crowd-only apps can tune their weights. A flexible weight never goes below 10% of its creation-time value. Also approved:
  - `COMPILE_MIN_GAP_S`, an optional env var, and `/compile` returning 429 (#212).
  - The Scout contract (#209).
  - Kida A takes data retention.
