"""Summarise the Claude Sync plugin's ntfy.sh messages (JSON lines on stdin) for Claude."""
import json
import sys
import time

OPEN_WINDOW = 180      # seconds without a heartbeat before Studio counts as closed
ERROR_WINDOW = 30 * 60

raw = sys.stdin.read()
print("## Pocket Studio status (checked automatically)")
if raw.strip() == "UNREACHABLE":
    print("STUDIO_UNKNOWN: couldn't reach ntfy.sh, so Studio's state is unknown.")
    sys.exit(0)

events = []
for line in raw.splitlines():
    try:
        outer = json.loads(line)
        if outer.get("event") != "message":
            continue
        data = json.loads(outer["message"])
        data["_t"] = outer["time"]
        events.append(data)
    except (ValueError, KeyError, TypeError):
        continue
events.sort(key=lambda e: e["_t"])

now = time.time()
alive = [e for e in events if e.get("event") in ("opened", "heartbeat", "synced", "closed")]
last = alive[-1] if alive else None

if last and last["event"] != "closed" and now - last["_t"] < OPEN_WINDOW:
    beat = next((e for e in reversed(alive) if e.get("event") in ("opened", "heartbeat")), last)
    print(
        f'STUDIO_OPEN: Roblox Studio is open on the user\'s PC right now '
        f'(place "{last.get("place", "?")}", last signal {int(now - last["_t"])}s ago). '
        f'Auto-pull is {"ON" if beat.get("auto") else "OFF"}; '
        f'last synced commit {beat.get("synced") or "none yet"} from branch "{beat.get("branch", "studio")}".'
    )
    print("They're ready to test: after you push, they can Pull (or Auto-pull picks it up) and press Play.")
elif last:
    ago = int((now - last["_t"]) / 60)
    print(f"STUDIO_CLOSED: Studio isn't open (last signal {ago} min ago). Code you push syncs next time they open it.")
else:
    print("STUDIO_CLOSED: no signal from Studio in the last 2 hours. It's closed, or the Claude Sync plugin "
          "isn't installed or allowed to reach ntfy.sh yet.")

errors = [e for e in events if e.get("event") == "errors" and now - e["_t"] < ERROR_WINDOW]
if errors:
    print("\nRecent red errors from Studio's Output (newest last):")
    lines = [(e.get("mode", "?"), l) for e in errors for l in e.get("lines", [])]
    for mode, text in lines[-15:]:
        print(f"- [{mode}] {text}")
