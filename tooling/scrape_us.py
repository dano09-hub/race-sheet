#!/usr/bin/env python3
"""Scrape US entries from entries.horseracingnation.com.

Usage:
    python3 scrape_us.py 2026-11-12 > us.json

Each race carries the post time as an ISO UTC timestamp, the surface/distance
line and a runner table (program number, horse, HRN speed figure, trainer,
jockey, morning line). US cards do not publish OR/RPR/Topspeed, so those fields
are left blank and the model scores them on price and speed figure only.
"""

import html
import json
import re
import sys
import urllib.request

BASE = "https://entries.horseracingnation.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def to_decimal(ml):
    ml = (ml or "").strip()
    if not ml:
        return ""
    m = re.match(r"(\d+)\s*/\s*(\d+)", ml)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return f"{a / b + 1:.2f}"
    m = re.match(r"(\d+(?:\.\d+)?)", ml)
    return m.group(1) if m else ""


def parse_track(page, course, date):
    cards = []
    blocks = re.split(r'class="race-header"', page)[1:]
    for blk in blocks:
        hdr = re.match(r"[^>]*>(.*?)</a>", blk, re.S)
        iso = re.search(r'class="race-time"\s+datetime="([^"]+)"', blk)
        num = re.search(r"Race\s*#?\s*(\d+)", clean(hdr.group(1)) if hdr else "")
        if not iso or not num:
            continue
        dist = re.search(r'race-distance">\s*(.*?)\s*</div>', blk, re.S)
        table = re.search(r'<table class="[^"]*table-entries[^"]*">(.*?)</table>', blk, re.S)
        if not table:
            continue
        runners = []
        for row in re.split(r"<tr[^>]*>", table.group(1))[1:]:
            horse = re.search(r'class="horse-link"[^>]*>([^<]+)</a>', row)
            if not horse:
                continue
            fig = re.search(r'<span class="small">\s*\((\d+)\)', row)
            tj = re.findall(r'data-label="Trainer / Jockey">\s*(.*?)</td>', row, re.S)
            trainer = jockey = ""
            if tj:
                ps = re.findall(r"<p>(.*?)</p>", tj[0], re.S)
                if len(ps) > 0:
                    trainer = clean(ps[0])
                if len(ps) > 1:
                    jockey = clean(ps[1])
            pp = re.search(r'data-label="Post Position">\s*(\d+)', row)
            ml = re.search(r'data-label="Morning Line Odds">\s*<p>([^<]*)</p>', row)
            scratch = re.search(r'data-label="Scratched\?"[^>]*>\s*(.*?)</td>', row, re.S)
            scratched = bool(scratch and clean(scratch.group(1)))
            runners.append({
                "horse": clean(horse.group(1)),
                "odds": to_decimal(ml.group(1)) if ml else "",
                "form": "", "or": "", "rpr": "",
                "ts": fig.group(1) if fig else "",
                "weight": "", "days": "",
                "course": "N", "distance": "N", "going": "?",
                "draw": pp.group(1) if pp else "",
                "jp": jockey, "tp": trainer, "tips": "", "naps": "",
                "trainer": trainer, "jockey": jockey, "scratched": scratched,
            })
        runners = [r for r in runners if not r["scratched"]]
        if not runners:
            continue
        iso_s = iso.group(1)
        cards.append({
            "id": f"us-{course.lower().replace(' ', '-')}-r{num.group(1)}",
            "label": f"Race {num.group(1)}",
            "course": course,
            "time": iso_s[11:16],
            "iso": iso_s,
            "race": clean(dist.group(1)) if dist else f"Race {num.group(1)}",
            "going": "", "klass": clean(dist.group(1)) if dist else "", "note": "",
            "region": "US",
            "runners": runners,
        })
    return cards


def main():
    date = sys.argv[1]
    iso = date
    day = get(f"{BASE}/entries-results/{iso}")
    slugs = sorted(set(re.findall(rf'href="/entries-results/([a-z0-9-]+)/{iso}"', day)))
    out = []
    for slug in slugs:
        page = get(f"{BASE}/entries-results/{slug}/{iso}")
        course = slug.replace("-", " ").title()
        cards = parse_track(page, course, iso)
        if cards:
            print(f"# {course}: {len(cards)} races, {sum(len(c['runners']) for c in cards)} runners", file=sys.stderr)
            out.extend(cards)
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
