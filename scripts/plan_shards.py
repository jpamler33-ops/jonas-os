#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

def normalize(value):
    value = value.strip()
    value = value.replace("https://x.com/", "").replace("https://twitter.com/", "")
    value = value.split("/", 1)[0].lstrip("@")
    if not HANDLE.fullmatch(value):
        raise SystemExit(f"Invalid X account: {value!r}")
    return value

raw_env = os.getenv("X_ACCOUNTS", "").strip()
if raw_env:
    raw = raw_env.split(",")
else:
    path = Path("config/accounts.txt")
    raw = path.read_text("utf-8").splitlines() if path.exists() else []

accounts = []
seen = set()
for item in raw:
    item = item.strip()
    if not item or item.startswith("#"):
        continue
    account = normalize(item)
    key = account.lower()
    if key not in seen:
        seen.add(key)
        accounts.append(account)

if not accounts:
    raise SystemExit("No X accounts configured")

size = int(os.getenv("SHARD_SIZE", "3"))
size = max(1, min(size, 20))
include = []
for i in range(0, len(accounts), size):
    chunk = accounts[i:i + size]
    include.append({
        "id": f"{i // size:03d}",
        "accounts": ",".join(chunk),
        "count": len(chunk),
    })

matrix = json.dumps({"include": include}, separators=(",", ":"))
print(f"Accounts: {len(accounts)} | Shards: {len(include)} | Shard size: {size}")
for shard in include:
    print(f"  shard {shard['id']}: {shard['accounts']}")

output = os.environ.get("GITHUB_OUTPUT")
if output:
    with open(output, "a", encoding="utf-8") as fh:
        fh.write(f"matrix={matrix}\n")
        fh.write(f"account_count={len(accounts)}\n")
        fh.write(f"shard_count={len(include)}\n")
else:
    print(matrix)
