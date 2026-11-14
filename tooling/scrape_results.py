#!/usr/bin/env python3
"""Scrape settled race results from horseracing.net into one JSON file per day.

Usage:
    python3 scrape_results.py 01-11-26 12-11-26 [--workers 4]

Each day produces data/YYYY-MM-DD.json containing every meeting, every race and
every runner (finishing position, SP, tips, jockey, trainer, in-running comment).
Days already on disk are skipped, so an interrupted run resumes where it stopped.

Note: the source only archives RESULTS for past dates. Pre-race ratings
(OR / RPR / Topspeed) and form strings are wiped once the meeting is settled,
so they cannot be back-filled -- see build_dataset.py for what is derived instead.
"""

import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request

BASE = "https://www.horseracing.net"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data")
UA = {"User-Agent": "Mozilla/5.0"}


def get(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            if attempt == tries - 1:
                return ""
    return ""


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def parse_meeting(page, course, date):
    races = []
    for chunk in re.split(r'<section data-time="', page)[1:]:
        tm = re.match(r"([0-9:]+)", chunk)
        if not tm:
            continue
        title = re.search(r'class="chase-title">([^<]*)<', chunk)
        summary = {k: clean(v) for k, v in re.findall(
            r'class="chase-text">\s*([^:<]{3,24}?)\s*:\s*([^<]*)</span>', chunk)}
        going = re.search(r"Going ([A-Za-z0-9 ();,.\-/]+?)</span>", chunk)
        dist = re.search(r"Distance ([0-9a-z ]+)</span>", chunk)
        runners = []
        for c in chunk.split('<li class="results-table-row"')[1:]:
            horse = re.search(r'class="runner-title">\s*([^<]+?)\s*</a>', c)
            if not horse:
                continue
            pos = re.search(r'class="number position">\s*(?:<span[^>]*>)?\s*([^<]{1,8}?)\s*<', c)
            sp = re.search(r'raceresults-sp-btn">\s*([^<]*?)\s*</div>', c)
            dec = re.search(r'data-oddsdecimal="([0-9.]+)"', c)
            tips = re.match(r'\s*data-tips="(\d*)"', c)
            jockey = re.search(r'/jockeys/[^"]*">([^<]+)</a>', c)
            trainer = re.search(r'/trainers/[^"]*">([^<]+)</a>', c)
            btn = re.search(r'class="dist-title">\s*([^<]*?)\s*</span>', c)
            comment = re.search(r'class="description-text">\s*([^<]*?)\s*</p>', c)
            runners.append({
                "pos": clean(pos.group(1)) if pos else "",
                "horse": clean(horse.group(1)),
                "sp": clean(sp.group(1)) if sp else "",
                "dec": float(dec.group(1)) if dec else None,
                "tips": int(tips.group(1)) if tips and tips.group(1) else 0,
                "jockey": clean(jockey.group(1)) if jockey else "",
                "trainer": clean(trainer.group(1)) if trainer else "",
                "btn": clean(btn.group(1)) if btn else "",
                "comment": clean(comment.group(1)) if comment else "",
            })
        if not runners:
            continue
        races.append({
            "time": tm.group(1),
            "title": clean(title.group(1)) if title else "",
            "going": clean(going.group(1)) if going else "",
            "distance": clean(dist.group(1)) if dist else "",
            "meta": summary,
            "runners": runners,
        })
    return {"course": course, "date": date, "races": races}


def fetch_day(date):
    """Return (date, [meeting payloads]) after fetching every meeting's results."""
    index = get(f"{BASE}/results/{date}")
    slugs = sorted(set(re.findall(rf'href="/results/([a-z0-9-]+)/{re.escape(date)}', index)))
    out = []
    for slug in slugs:
        page = get(f"{BASE}/results/{slug}/{date}")
        if not page:
            continue
        course = slug.replace("-", " ").title()
        m = parse_meeting(page, course, date)
        if m["races"]:
            out.append(m)
    return date, out


def to_disk(date, meetings):
    os.makedirs(OUT, exist_ok=True)
    iso = dt.datetime.strptime(date, "%d-%m-%y").strftime("%Y-%m-%d")
    path = os.path.join(OUT, f"{iso}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": date, "meetings": meetings}, f, indent=1)
    return path, sum(len(m["races"]) for m in meetings), sum(len(r["runners"]) for m in meetings for r in m["races"])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    workers = 4
    for a in sys.argv[1:]:
        if a.startswith("--workers"):
            workers = int(a.split("=")[1])
    start = dt.datetime.strptime(args[0], "%d-%m-%y")
    end = dt.datetime.strptime(args[1], "%d-%m-%y")
    dates, d = [], start
    while d <= end:
        dates.append(d.strftime("%d-%m-%y"))
        d += dt.timedelta(days=1)

    todo = [x for x in dates if not os.path.exists(os.path.join(
        OUT, dt.datetime.strptime(x, "%d-%m-%y").strftime("%Y-%m-%d") + ".json"))]
    print(f"{len(dates)} days requested, {len(todo)} to fetch, {len(dates)-len(todo)} already on disk", flush=True)

    total_races = total_runners = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for date, meetings in pool.map(fetch_day, todo):
            if not meetings:
                print(f"  {date}: no results", flush=True)
                continue
            path, races, runners = to_disk(date, meetings)
            total_races += races
            total_runners += runners
            print(f"  {date}: {len(meetings)} meetings, {races} races, {runners} runners", flush=True)

    print(f"\nnewly written: {total_races} races / {total_runners} runners")


if __name__ == "__main__":
    main()
