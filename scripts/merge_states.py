#!/usr/bin/env python3
import json
from pathlib import Path

BASE = Path("state/seen.json")
SHARDS = Path("shard-states")
MAX_SEEN = 1000

def load(path):
    if not path.exists():
        return {"version": 1, "accounts": {}}
    return json.loads(path.read_text("utf-8"))

state = load(BASE)
state.setdefault("version", 1)
state.setdefault("accounts", {})

files = sorted(SHARDS.glob("shard-*.json"))
print(f"Merging {len(files)} shard state file(s)")

for path in files:
    shard = load(path)
    state["version"] = max(int(state.get("version", 1)), int(shard.get("version", 1)))

    if not state.get("telegram_verified") and shard.get("telegram_verified"):
        state["telegram_verified"] = shard["telegram_verified"]

    state["heartbeat_unix"] = max(
        int(state.get("heartbeat_unix") or 0),
        int(shard.get("heartbeat_unix") or 0),
    )

    for key, incoming in shard.get("accounts", {}).items():
        current = state["accounts"].get(key, {})
        merged = dict(current)
        merged.update(incoming)

        old_seen = [str(x) for x in current.get("seen", [])]
        new_seen = [str(x) for x in incoming.get("seen", [])]
        union = set(old_seen) | set(new_seen)
        try:
            ordered = sorted(union, key=int)
        except ValueError:
            ordered = sorted(union)

        merged["seen"] = ordered[-MAX_SEEN:]
        merged["initialized"] = bool(
            current.get("initialized", False) or incoming.get("initialized", False)
        )
        state["accounts"][key] = merged

BASE.parent.mkdir(parents=True, exist_ok=True)
BASE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", "utf-8")
print(f"Merged account states: {len(state['accounts'])}")
