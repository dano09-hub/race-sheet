#!/usr/bin/env python3
"""Pull settled results from Sporting Life, which serves them as real HTML.

Racing Post gates its results table (0 rows reachable) and Equibase forbids
automated access; Sporting Life renders `data-test-id="position-no"` blocks
server-side, so this is the one free route that works without a browser.

    python3 tooling/fetch_results_sl.py            # today
    python3 tooling/fetch_results_sl.py 2026-11-13

Writes data/results/<date>.json:
    [{"course": "...", "time": "...", "race": "...", "winner": "...", "places": [...]}]
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "results")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BASE = "https://www.sportinglife.com"


def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            continue
    return ""


def parse_race(html):
    """Return (race_name, [(pos, horse), ...]) from a Sporting Life result page."""
    m = re.search(r'data-testid="racecard-title"[^>]*>(.*?)</', html, re.S)
    name = re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
    runners = []
    seen = set()
    for chunk in html.split('data-test-id="position-no"')[1:]:
        chunk = chunk[:2000]
        if "saddle-cloth-no" not in chunk:          # forecast/tipping widgets reuse the id
            continue
        pos = re.search(r"Ordinal__OrdinalWrapper[^>]*>(\d+)<", chunk)
        horse = re.search(r'/racing/profiles/horse/\d+">([^<]+)</a>', chunk)
        if pos and horse:
            name_ = horse.group(1).strip()
            if name_ in seen:
                continue
            seen.add(name_)
            runners.append((int(pos.group(1)), name_))
    runners.sort()
    return name, runners


GB_IRE = {"ENG", "GB", "SCO", "WAL", "NI", "IRE", "IE"}   # Sporting Life country codes


def country_of(html):
    m = re.search(r'"short_name":"([A-Z]{2,3})"', html)
    return m.group(1) if m else ""


def fetch_day(date):
    """Return a de-duplicated list of settled races for one ISO date."""
    index = get(f"{BASE}/racing/results/{date}")
    if not index:
        return []
    links = sorted(set(re.findall(rf'href="(/racing/results/{date}/[^"]+)"', index)))
    out, seen = [], set()
    for href in links:
        parts = href.strip("/").split("/")
        course = parts[3].replace("-", " ").title() if len(parts) > 3 else ""
        page = get(BASE + href)
        cc = country_of(page)
        if cc and cc not in GB_IRE:          # France, USA, South Africa: not our patch
            continue
        name, runners = parse_race(page)
        if not runners:
            continue
        key = (course, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"course": course, "time": "", "race": name,
                    "winner": runners[0][1], "places": [h for _, h in runners[:3]]})
    return out


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    rows = fetch_day(date)
    if not rows:
        raise SystemExit(f"no settled GB/IRE races found for {date}")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{date}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f)
    for r in rows:
        print(f"  {r['course']:22} {r['winner']}")
    print(f"wrote {len(rows)} races -> data/results/{date}.json")


if __name__ == "__main__":
    main()
