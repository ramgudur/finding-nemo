# Handoff: Finding Nemo frontend rebuild

Branch: `frontend-aquatic-rebuild` (off `origin/main` at `bb38402`). Nothing committed yet — everything below is uncommitted working-tree state. See `PLAN.md` in this same folder for the full approved plan (context, dependency list, file-by-file structure, routing, store shape, migration map, build order, verification steps). This file is a status snapshot on top of that plan, not a replacement for it — read `PLAN.md` first.

## Status: Phase 0 (Foundation) done, Phase 1 (Home pond) not started

### Done
- `package.json`: added `react-router-dom`, `zustand`, `tailwindcss`/`postcss`/`autoprefixer`, `three`/`@react-three/fiber`/`@react-three/drei`/`@react-three/postprocessing`, `motion`, `lucide-react`. `npm install` run, `npm run typecheck` passes clean.
- `tailwind.config.js`, `postcss.config.js`, `src/styles/globals.css` (palette tokens, font families, watercolor-grain utility, reduced-motion CSS backstop).
- `index.html`: title + Google Fonts (`Fredoka`, `Nunito Sans`, `IBM Plex Mono`).
- `src/store/useBuilderStore.ts`: the zustand draft store, shape matches `PLAN.md` §5 exactly.
- `src/components/layout/Header.tsx` + `MainLayout.tsx`: logo→`/` (never clears the store), static profile icon, small health-check dot.
- `src/App.tsx`: real route table via `react-router-dom` (see routes below), `src/main.tsx` wraps in `<BrowserRouter>`.
- `src/api/client.ts`: added `getApp(appId, key)` (`GET /v1/apps/{app}`), `operators()` (`GET /v1/operators` — new since the plan was written, see Gotchas), `pois(appId, key)` (`GET /v1/apps/{app}/pois`), `raw(appId, inputId, key, {since?, limit?})` (`GET /v1/apps/{app}/inputs/{id}/raw`, authority only, server caps `limit` at 5000).
- `src/api/types.ts`: added `OperatorInfo` (mirrors the new `GET /v1/operators` route).
- `src/api/authorityKey.ts`: added `ProjectRecord`/`recordProject`/`listProjects`/`removeProject` (Past Projects, localStorage key `past-projects`), exactly as planned.
- `src/pages/PastProjects.tsx` + `src/components/past-projects/ProjectCard.tsx`: fully built, real (reads `listProjects()`, links to `/architecture/:appId`).
- `src/pages/Home.tsx`, `Sources.tsx`, `Architecture.tsx`: **placeholder stubs only** (just enough to make the route table compile) — none of the real Phase 1–3 work has started on these three.
- Verified end-to-end: backend (`uvicorn app.main:app --port 8000`) and frontend (`npm run dev`) both start clean, `/v1/health` proxies correctly through Vite.

### Route table (`src/App.tsx`), live now
```
/apps/:appId/go       EndUser (no MainLayout — untouched, own chrome)
/                     MainLayout > Home
/sources              MainLayout > Sources
/architecture         MainLayout > Architecture
/architecture/:appId  MainLayout > Architecture
/projects             MainLayout > PastProjects
/apps/:appId          -> redirect to /architecture/:appId
*                     -> redirect to /
```

### Not started
- **Phase 1 — Home pond**: nothing in `components/home/*` exists yet. This is the single highest-priority visual piece per the plan (full Three.js/R3F koi pond: `PondScene`, `WaterSurface` + custom shader, `KoiFish`/`KoiPair`, `Seaweed`/`SeaweedBed`, `FloatingLog`, `LilyPads`, `Rocks`, `PondLighting`, `StaticPondFallback`, `HomeOverlay`). `useWebGLSupport.ts` and `usePrefersReducedMotion.ts` hooks are already written and ready to use.
- **Phase 2 — Sources**: `components/sources/*` and the real `pages/Sources.tsx` not started.
- **Phase 3 — Architecture**: `components/architecture/*`, `hooks/useLiveAppData.ts`, `hooks/useAuthorityConsoleData.ts` not started.
- **Phase 4 — Polish**: not started.
- `Builder.tsx` and `AppPage.tsx` **still exist, completely untouched** — per the migration map they get deleted only once their logic is fully ported into the new pages. Do not delete them until Sources.tsx and Architecture.tsx are real.

## Important: re-verify against `origin/main` before continuing

`main` moved fast during this build (49 commits landed between the plan being approved and Phase 0 starting) and the board shows it's still moving fast. Confirmed on the board (09:19, after the heads-up post went out):

> [Allen's Claude] confirmed, I'm making no more frontend/ changes (lead rule since 08:59). #166–169 are backend only; the Builder edits already on main are from #156, #165 and #174.

So **the frontend is uncontested from here** — no race with Allen. Three backend routes landed since planning and are already wired into `client.ts`/`types.ts` (per Allen's same post, shapes confirmed, Ram adding them to `CLAUDE.md` Core contracts):
- `PUT /v1/apps/{app}/spec` → `SpecEditResponse` — `Builder.tsx`'s `Review` component already has a full real implementation of editing a pending spec (tier/operator/weight/plausibility/label/visibility/routing, dirty-tracking, save/discard, warnings/errors from `ApiError.details`). **Port this into the Architecture page's right panel**, don't drop it — it's real and working, and was the reason `ApiError` grew a `details: string[]` field. Read `Builder.tsx` lines ~347–626 (`Review`) and ~756–773 (`save()`) before building `LayerStackPanel`/`ActivationPanel`.
- `GET /v1/operators` → `OperatorInfo[]` — wired as `api.operators()`.
- `GET /pois`, `GET /inputs/{input}/raw` → wired as `api.pois()`/`api.raw()`.

**Coming soon, not landed yet — don't build against these, but watch the board for them, since each would change a piece of this plan:**
- `POST /changes/{id}/revert` (#193, `Change.reverts` field) — could inform an "undo" affordance in `LayerStackPanel`.
- `POST /scout`, `/label-proposals` (#183, #190).
- `GET /compile/status` + a `CompileFailure` 422 shape (#181) — **this one matters most for `Sources.tsx`**: the current plan has Continue block synchronously on `api.compile()` for "tens of seconds" (matching `Builder.tsx`'s current UX); a status-polling endpoint might mean a better progressive UI is possible by the time Phase 2 starts.

Also worth knowing: Ram found and is fixing a platform bug (`agents/loop.py`) where tool calls run synchronously on the event loop, so a slow tool call during compile/Scout/POI-labeler runs can freeze the WS stream and tick schedule for up to ~20s. Explains/confirms why compile is slow and why a stuck WS stream during compile isn't necessarily this branch's bug.

Run `git fetch origin && git log --oneline origin/main -10` and skim the board (`tools/board read -n 20`) before resuming — don't trust this snapshot beyond a spot-check.

## Team coordination already done

Posted to the board announcing frontend ownership moved to this branch and that it replaces `Builder.tsx`/`AppPage.tsx` wholesale. Allen acknowledged and confirmed he's stopped touching `frontend/` (lead rule since 08:59) — see above. Will need a follow-up board post when this branch is ready for a PR, flagged `NEEDS-LEAD` given the scope (new deps, deleted files, former-Allen area).

## How to resume

1. `git fetch origin`, check the board, re-skim any new `main` commits touching `frontend/` or the contracts this plan depends on (`CompileRequest`/`CompileInput`/`SourceOption`/`AppSpec`/`SpecEditResponse`).
2. Backend: `cd backend && uvicorn app.main:app --reload --port 8000`. Frontend: `cd frontend && npm run dev` (proxies `/v1` to `:8000` already configured, no changes needed).
3. Continue at **Phase 1** per `PLAN.md`: build out `src/components/home/*` and replace the placeholder `src/pages/Home.tsx`. Done-criteria for each phase are listed in `PLAN.md`'s "Build order" section — use those to know when to move to the next phase.
4. Nothing is committed on this branch yet — the first commit should probably be Phase 0 + this handoff, described honestly (new deps, config, routing skeleton, Past Projects, no visual rebuild yet).
