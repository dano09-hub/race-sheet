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
import html
import json
import os
import re
import sys
import urllib.request

import feed

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "results")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BASE = "https://www.sportinglife.com"


def get(url, tries=3):
    """Fetch a page, retrying with a growing wait, then raise FeedError.

    It used to answer "" for every failure, which is why a blocked feed and a
    quiet day looked identical and a run could finish green with nothing in the
    ledger.
    """
    return feed.get_page(_open, url, tries, label="UK/IRE results")


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def parse_race(page):
    """Return (off_time, race_name, [(pos, horse), ...]) from a result page.

    The page's title carries the off in front of the course ("12:10 Limerick"),
    which is where the stored time comes from. A title with no clock leaves it
    empty rather than guessed.
    """
    m = re.search(r'data-testid="racecard-title"[^>]*>(.*?)</', page, re.S)
    # The page holds text as HTML, so a title or horse can arrive as
    # "Don&#x27;t Hurry Love". Stored decoded, so the ledgers show the name the
    # page renders rather than the entity.
    name = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""
    clock = re.match(r"\s*(\d{1,2}:\d{2})\b", name)
    time = clock.group(1) if clock else ""
    runners = []
    seen = set()
    for chunk in page.split('data-test-id="position-no"')[1:]:
        chunk = chunk[:2000]
        if "saddle-cloth-no" not in chunk:          # forecast/tipping widgets reuse the id
            continue
        pos = re.search(r"Ordinal__OrdinalWrapper[^>]*>(\d+)<", chunk)
        horse = re.search(r'/racing/profiles/horse/\d+">([^<]+)</a>', chunk)
        if pos and horse:
            name_ = html.unescape(horse.group(1)).strip()
            if name_ in seen:
                continue
            seen.add(name_)
            runners.append((int(pos.group(1)), name_))
    runners.sort()
    return time, name, runners


GB_IRE = {"ENG", "GB", "SCO", "WAL", "NI", "IRE", "IE"}   # Sporting Life country codes


def country_of(html):
    m = re.search(r'"short_name":"([A-Z]{2,3})"', html)
    return m.group(1) if m else ""


def fetch_day(date):
    """Return a de-duplicated list of settled races for one ISO date."""
    index = get(f"{BASE}/racing/results/{date}")
    links = sorted(set(re.findall(rf'href="(/racing/results/{date}/[^"]+)"', index)))
    out, seen, failed = [], set(), 0
    for href in links:
        parts = href.strip("/").split("/")
        course = parts[3].replace("-", " ").title() if len(parts) > 3 else ""
        try:
            page = get(BASE + href)
        except feed.FeedError:
            failed += 1
            continue
        cc = country_of(page)
        if cc and cc not in GB_IRE:          # France, USA, South Africa: not our patch
            continue
        time, name, runners = parse_race(page)
        if not runners:
            continue
        key = (course, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"course": course, "time": time, "race": name,
                    "winner": runners[0][1], "places": [h for _, h in runners[:3]]})
    if links and failed == len(links):
        raise feed.FeedError(f"UK/IRE results: every one of {len(links)} meeting "
                             f"pages failed for {date}")
    if failed:
        print(f"  results:     {failed} of {len(links)} meeting pages failed "
              f"for {date}", file=sys.stderr)
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
