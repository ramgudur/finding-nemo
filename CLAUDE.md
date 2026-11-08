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
3. Gives end users safe-route **instructions**.
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
3. **Platform code is app-agnostic.** No app-specific IDs, coordinates, label names, thresholds or "if campus/birds" branches in `backend/`. App-specific things live in the `AppSpec`. Demo material lives in `sim/` and `specs/`.
4. **Locked stays locked.** The Compiler only *proposes* tiers, verification methods and their priority. They take effect only after the dev confirms the spec. After that, nothing an agent does can change verification methods, input tiers or locked weights.
5. **Tier and trust bounds** (enforced in `engine/tiers.py`, `engine/trust.py` and `tools/guards.py`):

   | Tier | Used for | Weight changes allowed |
   |---|---|---|
   | `locked` | authority inputs | never |
   | `stiff` | news, official sensors | at most ±10% per hour, and only with authority-confirmation evidence |
   | `flexible` | crowd inputs | at most ±25% per step. Flexible weights are renormalized so their sum stays at the value set at creation. Stiff and locked weights are never moved by renormalization. |

   - **Trust:** agents may lower a user's trust freely (with a reason). Increases are capped at **+0.1 per user per 5 minutes** of Clock time.
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
      candidates/           registry.py, dwell_clusters.py, flow_endpoints.py
                            (persistent_hotspots.py and event_clusters.py stay on disk but are UNREGISTERED)
    agents/
      llm.py                OpenAI-compatible clients per provider, per-provider token buckets, retry/backoff
      loop.py               generic tool-calling loop; writes TraceEntries; caps iterations
      compiler.py, scout.py, critic.py, poi_labeler.py
      prompts/              one prompt file per agent
    tools/                  registry.py (JSON schemas), guards.py, scoring_tools.py, poi_tools.py,
                            connector_tools.py, geo_tools.py (Nominatim, Overpass, Open-Meteo),
                            search_tools.py (Tavily), message_guard.py
    connectors/             engine.py (runs configs), rss.py, json_api.py, open_meteo.py
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
COMPILER_MODEL=nvidia::nvidia/nemotron-3-super-120b-a12b
CRITIC_MODEL=nvidia::nvidia/nemotron-3-super-120b-a12b
SCOUT_MODEL=nvidia::nvidia/nemotron-3-super-120b-a12b
EXTRACT_MODEL=openrouter::nvidia/nemotron-3.5-lightning:free
POI_MODEL=openrouter::nvidia/nemotron-3.5-lightning:free
REPAIR_MODEL=openrouter::nvidia/nemotron-3.5-lightning:free
SAFETY_MODEL=openrouter::nvidia/nemotron-3.5-content-safety:free   # used only if instruction rephrasing is on

NVIDIA_RPM=35              # free tier is ~40 RPM, shared across all models
OPENROUTER_RPM=18          # free models: 20 RPM; daily cap of 50 unless the account has bought >= $10 of credits ever
TAVILY_API_KEY=
NOMINATIM_USER_AGENT=      # required by Nominatim policy; generic app name + team contact
SQLITE_PATH=./data/app.db
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
  - `connector?`: `{type: rss|json_api|open_meteo, url?, poll_s, config{}}`. Push inputs may not have one.
  - `operators[]`: `{op, weight, noise_floor?, layer_id?, ...params}`. `layer_id` defaults to `<input_id>.<op>`; `OperatorConfig.params()` returns what `registry.run` takes.
- `verification[]`: `{method, priority}`. Priority 1 is highest; methods and priorities are unique.
- `outputs.layers[]`: `{id, visibility}`. `id` is a layer id or `score`. Visibility defaults to `authority` everywhere.
- All datetimes are timezone-aware and normalized to UTC (`UtcDatetime`).
- `outputs.poi_labels`: `{mode: auto|on|off, label_set: [{label, sensitive, destination}], min_confidence}`. A `sensitive` label is authority-only. A `destination` label is a routing target and must not also be `sensitive`.
- `routing`: `{enabled, destination_points[{lat, lon, label}], impassable_score, alpha, fallback_text (≤ 360 chars), rephrase: false}`

**Event (push):** `POST /v1/apps/{app}/inputs/{input}/events`
- Header `X-Ingest-Key`; body `{user_id?, lat, lon, ts, ...input fields}`.
- Validated against the input's `fields`. Rejected unless the app is `active`.

**Other contracts:**
- **Candidate:** `{id, lat, lon, radius_m, source, stats, contributing_layers, persisted_ticks}`
- **POI:** `{id, candidate_id, lat, lon, radius_m, label, confidence, evidence[], visibility, status, created_at, updated_at}`
- **Backtest:** `{id, kind: single|batch, candidates_tested, candidates_valid, train_scores, holdout_scores, passed, device: cpu|gpu, elapsed_ms, t}`
- **TraceEntry:** `{t, app_id, agent, model, saw, hypothesis, tested, result, action, why, tool_calls[], skipped_reason?}`
- **Change:** `{id, app_id, kind: weight|trust|poi|connector|spec_confirm, target, before, after, reason, backtest_id?, actor: agent|dev, t}`
- **Instruction:** `{text, destination{lat, lon, label}, route[[lat, lon]], distance_m, computed_at, guard{passed, checks[]}}`
- **Hotspot:** `{id, lat, lon, radius_m, score, mean, area_m2, n_cells, persisted_ticks, contributing_layers[], visibility}`. `score` is the peak cell score; `radius_m = sqrt(area_m2 / π)`. The tick produces it; `/hotspots` and the Critic/POI tools read it.
- **MapResponse** (`GET /map?format=geojson`): `{app_id, t, cell_m, bbox: [min_lon, min_lat, max_lon, max_lat], layers: {<layer_id> | "score": FeatureCollection}}`. One Polygon feature per cell with `properties: {value, row, col}` (a single `value` field). Cells with `value < 0.01` are omitted to keep the payload small. Without the authority key, only `public` layers are included.
- **HotspotsResponse** (`GET /hotspots`): `{app_id, t, hotspots: Hotspot[]}`, filtered per item by `visibility`.

`Hotspot`, `MapResponse` and `HotspotsResponse` are Pydantic models in `core/models.py`, mirrored in `types.ts`, like every other contract.

**Outputs.** Without the `X-Authority-Key` header, responses contain only `public` items. This applies to REST and WebSocket alike. Browsers can't set WebSocket headers, so the WS client sends `{type: "auth", key}` as its first frame; until a valid key arrives the stream is public-only. Never echo or log the key.

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
| `flow_source` | tracks | Divergence over a ~20 s window as the heat layer. **Point estimate:** weighted least-squares intersection of back-extended movement lines, with robust reweighting. Reports `source` (people leaving) vs `sink` (converging). Needs tracks, not single taps. Reference tests: ~13–29 m median error in a 300 m area with 40+ phones. |
| `density` | point events | Smoothed density with half-life decay, trust weights and duplicate removal. |
| `dwell` | tracks | Stop detection (individual within R meters for ≥ T, both from the spec) → stationary clusters. |
| `field` | gridded data | Sampled onto the grid; can optionally shift another layer downwind. |
| `event_points` | geocoded documents | Confidence × source reliability, decaying over time. |
| `override` | authority fixed points | max(score, 1.0) within the radius until cleared. |

**Combining layers:**
- `norm(layer) = layer / max(p99(layer), noise_floor)`. An all-zero layer returns zeros; never produce NaN.
- `score = clamp(Σ wᵢ · norm(layerᵢ), 0, 1)`, then apply overrides.
- Trust is applied inside operators, so normalized layers don't depend on weights.
- `layer_cache.py` stores each tick's normalized stack `[L, H, W]` and override mask for the replay window.

**Candidates** (`engine/candidates/`): only `dwell_clusters` and `flow_endpoints` are registered. They output candidates, never labels.

**Verification** (`engine/verification.py`):
- `authority_confirmation`: when an authority input arrives, how early and how close each layer's peak was.
- `predictive_check` (**leave-one-input-out**): for each input with new events in (t, t+60 s], recompute the score map with that input's layers removed, then measure the share of its new events that land in the top 10% of cells. Average across inputs. This stops crowd events from "predicting" themselves.
- `cross_input_agreement`: rank correlation between layers from **different inputs**. It counts as "having data" only when at least two independent inputs have non-zero layers.

**Backtests** (`engine/backtest.py`):
- **Window:** the last min(10 min, available history), split **70/30 by time** into train and holdout. Reject with "insufficient holdout data" if the holdout has fewer than `MIN_HOLDOUT_EVENTS` (default 20).
- **Acceptance:** the highest-priority method with data must improve on the **holdout**, and no higher-priority method may get worse by more than ε. Store both train and holdout scores.
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
  - impossible speed (> ~12 m/s on foot)
  - bursts of new user IDs
  - identical speeds or perfectly straight tracks
  - shared device or IP, if provided
- **Never** flag users just for being close together.

The engine reads time only from `core/clock.py`. Use fixed seeds in tests.

## Agents and Nemotron

- **Clients:** one OpenAI-SDK client per provider, chosen per agent from `*_MODEL` config. Agent code never hardcodes a model.
- **Rate limits:** a token bucket per provider (`NVIDIA_RPM`, `OPENROUTER_RPM`), with backoff and retries on 429/5xx.
- **Reasoning switch:** `extra_body={"chat_template_kwargs": {"enable_thinking": bool, "low_effort"?: bool}}`. When on, reasoning comes back in `message.reasoning_content`. Check on the first call that each provider honors this.
- **Inputs to the model:** never send raw event arrays. Tools return compact summaries.
- **Output validation:** every agent output must validate against a Pydantic model. On failure, retry once with `REPAIR_MODEL`, then log and give up.

| Agent | Model | When it runs | Max iterations | Tools | Output |
|---|---|---|---|---|---|
| Compiler | COMPILER (Super, reasoning on) | app creation | 8 | `list_operators`, `validate_spec`, `create_input`, `create_connector`, `test_connector`, `request_scout`, `finalize_app` | spec in `pending_confirmation` |
| Scout | SCOUT (Super) for planning; EXTRACT (Lightning, reasoning off) for per-article extraction | **on request only** | 8 | `web_search`, `fetch_url`, `probe_feed`, `create_connector`, `test_connector`, `set_source_reliability`, `geocode` | connectors (they keep polling on their own) |
| Critic | CRITIC (Super, reasoning on) | on triggers (authority input, new hotspot, verification drop), plus at most every 60 s | 5 | `get_map_summary`, `get_layer_stats`, `get_verification_report`, `get_suspicious_users`, `run_backtest`, `run_backtest_batch`, `apply_changes`, `set_user_trust`, `annotate` | gated weight and trust changes |
| POI Labeler | POI (Lightning, low effort) | every ~60 s, or when a candidate persists 3+ ticks | 6 | `get_poi_candidates`, `find_candidates`, `osm_features_near`, `weather_at`, `get_layer_stats_at`, `get_time_pattern`, `create_poi`, `update_poi`, `retire_poi` | POIs (from candidates only) |

- **Compiler:**
  - Picks operators from each input's `meaning`: the same GPS input becomes `density` or `flow_source` depending on what the dev says a ping means.
  - Proposes tiers, verification methods, the label set (with `sensitive`/`destination` flags), visibility and routing settings.
  - `finalize_app` sets `pending_confirmation`. The builder shows each proposed tier and visibility, and the dev confirms. Confirmation writes a `Change` with `actor=dev` and activates the spec.
  - Compiled specs are cached by a hash of (intent, inputs).
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
  - `persistent_hotspots` and `event_clusters` (unregistered)
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

## Demo apps (context only; never hardcode them into the platform)

Demo apps are built *through* the platform from `specs/` and driven by `sim/`, which posts through the public API.

- **Campus Alert** (public safety; State Farm "worst day" angle):
  - **Inputs:** panic button (press → GPS stream; flexible), classroom buttons and police marker (locked authority; both use the `override` operator, rule 6), local news (Scout RSS; stiff).
  - **Location:** a generic, non-school area (e.g., a business park or fairground). Not a real, recognizable school campus.
  - **Labels:** threat origin, gathering point / exit (`destination`), shelter-in-place group (`sensitive`), suspected spoof cluster (`sensitive`).
  - **Sim timeline:**
    - t0: threat appears; presses start GPS streams
    - hiders form groups inside rooms
    - t+60 s: room button
    - t+100 s: spoof burst (coordinated fake escape + exact-duplicate stationary accounts)
    - t+150 s: police marker
    - t+180 s: scripted news article through the RSS connector (labeled as scripted)
    - the threat keeps moving throughout
  - **End-user page:** must work on a phone over a tunnel; test that before judging.
- **Flyway** (wildlife):
  - **Inputs:** tag GPS tracks, Open-Meteo wind.
  - **Labels:** watering hole, roost, feeding ground, stopover, grounded in OSM features near real Dallas-area lakes.
  - **Demo:** live compile (the same GPS input becomes a different operator) plus a short replay with the POI Labeler running live.

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
- **2026-11-07:** The engine `Grid` takes `bbox` as a tuple in GeoJSON order `(min_lon, min_lat, max_lon, max_lat)` (PR #12). The `Tier` type is defined once, in `core/models.py`; `engine/tiers.py` imports it and does not define its own.- **2026-11-07:** WebSocket auth is a first-frame `{type: "auth", key}` message (option a); public-only until a valid key arrives.
