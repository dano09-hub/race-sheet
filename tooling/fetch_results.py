#!/usr/bin/env python3
"""Pull yesterday's settled results into data/results/<date>.json.

The free tier at horseracingapi.org covers US + GB + IRE + FR + SA for *yesterday*,
50 calls a day, with a key you create yourself. Put it in the environment:

    export AHR_KEY=...
    python3 tooling/fetch_results.py            # yesterday
    python3 tooling/fetch_results.py 2026-11-12

You can also convert a payload you saved by hand, which needs no key:

    python3 tooling/fetch_results.py --import saved.json 2026-11-12

Output shape is what measure.py expects:
    {"Course": {"Race 1": {"winner": "Horse", "places": ["Horse", ...]}}}
"""
import datetime as dt
import json
import os
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = "https://api.apihorseracing.com/v1/results"
OUT = os.path.join(ROOT, "data", "results")


def key():
    return os.environ.get("AHR_KEY") or os.environ.get("HORSERACINGAPI_KEY") or ""


def fetch(date):
    k = key()
    if not k:
        raise SystemExit(
            "no API key: set AHR_KEY (free key at horseracingapi.org), "
            "or use --import with a saved payload"
        )
    req = urllib.request.Request(
        f"{BASE}/{date}",
        headers={"X-API-Key": k, "Accept": "application/json",
                 "User-Agent": "race-sheet/1.0"},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def pick(d, *names, default=None):
    for n in names:
        if isinstance(d, dict) and d.get(n) not in (None, ""):
            return d[n]
    return default


def normalise(payload):
    """Tolerant: API shapes drift, so accept a few plausible layouts."""
    out = {}
    races = pick(payload, "results", "races", "data", default=payload)
    if isinstance(races, dict):
        races = pick(races, "results", "races", "data", default=[])
    for race in races if isinstance(races, list) else []:
        course = pick(race, "course", "track", "venue", default="Unknown")
        no = pick(race, "race_number", "raceNumber", "race", "number", default="")
        label = f"Race {no}" if str(no).isdigit() else str(no)
        runners = pick(race, "runners", "results", "finishers", default=[]) or []
        if runners and isinstance(runners[0], dict):
            ordered = sorted(
                runners,
                key=lambda r: pick(r, "finish_position", "finishPosition",
                                   "position", "place", default=99) or 99,
            )
            names = [pick(r, "horse", "name", "horse_name", default="") for r in ordered]
            names = [n for n in names if n]
            if names:
                out.setdefault(course, {})[label] = {
                    "winner": names[0],
                    "places": names[:3],
                }
    return out


def save(date, payload):
    data = normalise(payload)
    if not data:
        raise SystemExit("payload normalised to nothing - shape not recognised")
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    n = sum(len(v) for v in data.values())
    print(f"wrote {n} races across {len(data)} courses -> data/results/{date}.json")
    return path


def main():
    args = sys.argv[1:]
    if args and args[0] == "--import":
        payload = json.load(open(args[1], encoding="utf-8"))
        date = args[2] if len(args) > 2 else dt.date.today().isoformat()
        save(date, payload)
        return
    date = args[0] if args else (dt.date.today() - dt.timedelta(days=1)).isoformat()
    save(date, fetch(date))


if __name__ == "__main__":
    main()
