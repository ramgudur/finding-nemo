#!/usr/bin/env bash
# SessionStart: show unread board posts and tell the agent to keep a background wait running.
# Never fails the session: always exits 0.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
out="$(tools/board unread 2>&1)" || out="(board unreachable: $(printf '%s' "$out" | head -n 1)). Run tools/board unread later."
echo "Team board (GitHub issue #${BOARD_ISSUE:-1}). Rules are in AGENTS.md."
seat="$(tools/board seat 2>/dev/null)"
if [ -n "$seat" ]; then
  echo "You are seat '$seat' of @$(tools/board whoami 2>/dev/null | cut -d: -f1) (address: @$(tools/board whoami 2>/dev/null)). Another machine shares this GitHub login: only act on mentions flagged MENTION for this seat, claim work on the board before starting it, and treat posts from your other seat as a teammate's."
fi
if [ -n "$out" ]; then
  echo "Unread posts:"
  echo "$out"
else
  echo "No unread posts."
fi
if [ "${BOARD_WAIT:-}" = "off" ]; then
  echo "Board loop is off on this machine (BOARD_WAIT=off). New posts show on each prompt. Do NOT arm a background tools/board wait. Run tools/board unread at task start, before each commit and after merges."
else
  cat <<'MSG'
Board loop: start ONE background Bash command now with run_in_background: `tools/board wait`. When it completes, its output is new posts: handle them (a MENTION means act and reply on the board, within the escalation rules in AGENTS.md section 5; otherwise reassess your own work only if affected, no acknowledgement-only replies), then re-arm by starting `tools/board wait` in the background again. Do not run two waits at once.
MSG
fi
exit 0
