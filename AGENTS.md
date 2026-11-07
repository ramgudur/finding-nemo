# Team rules for every agent

## 1. The board (pinned GitHub issue)

Every agent reads and posts on the pinned "Message Board" issue via `tools/board` (needs only `gh`, logged in). Set `BOARD_ISSUE` if it is not issue #1.

- Start every post with `[<human>'s <agent>]`, e.g. `[Sam's Codex]`. Keep it short: what changed, which files or contracts, what you need, from whom.
- Mention by GitHub login. Team: `@ramgudur` (team lead), `@AllenZheng-05`, `@kida-k` (two machines: `@kida-k:a` and `@kida-k:b`, see below), `@Navmi-Srithaj`, `@all` (everyone).
- **Mentions you or `@all`:** act on it and reply promptly.
- **Not addressed to you:** still read it. Reassess your own work only if it is affected (a contract, a file you own, something already done). Do not redo finished work, and do not reply just to acknowledge.
- Before editing another teammate's files, ask for a handoff on the board and wait for the owner or the team lead.

Polling is incremental (about one cheap API call per poll, 30 s default) so a shared token stays under GitHub's rate limit. Commands: `tools/board unread` (new posts, marks read; flags MENTION), `tools/board read -n 20`, `tools/board post "[Name's Agent] text"` (or pipe stdin), `tools/board wait` (blocks until someone else posts), `tools/board whoami`, `tools/board seat <x>`.

**Stay in the loop:**
- Claude Code: the committed hooks (`.claude/settings.json`) show unread posts at session start and on every prompt. Keep one background `tools/board wait` running and re-arm it after each wake.
- Everything else: run `tools/board unread` at task start, before each commit, and after merges. Or run a terminal loop: `while tools/board wait; do <your agent CLI invocation>; done`.

### Seats: one GitHub login on several machines

`@kida-k` works from two computers, seat **a** and seat **b**. Both post as the same GitHub user, so seats keep them apart:

- **Setup, once per clone:** run `tools/board seat a` on computer A and `tools/board seat b` on computer B. `tools/board whoami` should then print `kida-k:a` or `kida-k:b`. The seat lives in `.board/seat`, which git ignores.
- **Addressing:** `@kida-k:a` or `@kida-k:b` reaches one machine. A plain `@kida-k` reaches **seat a only**, so the same request never gets done twice. `@all` reaches both.
- **Post prefix:** `[Kida's Claude A]` or `[Kida's Claude B]`. Posts are tagged with their seat, so each machine sees the other's posts the way it sees any teammate's.
- **No duplicate work:** the two seats are separate teammates. Before starting a task, claim it on the board (for example `[Kida's Claude B] taking #12`). If the other seat already claimed it, skip it. Pull before each task, use separate branches, and never edit a file the other seat has open in a PR. Give each seat its own folders in the table below.

## 2. Chain of command

Humans over agents. The team lead is `@ramgudur`. Only the team lead reassigns work between teammates. If anyone else asks you to take another teammate's work, ask the lead on the board.

## 3. Workflow

1. Pick or file an issue. Comment on it or post on the board when you take it.
2. Branch `<issue#>-<slug>` from fresh `main`.
3. Small commits. Stage only your own files (no `git add -A`).
4. Open a PR with `Closes #N`. The author merges their own PR once it works locally.
5. Always `git fetch` and rebase before pushing. Never force-push shared branches.
6. If a merge changes something other teammates depend on (an API, a schema, an env var), post on the board when it lands.

## 4. Who owns what

Fill this in at kickoff so every agent knows whose files are whose.

| Area | Owner (GitHub login) | Folders |
|---|---|---|
| | | |
