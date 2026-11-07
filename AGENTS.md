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
- **No duplicate work:** the two seats are separate teammates. Before starting a task, claim it on the board (for example `[Kida's Claude B] taking #12`). If the other seat already claimed it, skip it. Pull before each task, use separate branches, and never edit a file the other seat has open in a PR. Each seat claims and owns its own tasks and areas (section 4).

## 2. Chain of command

Humans over agents. The team lead is `@ramgudur`. Only the team lead reassigns work between teammates. If anyone else asks you to take another teammate's work, ask the lead on the board.

## 3. Workflow

1. Pick or file an issue and claim it (see section 4). Do not start work you have not claimed.
2. Branch `<issue#>-<slug>` from fresh `main`.
3. Small commits. Stage only your own files (no `git add -A`).
4. Open a PR with `Closes #N`. The author merges their own PR once it works locally.
5. Always `git fetch` and rebase before pushing. Never force-push shared branches.
6. If a merge changes something other teammates depend on (an API, a schema, an env var), post on the board when it lands.

## 4. Who owns what: claim as you go

Work isn't split up front. You own what you claim, plus whatever grows out of it.

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

### Areas (filled in as they come up)

| Area | Owner (GitHub login, plus seat if used) | Folders | Started in |
|---|---|---|---|
| | | | |
