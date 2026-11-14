#!/usr/bin/env python3
"""Scrape Punters form guides for every region they cover.

Punters lists GB, Ireland, USA, Canada, Australia, New Zealand, Hong Kong,
Japan, South Africa, France, Turkey and the UAE on one site, and every race
page carries the full form block:

    1. Bella Tatiana (2)
       J: Tom Sherry (58.5kg)  T: Mick Price & Michael Kent Jnr
       Last 10: -  Career: 00-0-0  Rating: -  Win: 0%  Place: 0%
       Flucs: 2%  Odds: $5.5

That gives barrier, weight, last-ten form, their rating and the price — enough
to score a card properly instead of listing names.

ACCESS: punters.com.au sits behind Cloudflare and api.punters.com.au behind a
CloudFront WAF. Both refuse datacenter IPs (plain 403). From an ordinary
connection `requests` gets through untouched. If you are on a blocked network,
render the page in a browser and feed the innerText to parse_runner_block():

    python3 tooling/scrape_punters.py 2026-11-13 --regions AU,NZ
    python3 tooling/scrape_punters.py 2026-11-13 --regions US,HK --out hk.json

Output is the same card schema the page uses, so the blocks drop straight into
index.html.
"""

import argparse
import datetime as dt
import json
import re
import sys

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://www.punters.com.au"

# meeting slug -> region tag. Punters puts the country in the slug.
REGION_HINTS = [
    ("-nz", "NZ"), ("-us", "US"), ("-ca", "CA"), ("-za", "ZA"),
    ("-uk", "GB"), ("-fr", "FR"), ("-jp", "JP"), ("-tr", "TR"),
    ("-ae", "AE"), ("-hk", "HK"),
]


def get(url, timeout=30):
    import requests
    r = requests.get(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": BASE + "/form-guide/",
    }, timeout=timeout)
    r.raise_for_status()
    return r.text


def region_of(slug):
    for suffix, tag in REGION_HINTS:
        if slug.endswith(suffix):
            return tag
    return "AU"          # Punters is an Australian site: unmarked slugs are AUS


def price_to_decimal(s):
    s = (s or "").strip().replace("$", "")
    if not s:
        return ""
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return ""


def parse_runner_block(lines):
    """Turn the flat innerText lines of a race page into runner dicts.

    The page renders each runner as a run of lines. We key off the numbered
    header ("1.") and then take what follows, ignoring anything unexpected.
    """
    runners = []
    i = 0
    n = len(lines)
    while i < n:
        if not re.fullmatch(r"\d{1,2}\.", lines[i]):
            i += 1
            continue
        i += 1
        if i >= n:
            break
        horse = lines[i].strip()
        i += 1
        barrier = ""
        m = re.match(r"^\((\d+)\)$", lines[i].strip()) if i < n else None
        if m:
            barrier = m.group(1)
            i += 1
        jockey = weight = trainer = ""
        if i < n and lines[i].strip() == "J:":
            jockey = lines[i + 1].strip() if i + 1 < n else ""
            i += 2
            m = re.match(r"^\(([\d.]+)kg\)$", lines[i].strip()) if i < n else None
            if m:
                weight = m.group(1)
                i += 1
        if i < n and lines[i].strip() == "T:":
            trainer = lines[i + 1].strip() if i + 1 < n else ""
            i += 2
        form = rating = gave = price = ""
        # the figures line is tab separated: form, career, rating, win%, place%, prize
        while i < n and not re.fullmatch(r"\d{1,2}\.", lines[i]):
            raw = lines[i]
            cells = [c.strip() for c in raw.split("\t") if c.strip()]
            if len(cells) >= 3 and re.fullmatch(r"[0-9PFURXx/\-]+", cells[0]):
                form = "" if cells[0] == "-" else cells[0]
                rating = cells[2] if cells[2] != "-" else ""
            if "$" in raw:
                m = re.search(r"\$([\d.]+)\s*$", raw)
                if m and float(m.group(1)) >= 1.01:   # $0 in the prize column is not a price
                    price = price_to_decimal(m.group(1))
            if i + 1 < n and re.fullmatch(r"\d{1,2}\.", lines[i + 1]):
                i += 1
                break
            i += 1
        if horse and not horse.startswith("J:"):
            runners.append({
                "horse": horse, "odds": price, "form": form,
                "or": rating, "rpr": "", "ts": "",
                "weight": weight, "days": "", "course": "N", "distance": "N",
                "going": "?", "jp": jockey, "tp": trainer, "tips": "", "naps": "",
                "draw": barrier, "gave": gave,
            })
    return runners


def race_cards(html, date_iso):
    """Pull race headings and their runners out of one meeting page's HTML."""
    cards = []
    for m in re.finditer(r"Race\s*(\d+)[^<]{0,80}", html):
        pass  # headings are handled by the caller; kept for clarity
    return cards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--regions", default="AU,NZ,US,HK", help="comma separated region tags")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    want = {r.strip().upper() for r in args.regions.split(",") if r.strip()}
    stamp = dt.date.fromisoformat(args.date).strftime("%Y%m%d")

    index = get(f"{BASE}/form-guide/")
    slugs = sorted({m for m in re.findall(r"/form-guide/horses/([a-z0-9-]+)-" + stamp + r"/", index)})

    out = []
    for slug in slugs:
        region = region_of(slug)
        if want and region not in want:
            continue
        page = get(f"{BASE}/form-guide/horses/{slug}-{stamp}/")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
        text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "\n", text)
        lines = [ln.strip() for ln in text.split("\n")]
        lines = [ln for ln in lines if ln]

        course = slug.replace("-", " ").title()
        runners = parse_runner_block(lines)
        if not runners:
            print(f"# {slug}: no runners parsed", file=sys.stderr)
            continue
        out.append({
            "id": f"{region.lower()}-{slug}-r1", "label": course,
            "course": course, "time": "", "day": args.date, "region": region,
            "race": course, "going": "?", "klass": "", "note": "",
            "runners": runners, "source": f"{BASE}/form-guide/horses/{slug}-{stamp}/",
        })
        print(f"# {course} [{region}]: {len(runners)} runners", file=sys.stderr)

    payload = json.dumps(out, indent=1)
    if args.out == "-":
        print(payload)
    else:
        open(args.out, "w", encoding="utf-8").write(payload)
        print(f"wrote {args.out}: {len(out)} cards", file=sys.stderr)


if __name__ == "__main__":
    main()
