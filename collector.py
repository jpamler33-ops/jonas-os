#!/usr/bin/env python3
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

STATE_FILE = Path("state/seen.json")
COOKIE_FILE = Path(".runtime/x-cookies.txt")
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov"}
TWEET_RE = re.compile(r"^(?P<account>[A-Za-z0-9_]+)_(?P<tweet>\d{8,})_(?P<num>\d+)\.(?P<ext>[A-Za-z0-9]+)$")
MAX_SEEN_PER_ACCOUNT = 1000
POST_RANGE = os.getenv("X_POST_RANGE", "1-40")
SEND_INITIAL_BACKLOG = os.getenv("SEND_INITIAL_BACKLOG", "false").lower() == "true"
TELEGRAM_MAX_MB = int(os.getenv("TELEGRAM_MAX_MB", "49"))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
X_COOKIES_B64 = os.getenv("X_COOKIES_B64", "").strip()
X_AUTH_TOKEN = os.getenv("X_AUTH_TOKEN", "").strip()
X_CT0 = os.getenv("X_CT0", "").strip()
X_ACCOUNTS_ENV = os.getenv("X_ACCOUNTS", "").strip()

def log(msg):
    print(msg, flush=True)

def normalize_account(value):
    value = value.strip()
    value = value.replace("https://x.com/", "").replace("https://twitter.com/", "")
    value = value.split("/", 1)[0].lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
        raise ValueError(f"invalid X account: {value!r}")
    return value

def load_accounts():
    accounts = []
    if X_ACCOUNTS_ENV:
        raw = X_ACCOUNTS_ENV.split(",")
    else:
        path = Path("config/accounts.txt")
        raw = path.read_text("utf-8").splitlines() if path.exists() else []
    for item in raw:
        item = item.strip()
        if not item or item.startswith("#"):
            continue
        account = normalize_account(item)
        if account.lower() not in {a.lower() for a in accounts}:
            accounts.append(account)
    return accounts

def load_state():
    if not STATE_FILE.exists():
        return {"version": 1, "accounts": {}}
    try:
        data = json.loads(STATE_FILE.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state root not object")
        data.setdefault("version", 1)
        data.setdefault("accounts", {})
        return data
    except Exception as exc:
        raise RuntimeError(f"state file invalid: {exc}")

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.replace(STATE_FILE)

def materialize_cookies():
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if X_COOKIES_B64:
        try:
            COOKIE_FILE.write_bytes(base64.b64decode(X_COOKIES_B64))
            os.chmod(COOKIE_FILE, 0o600)
            return COOKIE_FILE
        except Exception as exc:
            raise RuntimeError(f"X_COOKIES_B64 invalid: {exc}")

    if X_AUTH_TOKEN:
        # Minimal Netscape cookie jar for an authenticated X session.
        # auth_token is the key cookie; ct0 is included when available.
        lines = [
            "# Netscape HTTP Cookie File",
            f".x.com\tTRUE\t/\tTRUE\t2147483647\tauth_token\t{X_AUTH_TOKEN}",
        ]
        if X_CT0:
            lines.append(f".x.com\tTRUE\t/\tTRUE\t2147483647\tct0\t{X_CT0}")
        COOKIE_FILE.write_text("\n".join(lines) + "\n", "utf-8")
        os.chmod(COOKIE_FILE, 0o600)
        return COOKIE_FILE

    return None

def tg(method, *, data=None, files=None, timeout=180):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    r = requests.post(url, data=data, files=files, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"ok": False, "description": r.text[:500]}
    if not r.ok or not payload.get("ok"):
        raise RuntimeError(payload.get("description") or f"Telegram HTTP {r.status_code}")
    return payload["result"]

def send_text(text):
    tg("sendMessage", data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "true"}, timeout=30)

def send_video(path, account, tweet_id):
    source = f"https://x.com/{account}/status/{tweet_id}"
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > TELEGRAM_MAX_MB:
        send_text(f"⚠️ @{account}: Video {size_mb:.1f} MB, über dem Bot-Limit.\n{source}")
        return False
    caption = f"🎬 @{account}\n{source}"
    try:
        with path.open("rb") as fh:
            tg("sendVideo", data={"chat_id": CHAT_ID, "caption": caption, "supports_streaming": "true"},
               files={"video": (path.name, fh, "video/mp4")}, timeout=300)
        return True
    except Exception as first:
        log(f"sendVideo failed: {first}; trying document")
        with path.open("rb") as fh:
            tg("sendDocument", data={"chat_id": CHAT_ID, "caption": caption},
               files={"document": (path.name, fh, "application/octet-stream")}, timeout=300)
        return True

def seen_filter(seen_ids):
    clauses = ["extension in ('mp4', 'webm', 'mkv', 'mov')"]
    recent = []
    for value in list(seen_ids)[-MAX_SEEN_PER_ACCOUNT:]:
        try:
            recent.append(str(int(value)))
        except Exception:
            pass
    if recent:
        clauses.append(f"tweet_id not in ({','.join(recent)})")
    return " and ".join(clauses)

def download_account(account, seen_ids, output_dir, cookie_path):
    cmd = [
        "gallery-dl", "--post-range", POST_RANGE,
        "--filter", seen_filter(seen_ids), "--no-mtime",
        "-D", str(output_dir),
        "-o", "extractor.twitter.videos=true",
        "-o", "extractor.twitter.text-tweets=false",
        "-o", "extractor.twitter.retweets=false",
        "-o", "extractor.twitter.replies=false",
        "-o", "extractor.twitter.pinned=true",
        "-o", "extractor.twitter.filename={author[name]}_{tweet_id}_{num}.{extension}",
    ]
    if cookie_path:
        cmd += ["-o", f"extractor.twitter.cookies={cookie_path}"]
    cmd.append(f"https://x.com/{account}")
    delays = [0, 20, 45]
    last_error = None
    proc = None
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            log(f"Retry @{account} in {delay}s after transient X/Cloudflare failure")
            time.sleep(delay)
        log(f"Scanning @{account} (attempt {attempt}/{len(delays)})")
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
        if proc.stdout.strip():
            log(proc.stdout.strip()[-3000:])
        if proc.returncode == 0:
            break
        last_error = proc.stderr.strip()[-3000:] or f"gallery-dl exited {proc.returncode}"
        transient = any(token in last_error.lower() for token in [
            "cloudflare challenge", "403 forbidden", "request timed out",
            "temporarily unavailable", "connection reset",
        ])
        if not transient:
            raise RuntimeError(last_error)
    if proc is None or proc.returncode != 0:
        raise RuntimeError(last_error or "gallery-dl failed after retries")
    items = {}
    for p in output_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        m = TWEET_RE.match(p.name)
        if not m:
            log(f"Skipping unexpected filename: {p.name}")
            continue
        tweet_id = m.group("tweet")
        items.setdefault(tweet_id, []).append(p)
    for media in items.values():
        media.sort()
    return items

def main():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    accounts = load_accounts()
    if not accounts:
        raise RuntimeError("No X accounts configured")
    cookie_path = materialize_cookies()
    state = load_state()
    state_accounts = state.setdefault("accounts", {})
    now = int(time.time())
    failures = []
    total_sent = 0
    changed = False

    if not state.get("telegram_verified"):
        me = tg("getMe", timeout=30)
        send_text(
            "✅ X Video Collector ist aktiv.\n"
            "Überwachung: " + ", ".join(f"@{a}" for a in accounts) + "\n"
            "Intervall: alle 5 Minuten"
        )
        state["telegram_verified"] = {
            "at_unix": now,
            "bot_username": me.get("username"),
            "chat_id": CHAT_ID,
        }
        changed = True

    with tempfile.TemporaryDirectory(prefix="xcollector-") as tmp:
        tmp = Path(tmp)
        for account in accounts:
            key = account.lower()
            acc_state = state_accounts.setdefault(key, {"handle": account, "initialized": False, "seen": []})
            acc_state["handle"] = account
            seen = [str(x) for x in acc_state.get("seen", [])]
            seen_set = set(seen)
            account_dir = tmp / account
            account_dir.mkdir(parents=True, exist_ok=True)
            try:
                found = download_account(account, seen, account_dir, cookie_path)
            except Exception as exc:
                failures.append(f"@{account}: {exc}")
                continue

            new_ids = sorted(found.keys(), key=int)
            if not acc_state.get("initialized", False) and not SEND_INITIAL_BACKLOG:
                if new_ids:
                    log(f"Bootstrap @{account}: recording {len(new_ids)} video tweet(s), sending none")
                seen.extend(x for x in new_ids if x not in seen_set)
                acc_state["initialized"] = True
                acc_state["seen"] = seen[-MAX_SEEN_PER_ACCOUNT:]
                changed = True
                continue

            for tweet_id in new_ids:
                if tweet_id in seen_set:
                    continue
                sent_all = True
                for path in found[tweet_id]:
                    try:
                        if send_video(path, account, tweet_id):
                            total_sent += 1
                    except Exception as exc:
                        sent_all = False
                        failures.append(f"@{account}/{tweet_id}: Telegram: {exc}")
                        break
                if sent_all:
                    seen.append(tweet_id)
                    seen_set.add(tweet_id)
                    changed = True

            if not acc_state.get("initialized", False):
                acc_state["initialized"] = True
                changed = True
            new_seen = seen[-MAX_SEEN_PER_ACCOUNT:]
            if acc_state.get("seen", []) != new_seen:
                acc_state["seen"] = new_seen
                changed = True

    heartbeat = int(state.get("heartbeat_unix") or 0)
    if now - heartbeat >= 30 * 24 * 3600:
        state["heartbeat_unix"] = now
        changed = True

    if changed:
        save_state(state)
    else:
        log("State unchanged; no repository commit needed.")

    log(f"Done. Telegram uploads: {total_sent}. Accounts: {len(accounts)}.")
    if failures:
        for failure in failures:
            log("ERROR " + failure)
        raise RuntimeError(f"{len(failures)} account/delivery error(s)")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
