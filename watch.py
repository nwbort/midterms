#!/usr/bin/env python3
"""Poll the Guardian's election feeds and push changes to ntfy.

Senate: notifies when a race in SENATE_STATES (the toss-ups) is called, or when
any race is called as a flip.
House: notifies once, when a party reaches a majority (218 seats).

Standard library only. Configuration is via environment variables:

  NTFY_TOPIC     ntfy topic to publish to (required unless DRY_RUN=1)
  NTFY_SERVER    ntfy server base URL (default: https://ntfy.sh)
  NTFY_TOKEN     optional access token for a protected topic
  SENATE_STATES  toss-up states to notify on any call (default: AK,IA,KS,ME,OH,TX)
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
SENATE_URL = (
    "https://interactive.guim.co.uk/2026/11/us-election-data/"
    "production/v1/latest/details/results_S.json"
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


def state_list(name, default):
    return {s.strip().upper() for s in env(name, default).split(",") if s.strip()}


SENATE_STATES = state_list("SENATE_STATES", "AK,IA,KS,ME,OH,TX")
HOUSE_MAJORITY = 218
PARTY_ABBR = {"Dem": "D", "GOP": "R", "Ind": "I", "NP": "I"}
ONCE = env("ONCE") == "1"


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def fetch(url, etag=None):
    """Return (status, etag, data). data is None on 304."""
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
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


def parse_senate(data):
    """Reduce results_S.json to {race_key: race}, where race["winner"] is None until called."""
    races = {}
    for r in data:
        if r.get("level") != "state" or r.get("test"):
            continue
        key = f"{r['statePostal']}-{r.get('seatNum', '')}"
        winner = next((c for c in r.get("candidates", []) if c.get("winner")), None)
        races[key] = {
            "state": r["statePostal"],
            "stateName": r.get("stateName", r["statePostal"]),
            "seatName": r.get("seatName", ""),
            "currentParty": r.get("currentParty"),
            "flippedSeat": r.get("flippedSeat"),
            "winner": None if winner is None else {
                "name": " ".join(x for x in (winner.get("first"), winner.get("last")) if x),
                "party": winner.get("party"),
                "voteShare": winner.get("voteShare"),
                "incumbent": bool(winner.get("incumbent")),
            },
        }
    return races


def is_flip(race):
    """True if the called winner takes the seat from the party that held it."""
    w = race["winner"]
    if w is None:
        return False
    if race["flippedSeat"] is not None:
        return bool(race["flippedSeat"])
    if w["incumbent"]:
        return False
    return w["party"] != race["currentParty"]


def describe_senate(old, new):
    """Return lines for toss-up calls and flips that changed since the last poll."""
    lines = []
    for key, race in new.items():
        prev_race = old.get(key) or {}
        prev, cur = prev_race.get("winner"), race["winner"]
        if prev == cur or (prev and cur and prev["name"] == cur["name"]):
            continue
        name = race["stateName"]
        if race["seatName"] and "Unexpired" in race["seatName"]:
            name += " (special)"
        tossup = race["state"] in SENATE_STATES
        if cur is None:
            if prev and (tossup or prev_race.get("flippedSeat") or is_flip({**prev_race, "winner": prev})):
                lines.append(f"{name}: call for {prev['name']} withdrawn")
            continue
        flip = is_flip(race)
        if not (tossup or flip):
            continue
        party = PARTY_ABBR.get(cur["party"], cur["party"])
        held = PARTY_ABBR.get(race["currentParty"], race["currentParty"])
        outcome = f"FLIP from {held}" if flip else "hold"
        share = f", {cur['voteShare']:.1f}%" if cur.get("voteShare") else ""
        lines.append(f"{name}: {cur['name']} ({party}) wins{share} · {party} {outcome}")
    return lines


def house_called(tally):
    """Return the party name with a House majority, or None."""
    for party, seats in (tally.get("House") or {}).get("parties", {}).items():
        if seats >= HOUSE_MAJORITY:
            return party
    return None


def notify(title, body, tags="ballot_box_with_ballot"):
    if DRY_RUN or not NTFY_TOPIC:
        log(f"[dry run] {title}\n{body}")
        return
    headers = {"Title": title, "Tags": tags, "User-Agent": USER_AGENT}
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


def poll_tracker(state):
    """House tally from the fronts tracker: notify when a party reaches a majority."""
    status, etag, data = fetch(FEED_URL, state.get("etag"))
    if status == 304 or data is None:
        return False
    tally = parse(data)
    if not tally:
        log("tracker returned no chambers; skipping")
        return False
    previous = state.get("tally")
    if previous is None:
        log("first run, baseline: " + " | ".join(describe_changes({}, tally)))
    else:
        winner, before = house_called(tally), house_called(previous)
        if winner and winner != before:
            line = describe_changes({}, {"House": tally["House"]})[0]
            notify(f"House called for the {winner}", line, tags="tada")
            log(f"notified: House called for {winner} · {line}")
    state["etag"], state["tally"] = etag, tally
    return True


def poll_senate(state):
    status, etag, data = fetch(SENATE_URL, state.get("senate_etag"))
    if status == 304 or data is None:
        return False
    races = parse_senate(data)
    if not races:
        log("senate feed returned no races; skipping")
        return False
    previous = state.get("senate")
    if previous is None:
        called = [f"{r['state']} {r['winner']['name']}" for r in races.values() if r["winner"]]
        log(f"senate baseline: {len(races)} races, called: {', '.join(called) or 'none'}")
    else:
        lines = describe_senate(previous, races)
        if lines:
            notify("Senate race called", "\n".join(lines), tags="classical_building")
            log("notified: " + " | ".join(lines))
    state["senate_etag"], state["senate"] = etag, races
    return True


def poll_once(state):
    state = dict(state)
    changed = False
    for poll in (poll_tracker, poll_senate):
        try:
            changed = poll(state) or changed
        except urllib.error.HTTPError as e:
            log(f"{poll.__name__} error: {e!r} {e.read()[:300]!r}")
        except Exception as e:  # keep running through network blips
            log(f"{poll.__name__} error: {e!r}")
    if changed:
        save_state(state)
    return state


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
