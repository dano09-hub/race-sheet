#!/usr/bin/env python3
"""Capture Racing Post RPR / Topspeed / official rating for one day's card.

Comparison data only: this writes its own file and a CSV of columns beside our
picks. It never edits the cards the model scores, never changes a pick, and is
not imported by refresh.py.

Racing Post answers plain Python with 406; curl_cffi's Chrome impersonation gets
200. If curl_cffi is missing:

    python3 -m pip install curl_cffi

usage:
    python3 tooling/fetch_rp_ratings.py 2026-11-14
"""
import csv
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:
    from curl_cffi import requests as cr
except ImportError:                      # pragma: no cover - environment guard
    cr = None                            # main() explains how to install it


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def blob_of(html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def race_links(date):
    url = f"https://www.racingpost.com/racecards/{date}"
    r = cr.get(url, impersonate="chrome", timeout=30)
    if r.status_code != 200:
        sys.exit(f"{url} -> HTTP {r.status_code}")
    links = sorted(set(re.findall(r"/racecards/\d+/[a-z\-]+/%s/\d+" % re.escape(date), r.text)))
    return ["https://www.racingpost.com" + p for p in links]


def race_data(blob):
    """The race header and its declared runners live side by side in racePage.data."""
    data = (blob.get("props", {}).get("pageProps", {}).get("initialState", {})
            .get("racePage", {}).get("data", {}))
    race, runners = data.get("race") or {}, data.get("runners") or []
    if race and runners and "rpPostmark" in runners[0]:
        return race, runners
    return None, None


def hhmm(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M")
    except (AttributeError, ValueError):
        return ""


def fetch_race(url):
    for attempt in (1, 2):
        try:
            r = cr.get(url, impersonate="chrome", timeout=40)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            blob = blob_of(r.text)
            if not blob:
                raise RuntimeError("no __NEXT_DATA__ blob")
            race, runners = race_data(blob)
            if not race:
                raise RuntimeError("no rated runners on the page")
            return race, runners
        except Exception as e:
            if attempt == 2:
                print(f"  failed {url}: {e}")
                return None
            time.sleep(1.5)
    return None


def main(date):
    if cr is None:
        sys.exit("curl_cffi is required: python3 -m pip install curl_cffi")
    links = race_links(date)
    print(f"racingpost {date}: {len(links)} races on the index")
    races = []
    for i, url in enumerate(links, 1):
        got = fetch_race(url)
        if not got:
            continue
        race, declared = got
        runners = []
        for r in declared:
            runners.append({"horse": r.get("horseName"),
                            "rpr": r.get("rpPostmark"),
                            "ts": r.get("rpTopspeed"),
                            "or": r.get("officialRatingToday"),
                            "nr": bool(r.get("nonRunner")),
                            "days": r.get("daysSinceLastRun"),
                            "jockey": r.get("jockeyName"),
                            "trainer": r.get("trainerName")})
        race_id = re.search(r"/(\d+)$", url)
        races.append({"course": (race.get("courseName") or "").title(),
                      "time": hhmm(race.get("raceTime")),
                      "race_id": int(race_id.group(1)) if race_id else None,
                      "country": race.get("countryCode"),
                      "runners": runners})
        if i % 10 == 0 or i == len(links):
            print(f"  {i}/{len(links)} races")
        time.sleep(0.25)

    out = {"date": date,
           "source": "racingpost.com racecards, via curl_cffi",
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "races": races}
    dest = os.path.join(ROOT, "data", "cards", f"{date}_rp.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    runners = [r for race in races for r in race["runners"] if not r["nr"]]
    print(f"\ncaptured {len(races)} races, {len(runners)} runners")
    for k in ("rpr", "ts", "or"):
        have = sum(1 for r in runners if r.get(k) not in (None, "", 0))
        print(f"  {k.upper():3} {have}/{len(runners)}")
    print(f"  written to {os.path.relpath(dest, ROOT)}")
    compare(date, races)


def compare(date, races):
    """Columns beside our card and pick; nothing here feeds a score."""
    cards_path = os.path.join(ROOT, "data", "cards", f"{date}_two_meetings.json")
    if not os.path.exists(cards_path):
        cards_path = os.path.join(ROOT, "data", "cards", f"{date}_rtv.json")
    if not os.path.exists(cards_path):
        print("no card to compare against")
        return
    cards = json.load(open(cards_path, encoding="utf-8"))
    picks_path = os.path.join(ROOT, "data", "picks_ab", f"test_{date}.json")
    picks = {}
    if os.path.exists(picks_path):
        snap = json.load(open(picks_path, encoding="utf-8"))
        for row in snap.get("rows", []):
            picks[(row.get("course"), str(row.get("time"))[:5])] = row.get("pick")

    rp = {(norm(r["course"]), r["time"]): r for r in races}
    rows, matched_cards, rp_top_agree = [], 0, 0
    for c in cards:
        key = (norm(c["course"]), str(c["time"])[:5])
        src = rp.get(key)
        if not src:
            continue
        matched_cards += 1
        by_horse = {norm(r["horse"]): r for r in src["runners"]}
        pick = picks.get((c["course"], str(c["time"])[:5]))
        def as_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        rated = [r for r in src["runners"] if not r["nr"] and as_int(r.get("rpr"))]
        top = max(rated, key=lambda r: as_int(r["rpr"]), default=None)
        if pick and top and norm(pick) == norm(top["horse"]):
            rp_top_agree += 1
        for r in c["runners"]:
            m = by_horse.get(norm(r["horse"]))
            rows.append({"course": c["course"], "time": c["time"], "horse": r["horse"],
                         "our_pick": "Y" if pick and norm(pick) == norm(r["horse"]) else "",
                         "our_tf": r.get("rpr", ""), "our_ts": r.get("ts", ""),
                         "rp_rpr": (m or {}).get("rpr", "") if m else "",
                         "rp_ts": (m or {}).get("ts", "") if m else "",
                         "rp_or": (m or {}).get("or", "") if m else "",
                         "rp_nr": "Y" if (m or {}).get("nr") else ""})

    pairs, diffs = 0, []
    for row in rows:
        try:
            a, b = int(str(row["our_tf"]).strip()), int(str(row["rp_rpr"]).strip())
        except ValueError:
            continue
        if not a or not b:          # 0 is "no rating", not a rating of zero
            continue
        pairs += 1
        diffs.append(a - b)
    if not rows:
        print("  no races matched our card, so no comparison columns were written")
        return
    dest = os.path.join(ROOT, "data", "picks_ab", f"rp_compare_{date}.csv")
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"\ncompare against {os.path.relpath(cards_path, ROOT)}")
    print(f"  races matched {matched_cards}/{len(cards)} | runners {len(rows)}")
    print("  our Timeform vs Racing Post RPR (two different ratings, not a discrepancy):")
    if pairs:
        print(f"    {pairs} runners comparable | median {statistics.median(diffs):+.0f} pts "
              f"| within 5 pts {sum(1 for d in diffs if abs(d) <= 5)} | "
              f"range {min(diffs):+d}..{max(diffs):+d}")
    if picks:
        print(f"  our pick == RP top-rated (by RPR): {rp_top_agree}/{len(picks)}")
    print(f"  columns written to {os.path.relpath(dest, ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.now().date().isoformat())
