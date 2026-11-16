#!/usr/bin/env python3
"""Completed US race results from Horse Racing Nation.

The same entries-results page that supplies the US cards also exposes a
payout table once a race is official. The table is ordered by finish, with the
top four in the payout table and the rest under "Also rans". That is enough to
score a pick as win, placed or unplaced without needing a paid data feed.

    python3 tooling/fetch_results_us.py 2026-11-16
"""
import datetime as dt
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import scrape_us  # noqa: E402

BASE = "https://entries.horseracingnation.com"


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def horse_name(value):
    return re.sub(r"\s*\(\d+\*?\)\s*$", "", clean(value)).strip()


def parse_page(page, course):
    """Return completed races from one track page."""
    parts = re.split(r'<a class="race-header" id="race-(\d+)">', page)
    out = []
    for i in range(1, len(parts) - 1, 2):
        number, block = parts[i], parts[i + 1]
        if 'class="table table-hrn table-payouts"' not in block:
            continue
        time_m = re.search(r'<time class="race-time" datetime="([^"]+)"', block)
        if not time_m:
            continue
        iso = time_m.group(1)
        time = iso[11:16]

        dist_m = re.search(r'class="[^"]*race-distance[^"]*">\s*(.*?)\s*</div>', block, re.S)
        race = clean(dist_m.group(1)) if dist_m else f"Race {number}"

        table = re.search(
            r'<table class="table table-hrn table-payouts">(.*?)</table>',
            block, re.S)
        if not table:
            continue
        rows = re.findall(
            r'<tr>\s*<td>\s*(.*?)\s*</td>\s*<td>\s*<img[^>]*alt="(\d+)"[^>]*>.*?</td>\s*'
            r'<td>\s*([^<]*)</td>\s*<td>\s*([^<]*)</td>\s*<td>\s*([^<]*)</td>',
            table.group(1), re.S)
        if not rows:
            continue

        order = []
        for row in rows:
            horse = horse_name(row[0])
            if horse and horse not in order:
                order.append(horse)
        if not order:
            continue

        also = []
        also_m = re.search(r'class="mb-3 race-also-rans">\s*(.*?)</div>', block, re.S)
        if also_m:
            text = clean(also_m.group(1))
            text = re.sub(r"^Also rans:\s*", "", text, flags=re.I)
            also = [x.strip() for x in text.split(",") if x.strip()]

        finish = {h: i + 1 for i, h in enumerate(order)}
        positions = {}
        for h, n in finish.items():
            positions[h] = n

        out.append({
            "course": course,
            "time": time,
            "race": race,
            "winner": order[0],
            "places": order[:3],
            "region": "US",
            "iso": iso,
            "finish": positions,
            "alsoRans": also,
            "field": len(order) + len(also),
        })
    return out


def fetch_day(date):
    """Return every completed US race on an ISO date, in track order."""
    index = scrape_us.get(f"{BASE}/entries-results/{date}")
    slugs = sorted(set(re.findall(
        rf'href="/entries-results/([a-z0-9-]+)/{date}"', index)))
    out = []
    for slug in slugs:
        page = scrape_us.get(f"{BASE}/entries-results/{slug}/{date}")
        if not page:
            continue
        course = slug.replace("-", " ").title()
        out.extend(parse_page(page, course))
    return out


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    rows = fetch_day(date)
    if not rows:
        raise SystemExit(f"no completed US races found for {date}")
    out_dir = os.path.join(ROOT, "data", "results_us")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{date}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    for r in rows:
        print(f"  {r['course']:18} {r['time']} {r['race'][:40]:40} {r['winner']}")
    print(f"wrote {len(rows)} US races -> data/results_us/{date}.json")


if __name__ == "__main__":
    main()
