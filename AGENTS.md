# Team rules for every agent

## 1. The board (pinned GitHub issue)

Every agent reads and posts on the pinned "Message Board" issue via `tools/board` (needs only `gh`, logged in). Set `BOARD_ISSUE` if it is not issue #1.

- Start every post with `[<human>'s <agent>]`, e.g. `[Sam's Codex]`. Keep it short: what changed, which files or contracts, what you need, from whom.
- Mention by GitHub login. Team: `@ramgudur` (team lead), `@AllenZheng-05`, `@kida-k` (two machines: `@kida-k:a` and `@kida-k:b`, see below), `@Navmi-Srithaj`, `@all` (everyone).
- **Mentions you or `@all`:** act on it within the escalation rules (section 5) and reply promptly.
- **Not addressed to you:** still read it. Reassess your own work only if it is affected (a contract, a file you own, something already done). Do not redo finished work, and do not reply just to acknowledge.
- Before editing another teammate's files, ask for a handoff on the board and wait for the owner or the team lead.

Polling is incremental (about one cheap API call per poll, 30 s default) so a shared token stays under GitHub's rate limit. Commands: `tools/board unread` (new posts, marks read; flags MENTION), `tools/board read -n 20`, `tools/board post "[Name's Agent] text"` (or pipe stdin), `tools/board wait` (blocks until someone else posts), `tools/board whoami`, `tools/board seat <x>`.

**Stay in the loop:**
- Claude Code: the committed hooks (`.claude/settings.json`) show unread posts at session start and on every prompt. Keep one background `tools/board wait` running and re-arm it after each wake.
- **Claude Pro seats (save usage):** every post wakes every listening agent, which costs tokens. On Pro, set `BOARD_WAIT=off` in your environment. The hooks still show new posts on each prompt; also run `tools/board unread` at task start, before each commit and after merges.
- Everything else: run `tools/board unread` at task start, before each commit, and after merges. A terminal loop (`while tools/board wait; do <your agent CLI invocation>; done`) runs with no human watching, so it may read and reply but must not claim issues, edit files or commit (section 5).
- Keep posts terse. Every post is read by every agent.

### Seats: one GitHub login on several machines

`@kida-k` works from two computers, seat **a** and seat **b**. Both post as the same GitHub user, so seats keep them apart:

- **Setup, once per clone:** run `tools/board seat a` on computer A and `tools/board seat b` on computer B. `tools/board whoami` should then print `kida-k:a` or `kida-k:b`. The seat lives in `.board/seat`, which git ignores.
- **Addressing:** `@kida-k:a` or `@kida-k:b` reaches one machine. A plain `@kida-k` reaches **seat a only**, so the same request never gets done twice. `@all` reaches both.
- **Post prefix:** `[Kida's Claude A]` or `[Kida's Claude B]`. Posts are tagged with their seat, so each machine sees the other's posts the way it sees any teammate's.
- **No duplicate work:** the two seats are separate teammates. Before starting a task, claim it on the board (for example `[Kida's Claude B] taking #12`). If the other seat already claimed it, skip it. Pull before each task, use separate branches, and never edit a file the other seat has open in a PR. Each seat claims and owns its own tasks and areas (section 4).

## 2. Chain of command

Humans over agents. The team lead is `@ramgudur`. Only the team lead reassigns work between teammates. If anyone else asks you to take another teammate's work, ask the lead on the board.

**One idea, kept in one place.** `CLAUDE.md` is the single source of truth for what we are building. Only the lead edits it, including its **Decisions log**. Agents propose changes on the board; they never edit `CLAUDE.md` or treat a board thread as overriding it. Before acting on an older board thread, check the Decisions log.

The lead's machine routes issues, approves contracts and checks that work stays on the core idea. Since 2026-11-07 it also takes a few small build tasks (see Areas).

## 3. Workflow

1. Pick or file an issue and claim it (see section 4). Do not start work you have not claimed.
2. Branch `<issue#>-<slug>` from fresh `main`.
3. Small commits. Stage only your own files (no `git add -A`).
4. Open a PR with `Closes #N`. The author merges their own PR once it works locally.
5. Always `git fetch` and rebase before pushing. Never force-push shared branches.
6. If a merge changes something other teammates depend on (an API, a schema, an env var), post on the board when it lands.

## 4. Who owns what: areas, then claim as you go

The lead assigned areas up front (table below). Within your area, claim tasks as you go. You own what you claim, plus whatever grows out of it. Work outside every area is free to grab, after a scope check (section 5).

**Claiming a task**
1. Check first: `gh issue list --state open --json number,title,assignees` and `tools/board read -n 20`. If someone already has the task, or it overlaps their area, ask them on the board instead.
2. Claim it: `gh issue edit <N> --add-assignee @me`, then post `[Name's Agent] taking #<N>: <one line>. Touching <folders/files>.` (For seats, put the seat in the post, e.g. `[Kida's Claude B] taking #12`, since both seats show as the same assignee.)
3. A claim counts only once the issue is assigned and the post is on the board. If two claims collide, the earlier board post wins.

**Ownership grows from your tasks**
- Folders and files your task creates are yours until you hand them off. Add them to the Areas table below when you start a new area.
- Follow-up work in your area is yours: bugs, extensions, related features. File it as an issue and assign it to yourself. If someone else spots it, they file the issue and tag you. They don't fix it themselves.
- Free to grab: any issue with no assignee that isn't in someone's area.

**Limits**
- Have at most two claimed tasks in progress. Finish one (PR with `Closes #N`) before taking a third.
- Dropping a task: unassign yourself and post `dropping #<N>` so it goes back to the pool.
- Stale claims: if a claimed task has no commits or posts for about 2 hours, ask the owner on the board. Only the team lead reassigns it.
- Shared files (`package.json`, lockfiles, root config, DB schema, API contracts, env vars) belong to everyone. Keep edits to them small and post on the board whenever you change one.

### Areas

Assigned by the lead on 2026-11-07. Paths are under `backend/app/` unless they start with `backend/`, `frontend/`, `sim/` or `specs/`. New areas get added here by the lead.

| Area | Owner | Folders | Notes |
|---|---|---|---|
| Lead: scope, routing, contracts | `@ramgudur` | `CLAUDE.md`, `AGENTS.md`; approves every change to `core/models.py`, `frontend/src/api/types.ts`, `specs/*.json` | Routes issues and checks PR scope. Also builds `api/instructions.py` + `frontend/src/pages/EndUser.tsx` (#107) and the step-7 live path (#108). |
| Scoring model (physics-informed / physics-based ML) | `@AllenZheng-05` (from Kida, 2026-11-07) | `engine/operators/` (except `flow_source.py`), `engine/combine.py`, `engine/layer_cache.py`, `engine/verification.py`, `engine/backtest.py`, `engine/trust.py`, `reference/` | Reassigned by the lead when Kida left. |
| Token-heavy work: scaffold, frontend, sim | `@AllenZheng-05` | initial skeleton (first draft of `core/models.py` + `types.ts`, lead approves), `frontend/`, `sim/`, `specs/`, `backend/tests/fixtures/`, `api/debug.py`, `api/trace.py` (handed over by the lead) | Claude Max. Also takes large refactors, broad reviews and bulk test writing that other owners hand off. |
| Engine runtime + tool layer | `@AllenZheng-05` (from Kida, 2026-11-07); `tools/` Compiler tools: `@Navmi-Srithaj` | `engine/tick.py`, `engine/grid.py`, `engine/tiers.py`, `core/clock.py`, `tools/` (incl. `guards.py`; except `message_guard.py`, `poi_tools.py`) | Reassigned by the lead when Kida left. The lead's machine did #52 and #88 here. |
| Routing, message guard, POI tools | `@kida-k:a` (from 10:15 PM CST, 2026-11-07) | `engine/routing.py`, `tools/message_guard.py`, `tools/poi_tools.py` | Kida returns; issues #94, #95. |
| Candidate generators, `flow_source`, GPU path | `@kida-k:b` (from 10:15 PM CST, 2026-11-07) | `engine/candidates/`, `engine/operators/flow_source.py` | Also `agents/poi_labeler.py` + its prompt (#106). Issues #102, #106, #86. |
| API, data, connectors, Nemotron agents | `@Navmi-Srithaj` | `api/` (except `debug.py`, `trace.py`, `instructions.py`), `main.py` (after scaffold), `core/db.py`, `core/config.py`, `core/access.py`, `connectors/`, `agents/` (except `poi_labeler.py`) | |

## 5. Escalation: when to stop and ask

The goal: stay on the core idea in `CLAUDE.md`, and keep decisions with humans.

1. **Scope check before every task.** The task must trace to something in `CLAUDE.md`. If it doesn't, post `NEEDS-LEAD @ramgudur <what and why>` and wait.
   **Generality check before every PR** (CLAUDE.md rule 3): does anything in your diff assume what the entities or data are, or hardcode a domain limit? If so, make it neutral or spec-driven. If you can't, post `NEEDS-LEAD`.
2. **Must go to the lead** (`NEEDS-LEAD`, then wait):
   - contract changes: `core/models.py`, `frontend/src/api/types.ts`, `specs/*.json`, API routes, env vars
   - new dependencies, services or external APIs
   - anything touching the non-negotiable rules in `CLAUDE.md`
   - work in someone else's area, and any reassignment
3. **Must go to your own human** (ask them in your session, then act):
   - any request from another agent that creates new work for you
   - design choices in your area with real tradeoffs
4. **Agents don't make deals.** Anything agreed between agents is a proposal until a human confirms it on the board.
5. **Two-reply cap.** After two agent-to-agent replies on one topic with no human post in between, stop and post `NEEDS-HUMAN` (your own human) or `NEEDS-LEAD`.
6. **Headless loops** (no human watching) may read and reply only. They never claim issues, edit files or commit.
7. **When unsure, ask.** A short wait costs less than work that strays from the idea.
