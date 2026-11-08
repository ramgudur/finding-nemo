# Finding Nemo — Frontend Rebuild

## Context

The hackathon team (`ramgudur/finding-nemo`) has a working backend (FastAPI, real Compiler/Scout/Critic/POI-Labeler agents on Nemotron) and a functional-but-undesigned frontend: three plain pages (`Builder.tsx`, `AppPage.tsx`, `EndUser.tsx`) wired to real endpoints, styled entirely with inline `style={{}}` objects and no visual identity. The team lead (`@ramgudur`) wants a demo-ready product; `frontend/` was previously `@AllenZheng-05`'s area under `AGENTS.md`, but per direct instruction from my human (Navmi) I now own it.

The ask: rebuild the frontend into an immersive "Neo-Ghibli / cozy web" aquatic identity — a genuine animated Three.js koi pond landing page, then a redesigned source-configuration flow, then a technical "architecture inspector" page — while **reusing every real backend contract already wired** (`/compile`, `/sources/suggest`, `/activate`, `/map`, `/hotspots`, `/pois`, `/trace`, `/weights`, `/verification`, `/instruction` for the untouched end-user page). Zero backend changes. This plan is frontend-only, verified against the actual current code on `origin/main` (not the possibly-aspirational text in `CLAUDE.md`).

**Decisions already made with the user** (not open questions):
- Frontend ownership: mine now. I'll post a transparent heads-up on the team board (not a blocking ask — the user authorized this directly) before starting, since `@AllenZheng-05` has 4 open issues (#166–#169) actively touching `Builder.tsx`/`AppPage.tsx` right now, and whoever next touches those files should know they're being replaced.
- Dependencies: add the full stack the user's spec asked for (no trimming).
- Sources page: full rebuild of the UX (overrides Allen's existing inline flow), but must call the same real `/sources/suggest` and `/compile` endpoints — no invented contracts.
- Architecture page: no separate static "review" page. Land directly on one page that shows the proposed spec right after compiling, has the real "Confirm & Activate" action embedded in it, and the same page continues to show live data post-activation. "Go Back to Editing" is the escape hatch back to Sources.
- Design direction: the user's own spec (Part 3) is the art-direction brief — exact color tokens, font pairing, texture rules — plus a reference screenshot (recreate its composition as real animated 3D/HTML elements, never a flattened background image).

## Current real state (verified against `origin/main`, not assumed)

- **Stack**: React 18.3.1, Vite 5.4.11, TS 5.6.3 (strict), Leaflet 1.9.4. Nothing else — no router, no CSS framework, no animation lib, no 3D lib, no state lib. All styling is ad-hoc inline hex. `vite.config.ts` already proxies `/v1` → `:8000` (incl. websockets).
- **Pages**: `Builder.tsx` (685 lines — app-id/intent/area/grid form, inline per-input "suggest a source" picker wired to real `/sources/suggest`, compile, inline review, confirm/activate showing keys once), `AppPage.tsx` (183 lines — authority-key-gated live console: WS stream + REST fallback, Map/LayerToggles/hotspots/ConsolePanels/DemoTools), `EndUser.tsx` (166 lines — public mobile routing page, **out of scope, left untouched**).
- **`api/client.ts`**: already has `map`, `hotspots`, `compile`, `activate`, `suggestSource`, `instruction`, `trace`, `changes`, `weights`, `verification`, `pin`, debug-mode calls. No `getApp` wrapper exists yet for `GET /v1/apps/{app}` (route exists, just unwrapped) — needs adding. No `pois`/`raw` wrappers either (routes exist per recent PR #173) but not required for this plan's scope.
- **`api/authorityKey.ts`**: guarded session/localStorage key persistence per app — the pattern to extend for "Past Projects," not replace.
- **Real contracts** (`core/models.py` / `types.ts`, confirmed current): `CompileRequest{intent, grid, inputs: CompileInput[], destination_points?}`, `CompileInput{id, kind, shape, meaning, fields[], connector?}` (connector pull-only, 422 on push), `SuggestSourceRequest{intent, description}` → `SourceSuggestion{kind, shape, options: SourceOption[], meaning}` (`SourceOption{type: "post_api"|"webhook"|"rss"|"json_api", label, why, config?}`, always 200), `AppSpec{app_id, intent, status, grid, inputs: InputSpec[], verification[], outputs, routing}` with `InputSpec.tier: "locked"|"stiff"|"flexible"`. `MapResponse`/`HotspotsResponse` only carry public layers without `X-Authority-Key`; a wrong key is a real 401. **No `GET /v1/apps` list-all-apps endpoint exists** — "Past Projects" must be client-side only.

## Dependencies to add (`frontend/package.json`)

| package | version | why |
|---|---|---|
| `react-router-dom` | ^6.28 | replaces the hand-rolled pathname-regex router; v6 (not v7) — no data-router APIs needed |
| `zustand` | ^5.0 | shared draft state across Sources↔Architecture nav (routing is now multi-page) |
| `tailwindcss` ^3.4, `postcss` ^8.4, `autoprefixer` ^10.4 | v3 (not v4) — this project's ~12 named colors + 3 font families theme is better served by `tailwind.config.js` than v4's CSS-first config |
| `three` ^0.169, `@react-three/fiber` ^8.17, `@react-three/drei` ^9.114, `@react-three/postprocessing` ^2.16 | the pond. **Fiber must stay on v8** — v9 requires React 19, this project is pinned to 18.3.1. Don't add `@types/three` (bundled since ~r150). |
| `motion` ^11.11 | current npm name for what was `framer-motion`; import from `motion/react` |
| `lucide-react` ^0.460 | icons |

**Not adding**: `gsap` (every animation need — page transitions, panel stagger, SVG wire draw-in — is expressible in `motion`; revisit only if Phase 4 proves a specific timeline awkward). `react-leaflet` (the existing imperative `Map.tsx` is correct, just needs restyling — wrapping it now is a rewrite with no gain). Any component framework (conflicts with the bespoke identity).

## Tailwind setup

- `frontend/tailwind.config.js` (new, ESM since `package.json` has `"type": "module"`): `content: ["./index.html", "./src/**/*.{ts,tsx}"]`; `theme.extend.colors` = the user's exact palette (`mist #DCEEF1, paleWater #BBDCE3, washedSky #D5E8EB, softAqua #A9D2D5, sage #A9BCA5, seaGlass #8DB9B7, mutedTeal #638F91, softStone #D7D1C0, slate #35596B, ink #526F7C, inkSoft #8199A0, coral #D9937E, cream #F0EBDD`); `theme.extend.fontFamily`: `display: Fredoka` (rounded hero/logo face), `sans: "Nunito Sans"` (UI), `mono: "IBM Plex Mono"` (technical labels/coordinates/weights/traces).
- `frontend/postcss.config.js` (new): `tailwindcss` + `autoprefixer`.
- `frontend/src/styles/globals.css` (new): Tailwind directives + a `.watercolor-grain` utility (SVG `feTurbulence` data-URI, low opacity, overlay blend) + a CSS-level `@media (prefers-reduced-motion: reduce)` backstop alongside the JS hook.
- Fonts via Google Fonts `<link>` in `index.html`'s `<head>` (Fredoka, Nunito Sans, IBM Plex Mono).
- `vite.config.ts` needs no changes (Vite auto-detects the new config files).

## New `frontend/src/` structure

```
styles/globals.css                      Tailwind directives, grain texture, reduced-motion backstop
store/useBuilderStore.ts                 zustand draft-build state (shape below)
api/client.ts                            + one addition: getApp(appId, key) -> GET /v1/apps/{app}
api/authorityKey.ts                      + recordProject/listProjects/removeProject (Past Projects)
hooks/useLiveAppData.ts                  ports AppPage.tsx's stream/poll/bbox/layers state machine
hooks/useAuthorityConsoleData.ts         ports ConsolePanels.tsx's weights/verification/trace polling
hooks/usePrefersReducedMotion.ts         matchMedia wrapper
hooks/useWebGLSupport.ts                 capability probe for the no-WebGL fallback

components/layout/Header.tsx             logo (-> "/", never clears store) + static profile icon
components/layout/MainLayout.tsx         <Header/><Outlet/>, NOT applied to EndUser's route

components/home/PondScene.tsx            <Canvas> root: camera, Suspense, dpr cap, tab-hidden pause, WebGL gate
components/home/WaterSurface.tsx         custom shader plane: refraction-feel distortion, caustics, depth color, cursor ripple uniform
components/home/shaders/water.ts         vertex/fragment shader strings + uniform factory (plain .ts, no GLSL loader)
components/home/KoiFish.tsx              one koi: spline path, per-vertex body bend, tail/fin phase offset, steers around center-text zone
components/home/KoiPair.tsx              2 koi, different speed/seed, shared "occasional following" steering
components/home/Seaweed.tsx              one plant, per-vertex shader sway, seeded phase
components/home/SeaweedBed.tsx           places several along left/right edges per the reference image
components/home/FloatingLog.tsx          mossy log: slow rotation + bob, noise-mottled material
components/home/LilyPads.tsx             notched discs at corners, idle bob
components/home/Rocks.tsx                static submerged rocks
components/home/lighting/PondLighting.tsx ambient + angled "sun" directional + hemisphere light
components/home/StaticPondFallback.tsx   CSS/SVG gradient fallback when WebGL is unavailable
components/home/HomeOverlay.tsx          title, 2 wave marks, Start Project / Past Projects buttons

components/sources/AppIdField.tsx        gates the rest (SLUG check, ported from Builder.tsx)
components/sources/AreaPicker.tsx        RECOMPOSE from Builder.tsx: Leaflet bbox + destination points, collapsible panel
components/sources/IntentInput.tsx       the single large input, blinking-cursor placeholder
components/sources/SourceSuggestions.tsx renders real api.suggestSource results, push vs pull distinguished
components/sources/SourceSuggestionCard.tsx one option: label/why/kind badge
components/sources/ConnectorConfigFields.tsx RECOMPOSE from Builder.tsx's SourcePicker: URL/preset/JSON fields
components/sources/SourceChipBar.tsx     persistent bottom bar: chips + count + Continue
components/sources/SourceChip.tsx        one committed source, remove

components/architecture/ArchitectureLayout.tsx two-panel + footer shell, owns selectedLayerId
components/architecture/HeatmapPanel.tsx  LEFT: ProposedSchematic pre-activation, restyled Map.tsx post-activation
components/architecture/ProposedSchematic.tsx pre-activation placeholder grid sized from AppSpec.grid, explicit "not yet live" banner, no fabricated values
components/architecture/LayerStackPanel.tsx RIGHT: LayerRow list + authority "Live" tabs (restyled WeightsPanel/VerificationScores/AgentTrace)
components/architecture/LayerRow.tsx      layer_id/op/weight/tier badge/visibility/description, click -> selectedLayerId
components/architecture/LayerDescription.tsx deterministic (not LLM) sentence generator from kind/shape/op/meaning
components/architecture/ConnectorDiagram.tsx organic SVG wires source->operator->layer->score, highlights selection, shows the score formula
components/architecture/ActivationPanel.tsx RECOMPOSE from Builder.tsx: embedded Confirm & Activate + one-time key reveal
components/architecture/ArchitectureFooter.tsx Save Project (-> recordProject) + Go Back to Editing (-> /sources, store intact)
components/architecture/DebugDrawer.tsx   RECOMPOSE from DemoTools.tsx: hidden-by-default drawer, same DEMO_MODE gating

components/past-projects/ProjectCard.tsx one past project: appId, intent snippet, status, link, remove

pages/Home.tsx                           PondScene + HomeOverlay
pages/Sources.tsx                        AppIdField + AreaPicker + IntentInput + SourceSuggestions + SourceChipBar
pages/Architecture.tsx                   ArchitectureLayout + fresh-vs-revisit rehydration
pages/PastProjects.tsx                   listProjects() -> ProjectCard list
pages/EndUser.tsx                        REUSE-AS-IS, untouched
```

## Routing (`App.tsx` → plain route table, no more regex matching)

```
/                     MainLayout > Home
/sources              MainLayout > Sources
/architecture         MainLayout > Architecture   (fresh compile, reads store.compiledSpec)
/architecture/:appId  MainLayout > Architecture   (revisit from Past Projects, rehydrates)
/projects             MainLayout > PastProjects
/apps/:appId          -> redirect to /architecture/:appId (back-compat)
/apps/:appId/go       EndUser (NO MainLayout — untouched, own chrome, same exact path)
*                     -> redirect to /
```
`main.tsx` wraps `<App/>` in `<BrowserRouter>`.

## Zustand store (`store/useBuilderStore.ts`)

In-memory only (no `persist` middleware — persistence is the explicit "Save Project" action):
- Identity/intent/area: `appId, intent, bbox, cellM, destinationPoints`
- Sources: `sources: CompileInput[]` (+ add/remove)
- Compile result: `compiledSpec: AppSpec | null, compiling, compileError, appStatus: "idle"|"compiling"|"proposed"|"activating"|"active"`
- Activation: `activation: ActivateResponse | null` (shown once)
- UI: `selectedLayerId: string | null`

Navigating Home → Sources never clears the store (so "Start Project" resumes an in-progress draft); only a real new-app-id entry or an explicit reset does.

## Past Projects (resolves the "no list-apps endpoint" gap)

Extend `api/authorityKey.ts` in place (same guarded-storage pattern already there) with `ProjectRecord{appId, intent, status, updatedAt}` under one `localStorage` key `past-projects`, plus `recordProject`/`listProjects`/`removeProject`. Write on successful compile (`status: "proposed"`) and on successful activate (`status: "active"`) — "Save Project" in the footer is this same upsert exposed explicitly, not a separate mechanism. No new backend route.

## Migration map

| File | Disposition |
|---|---|
| `Builder.tsx` | **DELETE** — logic ported into `Sources.tsx`, `ActivationPanel.tsx`, `AreaPicker.tsx`, `ProposedSchematic.tsx` |
| `AppPage.tsx` | **DELETE** — logic ported into `useLiveAppData.ts`, `HeatmapPanel.tsx`, `LayerStackPanel.tsx` |
| `EndUser.tsx` | **untouched** |
| `api/client.ts`, `stream.ts`, `types.ts` | **reused as-is** (client.ts +`getApp`) |
| `api/authorityKey.ts` | **extended in place** |
| `components/Map/*` | **restyled** (palette heat-ramp, muted tile filter) — logic kept |
| `components/LayerToggles/*` | **DELETE** — superseded by `LayerRow`'s single-select |
| `components/AgentTrace`, `WeightsPanel`, `VerificationScores` | **restyled**, data logic untouched |
| `components/ConsolePanels/*` | **DELETE** — polling moves to `useAuthorityConsoleData.ts`, children composed directly |
| `components/DemoTools/*` | **becomes `DebugDrawer.tsx`'s body** |
| `components/ErrorChart`, `SimControls` | **restyled** |

One live-console experience remains (`Architecture.tsx`), not two.

## Build order

1. **Foundation** — deps, Tailwind/postcss config, fonts/title in `index.html`, `globals.css`, router shell, `Header`/`MainLayout`, store skeleton. Done: all 5 routes serve placeholders, Tailwind color proven, `EndUser.tsx` at `/apps/:id/go` byte-for-byte unchanged against a running backend.
2. **Home pond** — all of `components/home/*`. Done: pond animates at interactive framerate, cursor ripple works, koi avoid the center-text zone, reduced-motion visibly calms it, forced no-WebGL shows the fallback without crashing, both CTAs navigate.
3. **Sources** — `components/sources/*`, `pages/Sources.tsx`, store wiring. Done (against real `uvicorn`): app-id gating, area picker respects `MAX_GRID_CELLS`, real `/sources/suggest` call returns ranked options, chips add/remove, Continue gated correctly, real `/compile` call lands on `/architecture` with the real `AppSpec`, 409/422/503 surfaced readably.
4. **Architecture** — `components/architecture/*`, both hooks, `api.getApp`, `authorityKey.ts` extension, `pages/Architecture.tsx`, `pages/PastProjects.tsx`. Done: pre-activation panel never fabricates values, locked tiers visibly non-editable, layer selection filters the heatmap, real `/activate` call shows one-time keys, post-activation left panel ticks live via WS/poll, Save Project writes real localStorage, `/architecture/:appId` rehydrates both with and without a remembered key, Go Back to Editing preserves the draft.
5. **Polish** — `motion` page transitions, reduced-motion audit, mobile/tablet pond object-count reduction and panel stacking, perf pass (dpr cap, geometry/material reuse, dispose, tab-hidden pause), `npm run build` clean under strict TS.

## Verification

Run backend (`uvicorn app.main:app --reload`) and frontend (`npm run dev`) together for every phase — no backend changes, so every call must genuinely succeed end to end:
- Phase 1: visually confirm pond behavior, toggle OS "prefers reduced motion," force the no-WebGL fallback path.
- Phase 2: create a real app, describe a real source, confirm `/sources/suggest` network call has real latency (not instant/hardcoded), pick push and pull options, confirm real `/compile` payload/response in devtools.
- Phase 3: confirm the proposed-state panel shows zero fabricated numbers, activate for real, cross-check the live UI's score against `curl -H "X-Authority-Key: <key>" .../map?format=geojson` directly, confirm WS ticks ~2s, confirm localStorage `past-projects` entry, confirm `/architecture/:appId` rehydration both with and without a remembered key.
- Phase 5: resize to phone viewport, confirm layout degrades usably; `npm run build` exits 0 with strict TS settings intact.

## Team coordination (first implementation step, not a blocker)

Before writing code: `gh issue` + board post announcing the ownership change to `@ramgudur`/`@AllenZheng-05` (per Navmi's direct instruction) — `frontend/`, including `Builder.tsx`/`AppPage.tsx` which are about to be deleted — so Allen's in-flight #166–#169 don't collide with this branch. This is a heads-up, not a wait-for-approval gate, since the user has already authorized the ownership move directly.

Branch: new branch off a freshly-pulled `origin/main` (current tip includes #120/#127 compile API, #130 instructions, #145 Scout/webhooks, #156 destination_points, #165 sources/suggest, #173 pois/raw — all of which this plan depends on being present).
