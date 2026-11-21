#!/usr/bin/env python3
"""File the irishracing racecard for a meeting the daily card feeds missed.

The refresh archives the cards it fetches -- GB from horseracing.net, France
from PMU, the US from its own feed.  irishracing's fast-results also carries
meetings those feeds do not list (Meydan on 20 November 2026 was one), so the
settled rows for that meeting had no archived card under the day and the
results audit flagged every one of them.  irishracing still serves the racecard
for the day, so the meeting can be filed under the day it ran with the runners
the page names.

The card is read after the off, so it is archived with no price and no
rating: the field is named and the six inputs stay blank.  Nothing can be
scored, picked or settled from it -- it only says the card was kept.

usage:
    python3 tooling/fetch_cards_ir.py 2026-11-20 Meydan          # print
    python3 tooling/fetch_cards_ir.py 2026-11-20 Meydan --write  # archive
"""
import argparse
import html
import json
import os
import re
import sys

import feed
from fetch_results_ir import BASE, _open, day_slug

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ARCHIVE = os.path.join(ROOT, "data", "cards", "by_date")
REGION = "AE"            # the country code Punters and Smarkets also use


def get(url):
    return feed.get_page(_open, url, 3, label="irishracing racecard")


def _name(chunk):
    """The runner's name from its card row."""
    m = re.search(r"<strong>([^<]+)</strong></a>", chunk)
    if not m:
        m = re.search(r"data-title='([^'<]*)", chunk)
    return html.unescape(m.group(1)).strip() if m else ""


def _runner(chunk):
    """One runner, with only what the card proves: name, form, weight, rating."""
    name = _name(chunk)
    if not name:
        return None
    form = re.search(r'class="[^"]*\bform\b[^"]*">([^<]*)</div>', chunk)
    body = re.search(r"</a>\s*<strong>\d+,([^<]*)</strong>", chunk)
    weight = re.search(r"(\d+-\d+)", body.group(1)) if body else None
    rating = re.search(r"Rated (\d+)</span>", chunk)
    return {"horse": name, "odds": "",
            "form": (form.group(1).strip() if form else ""),
            "or": (rating.group(1) if rating else ""),
            "rpr": "", "ts": "", "weight": (weight.group(1) if weight else ""),
            "days": "", "course": "", "distance": "", "going": "",
            "jp": "", "tp": "", "tips": "", "naps": ""}


def card(iso, course, clock, title, page):
    """One race's card from its racecard page."""
    runners = [r for r in (_runner(c) for c in
                           page.split('class="row runner-line')[1:]) if r]
    if not runners:
        return None
    return {"id": f"{course.lower().replace(' ', '-')}-{clock}",
            "label": title, "course": course, "time": f"{clock[:2]}:{clock[2:]}",
            "race": title, "going": "", "klass": "", "note": "",
            "runners": runners, "day": iso, "region": REGION}


def meeting(iso, course):
    """Every race on the meeting's card, in off order."""
    slug = day_slug(iso)
    path = re.sub(r"[^A-Za-z0-9]+", "-", course).strip("-")   # "Abu Dhabi"
    index = get(f"{BASE}/racecards/{slug}/{path}")
    listed = re.findall(rf'href="/racecards/{re.escape(slug)}/{re.escape(path)}/'
                        r'(\d{4})">Race \d+ - (.*?)</a>', index)
    out = []
    for clock, raw in listed:
        title = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        page = get(f"{BASE}/racecards/{slug}/{path}/{clock}")
        got = card(iso, course, clock, title, page)
        if got:
            out.append(got)
    return out


def archive(iso, meeting):
    """Merge the meeting into the day's by-date archive under its region."""
    path = os.path.join(ARCHIVE, iso + ".json")
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a region map")
    bucket = data.setdefault(REGION, [])
    by_key = {(c.get("course"), str(c.get("time") or "")[:5]): i
              for i, c in enumerate(bucket)}
    filed = 0
    for c in meeting:
        key = (c["course"], c["time"])
        if key in by_key:
            bucket[by_key[key]] = c
        else:
            by_key[key] = len(bucket)
            bucket.append(c)
            filed += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path, filed


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("date", help="the day the meeting ran, YYYY-MM-DD")
    ap.add_argument("course", help="the course as irishracing names it, e.g. Meydan")
    ap.add_argument("--write", action="store_true", help="merge into the by-date archive")
    args = ap.parse_args(argv)

    got = meeting(args.date, args.course)
    if not got:
        raise SystemExit(f"no {args.course} racecard found for {args.date}")
    for c in got:
        print(f"  {c['time']}  {c['race']}  ({len(c['runners'])} runners)")
    if not args.write:
        return 0
    path, filed = archive(args.date, got)
    print(f"{len(got)} races -> {os.path.relpath(path, ROOT)} "
          f"({filed} new, {REGION})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
