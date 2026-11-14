#!/usr/bin/env python3
"""Compare our current cards with FormFav's free form feed.

This is comparison data only. It writes a side-by-side CSV and the raw FormFav
response, but never edits a card, changes a score, or changes a pick.

    export FORMFAV_API_KEY=...
    python3 tooling/fetch_formfav.py 2026-11-14

FormFav's free tier covers runner form and career stats. Its date window is 30
days in the past to 7 days ahead, so this is for current cards only - it is not a
historical odds source and cannot rebuild the post-April-2024 Betfair report.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = "https://api.formfav.com/v1"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
COUNTRIES = {"gb": "Europe/London", "ie": "Europe/Dublin"}


class FormFavError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def local_hhmm(iso, tz):
    """FormFav startTime is UTC; compare it with our local racecard clock."""
    try:
        value = str(iso or "").replace("Z", "+00:00")
        when = dt.datetime.fromisoformat(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return when.astimezone(ZoneInfo(tz)).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def api_get(path, params, key):
    """Small stdlib client. The API key is sent only in the header."""
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-API-Key": key,
        "User-Agent": UA,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise FormFavError("FormFav rejected FORMFAV_API_KEY (HTTP 401).", e.code)
        if e.code == 403:
            raise FormFavError("that data is not available on the free FormFav tier.", e.code)
        if e.code == 429:
            retry = (e.headers or {}).get("Retry-After")
            extra = (" Retry after %s seconds." % retry) if retry else ""
            raise FormFavError("FormFav rate limit reached; try again later." + extra, e.code)
        if e.code in (400, 404):
            raise FormFavError("FormFav race/date not found (HTTP %d)." % e.code, e.code)
        raise FormFavError("FormFav returned HTTP %d." % e.code, e.code)
    except urllib.error.URLError as e:
        raise FormFavError("FormFav network error: %s" % e.reason)
    except (OSError, ValueError) as e:
        raise FormFavError("FormFav request failed: %s" % e)


def api_key():
    for name in ("FORMFAV_API_KEY", "FORM_FAV"):
        key = os.environ.get(name, "").strip()
        if key:
            return key
    path = os.path.join(ROOT, ".formfav_key")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def load_cards(path):
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    cards = []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                cards.extend(value)
    elif isinstance(payload, list):
        cards = payload
    return [c for c in cards if str(c.get("region", "GB")).upper() == "GB"]


def match_cards(cards, meetings):
    """Match on normalised course and local clock; return pairs and leftovers."""
    index = {}
    for meeting in meetings or []:
        country = str(meeting.get("country", "")).lower()
        tz = COUNTRIES.get(country)
        if not tz:
            continue
        for race in meeting.get("races") or []:
            hhmm = local_hhmm(race.get("startTime"), tz)
            if not hhmm:
                continue
            key = (norm(meeting.get("track")), hhmm)
            index[key] = None if key in index else (meeting, race)

    pairs, unmatched = [], []
    for card in cards or []:
        key = (norm(card.get("course")), str(card.get("time", ""))[:5])
        hit = index.get(key)
        if hit:
            pairs.append((card, hit[0], hit[1]))
        else:
            unmatched.append(card)
    return pairs, unmatched


def load_picks(date):
    picks = {}
    for name in ("test_%s.json" % date, "%s.json" % date):
        path = os.path.join(ROOT, "data", "picks_ab", name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, ValueError):
            continue
        rows = snap.get("rows", snap) if isinstance(snap, dict) else snap
        for row in rows or []:
            if isinstance(row, dict):
                key = (norm(row.get("course")), str(row.get("time", ""))[:5])
                picks[key] = row.get("pick")
        break
    return picks


def stat(stats, section, field):
    value = (stats or {}).get(section) or {}
    if not isinstance(value, dict):
        return ""
    return value.get(field, "")


def compare(cards, date, key, getter=api_get, delay=0.0):
    meetings = getter("/form/meetings", {
        "date": date,
        "race_code": "gallops",
        "timezone": "Europe/London",
    }, key)
    pairs, unmatched = match_cards(cards, meetings.get("meetings") or [])
    picks = load_picks(date)
    rows, raw_races = [], []
    failed = 0

    for card, meeting, race in pairs:
        country = str(meeting.get("country", "")).lower()
        params = {
            "date": date,
            "track": meeting.get("slug"),
            "race": race.get("raceNumber"),
            "race_code": "gallops",
            "country": country,
            "timezone": COUNTRIES.get(country, "Europe/London"),
        }
        try:
            form = getter("/form", params, key)
        except FormFavError as e:
            if e.status in (400, 404):
                failed += 1
                print("  formfav: skipped %s %s (%s)" %
                      (card.get("course"), card.get("time"), e), file=sys.stderr)
                continue
            raise
        if delay:
            time.sleep(delay)

        by_horse = {norm(r.get("name")): r for r in form.get("runners") or []}
        pick = picks.get((norm(card.get("course")), str(card.get("time", ""))[:5]))
        for runner in card.get("runners") or []:
            source = by_horse.get(norm(runner.get("horse")))
            if not source:
                continue
            stats = source.get("stats") or {}
            rows.append({
                "date": date,
                "course": card.get("course", ""),
                "time": card.get("time", ""),
                "race": card.get("race", ""),
                "horse": runner.get("horse", ""),
                "our_pick": "Y" if pick and norm(pick) == norm(runner.get("horse")) else "",
                "our_form": runner.get("form", ""),
                "formfav_form": source.get("form", ""),
                "formfav_last20": source.get("last20Starts", ""),
                "our_or": runner.get("or", ""),
                "formfav_age": source.get("age", ""),
                "formfav_weight": source.get("weight", ""),
                "formfav_jockey": source.get("jockey", ""),
                "formfav_trainer": source.get("trainer", ""),
                "formfav_starts": stat(stats, "overall", "starts"),
                "formfav_wins": stat(stats, "overall", "wins"),
                "formfav_places": stat(stats, "overall", "places"),
                "formfav_win_pct": stat(stats, "overall", "winPercent"),
                "formfav_place_pct": stat(stats, "overall", "placePercent"),
                "formfav_track_place_pct": stat(stats, "track", "placePercent"),
                "formfav_distance_place_pct": stat(stats, "distance", "placePercent"),
            })
        raw_races.append({"course": card.get("course", ""),
                          "time": card.get("time", ""),
                          "race": card.get("race", ""),
                          "formfav": form})

    rows.sort(key=lambda r: (r["course"], r["time"], r["horse"]))
    os.makedirs(os.path.join(ROOT, "data", "cards"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "data", "picks_ab"), exist_ok=True)
    raw_path = os.path.join(ROOT, "data", "cards", f"{date}_formfav.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump({"date": date,
                   "source": "formfav.com /v1/form (free tier)",
                   "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "races": raw_races,
                   "unmatched_cards": [{"course": c.get("course"), "time": c.get("time")}
                                       for c in unmatched]}, f, ensure_ascii=False, indent=1)

    csv_path = ""
    if rows:
        csv_path = os.path.join(ROOT, "data", "picks_ab", f"formfav_compare_{date}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    return {"ok": True, "date": date, "races": len(raw_races),
            "runners": len(rows), "unmatched": len(unmatched), "failed": failed,
            "csv": csv_path, "raw": raw_path}


def collect_file(cards_path, date, key=None, getter=api_get, delay=0.0):
    key = (key or api_key()).strip()
    if not key:
        return {"ok": False, "reason": "FORMFAV_API_KEY not set"}
    cards = [c for c in load_cards(cards_path) if str(c.get("day", date))[:10] == date]
    return compare(cards, date, key, getter=getter, delay=delay)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date", nargs="?", default=dt.date.today().isoformat())
    ap.add_argument("--cards", default=None,
                    help="card snapshot; defaults to data/cards/<date>.json")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="seconds between form requests (default 1.0)")
    args = ap.parse_args()
    path = args.cards or os.path.join(ROOT, "data", "cards", f"{args.date}.json")
    if not os.path.exists(path):
        print("no cards at %s" % path, file=sys.stderr)
        return 2
    try:
        result = collect_file(path, args.date, delay=args.sleep)
    except FormFavError as e:
        print("FormFav: %s" % e, file=sys.stderr)
        return 2
    if not result.get("ok"):
        print("FormFav: %s" % result.get("reason"), file=sys.stderr)
        return 2
    print("FormFav %s: %d races, %d runners; %d unmatched cards, %d skipped races"
          % (args.date, result["races"], result["runners"], result["unmatched"], result["failed"]))
    print("  raw: %s" % os.path.relpath(result["raw"], ROOT))
    if result["csv"]:
        print("  csv: %s" % os.path.relpath(result["csv"], ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
