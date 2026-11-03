#!/usr/bin/env python3
"""Poll the Guardian's congress tracker and push changes in called seats to ntfy.

Standard library only. Configuration is via environment variables:

  NTFY_TOPIC     ntfy topic to publish to (required unless DRY_RUN=1)
  NTFY_SERVER    ntfy server base URL (default: https://ntfy.sh)
  NTFY_TOKEN     optional access token for a protected topic
  POLL_SECONDS   seconds between polls (default: 60, the API's max-age)
  STATE_FILE     where the last seen tally is stored (default: ./state.json)
  DRY_RUN        if "1", print notifications instead of sending them
  ONCE           if "1", poll a single time and exit
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

FEED_URL = (
    "https://zug.guardianapis.com/views/elections/"
    "us-general-2026/congress-fronts-tracker/all/v1"
)
USER_AGENT = "midterms-watch/1.0"


def env(name, default=None):
    value = os.environ.get(name, "").strip()
    return value if value else default


NTFY_SERVER = env("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = env("NTFY_TOPIC")
NTFY_TOKEN = env("NTFY_TOKEN")
POLL_SECONDS = int(env("POLL_SECONDS", "60"))
STATE_FILE = env("STATE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
DRY_RUN = env("DRY_RUN") == "1"
ONCE = env("ONCE") == "1"


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def fetch(etag=None):
    """Return (status, etag, data). data is None on 304."""
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(FEED_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.headers.get("ETag"), json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, etag, None
        raise


def parse(data):
    """Reduce the feed to {chamber: {"called", "total", "parties": {name: seats}}}."""
    tally = {}
    for comp in data.get("components", []):
        if comp.get("kind") != "sideBySide":
            continue
        for side in ("left", "right"):
            panel = comp.get(side) or {}
            chamber = panel.get("heading")
            if not chamber:
                continue
            entry = {"called": 0, "total": 0, "parties": {}}
            for child in panel.get("children", []):
                props = child.get("props") or {}
                if child.get("kind") == "versus":
                    for k in ("left", "right"):
                        p = props.get(k) or {}
                        name = (p.get("name") or k).rstrip("*")
                        entry["parties"][name] = p.get("value", 0)
                elif child.get("kind") == "progressNumber":
                    entry["called"] = props.get("progress", 0)
                    entry["total"] = props.get("total", 0)
            tally[chamber] = entry
    return tally


def describe_changes(old, new):
    """Return a list of human-readable lines, one per chamber that changed."""
    lines = []
    for chamber, cur in new.items():
        prev = old.get(chamber)
        if prev == cur:
            continue
        parts = []
        for party, seats in cur["parties"].items():
            before = (prev or {}).get("parties", {}).get(party, seats)
            delta = seats - before
            parts.append(f"{party[0]} {seats}" + (f" ({delta:+d})" if delta else ""))
        called_delta = cur["called"] - (prev or {}).get("called", cur["called"])
        called = f"{cur['called']}/{cur['total']} called" + (f" ({called_delta:+d})" if called_delta else "")
        lines.append(f"{chamber}: {', '.join(parts)} · {called}")
    return lines


def notify(title, body):
    if DRY_RUN or not NTFY_TOPIC:
        log(f"[dry run] {title}\n{body}")
        return
    headers = {"Title": title, "Tags": "ballot_box_with_ballot", "User-Agent": USER_AGENT}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def poll_once(state):
    status, etag, data = fetch(state.get("etag"))
    if status == 304 or data is None:
        return state
    tally = parse(data)
    if not tally:
        log("feed returned no chambers; skipping")
        return state
    previous = state.get("tally")
    if previous is None:
        log("first run, baseline: " + " | ".join(describe_changes({}, tally)))
    else:
        lines = describe_changes(previous, tally)
        if lines:
            notify("Midterms: seats called", "\n".join(lines))
            log("notified: " + " | ".join(lines))
    new_state = {"etag": etag, "tally": tally}
    save_state(new_state)
    return new_state


def main():
    if not NTFY_TOPIC and not DRY_RUN:
        sys.exit("Set NTFY_TOPIC (or DRY_RUN=1 to print instead of sending).")
    state = load_state()
    while True:
        try:
            state = poll_once(state)
        except urllib.error.HTTPError as e:
            log(f"error: {e!r} {e.read()[:300]!r}")
        except Exception as e:  # keep running through network blips
            log(f"error: {e!r}")
        if ONCE:
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
