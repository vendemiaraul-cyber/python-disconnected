#!/bin/bash
# Pocket Studio: tells Claude whether Roblox Studio is open on the user's PC,
# and gets the session ready if it is. Reads the signal the Claude Sync plugin
# sends to ntfy.sh. Safe to re-run any time: .claude/hooks/session-start.sh
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0

topic=$(grep -oE 'pocketstudio-[0-9a-f]{16}' plugin/ClaudeSync.lua 2>/dev/null | head -1)
[ -n "$topic" ] || exit 0

json=$(curl -sS --max-time 4 "https://ntfy.sh/$topic/json?poll=1&since=2h" 2>/dev/null) || json="UNREACHABLE"
report=$(printf '%s' "$json" | python3 -I .claude/hooks/studio_status.py) || exit 0
echo "$report"

# Studio is open: set up right away so code can flow as soon as the user asks.
if grep -q '^STUDIO_OPEN' <<<"$report"; then
  if git fetch -q origin studio 2>/dev/null; then
    if git merge -q --no-edit origin/studio >/dev/null 2>&1; then
      echo "Setup done: merged origin/studio, so you're building on the game that's in Studio."
    else
      git merge --abort 2>/dev/null
      echo "Setup: couldn't auto-merge origin/studio (conflict). Merge it by hand before editing game/."
    fi
  fi
fi
exit 0
