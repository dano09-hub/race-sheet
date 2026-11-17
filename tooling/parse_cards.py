#!/usr/bin/env python3
"""Parse horseracing.net racecard pages into the same shape index.html embeds.

Usage:
    python3 parse_cards.py 13-11-26 cheltenham dundalk sedgefield southwell > out.json
"""

import html
import json
import re
import sys
import urllib.request

BASE = "https://www.horseracing.net"
UA = {"User-Agent": "Mozilla/5.0"}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def _blank_placeholder_odds(runners):
    """Drop the single default price a race carries before its market forms.

    Horseracing.net writes the same price on every runner when a race has no
    market yet (Irish cards have arrived as 4.50 all round). Storing that would
    invent a favourite and a price, so a race with four or more runners and one
    identical price is treated as unpriced.
    """
    prices = {r.get("odds") for r in runners if r.get("odds")}
    if len(runners) >= 4 and len(prices) == 1:
        for r in runners:
            r["odds"] = ""
        return len(runners)
    return 0


def parse_time(t):
    m = re.match(r"(\d{1,2}):(\d{2})", t or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else (t or "")


def parse_course_page(page, course, date):
    cards = []
    for sec in re.split(r'<section data-time="', page)[1:]:
        tm = parse_time(sec)
        title = re.search(r'class="chase-title">([^<]*)<', sec)
        if not title:
            title = re.search(r"<h2[^>]*>([^<]*)</h2>", sec)
        # the summary boxes read: "3yo+, 20 Runners" then
        # "Jumps,Turf , 2m 5f , GOOD, Good to soft in places (GoingStick: 6.1) ..."
        boxes = [clean(x) for x in re.findall(r'class="chase-text">(.*?)</span>', sec, re.S)]
        going = dist = ""
        for b in boxes:
            if " , " in b:
                parts = [p.strip() for p in b.split(" , ")]
                if len(parts) >= 3:
                    dist = parts[1]
                    going = re.sub(r"\s*\((?:GoingStick|Watering|Rail).*$", "", parts[2]).strip()
                break
        klass = re.search(r"Class (\d)", sec)

        runners = []
        for blk in re.split(r'<div data-tip="', sec)[1:]:
            num = re.search(r'data-runnernumber="(\d+)"', blk)
            if not num:
                continue
            # Current markup wraps the horse in a profile link; older pages
            # rendered it as plain text. Accept both.
            name = re.search(r'class="fri-name">\s*<a[^>]*>([^<]+)</a>', blk)
            if not name:
                name = re.search(r'class="fri-name">\s*([^<]+?)(?:\s*<span|</div>)', blk)
            if not name:
                continue
            form = re.search(r'class="fs-form">\s*([^<]*?)\s*</div>', blk)
            odds = re.search(r'data-oddsdecimal="([0-9.]+)"', blk)
            ts = re.search(r'data-topspeed="(\d+)"', blk)
            rating = re.search(r'data-rating="(\d+)"', blk)
            coursew = re.search(r'data-coursewinner="(\d)"', blk)
            distw = re.search(r'data-distancewinner="(\d)"', blk)
            wonlast = re.search(r'data-wonlasttimeout="(\d)"', blk)
            racefit = re.search(r'data-racefit="(\d)"', blk)
            lastran = re.search(r'class="fir-last-ran">\s*([^<]*)<', blk)
            weight = re.search(r'Weight:\s*<span>([^<]*)</span>', blk)
            jt = re.findall(r"class='fi-tl'>([^<]*)<", blk)
            # Read the three figures independently: the card wraps Topspeed in
            # a <span> on some runners, which breaks one combined regex and
            # silently dropped the official rating.
            or_m = re.search(r"\bOR:\s*([0-9-]+)", blk)
            rpr_m = re.search(r"\bRPR:\s*([0-9-]+)", blk)
            ts_m = re.search(r"\bTopspeed:\s*([0-9-]+)", blk)
            tips = re.search(r'class="fti-tips-total"[^>]*>\s*(\d*)', blk)
            naps = re.search(r'class="fs-tip-info"[^>]*>\s*(\d*)', blk)

            def num_or_blank(v):
                # The card writes 0 where it has no figure; 0 is not a rating.
                return v if v and v not in ("-", "0") else ""

            orv = num_or_blank(or_m.group(1)) if or_m else ""
            rprv = num_or_blank(rpr_m.group(1)) if rpr_m else ""
            tsv = num_or_blank(ts_m.group(1)) if ts_m else ""
            # data-rating is the Racing Post Rating (the card's "Sort By RP
            # Rating" field), never the official handicap mark. Only the
            # "OR: ..." text carries the official rating.
            if not rprv and rating:
                rprv = num_or_blank(rating.group(1))
            if not tsv and ts:
                tsv = num_or_blank(ts.group(1))

            oddsv = clean(odds.group(1)) if odds else ""
            if oddsv and float(oddsv) >= 100000:
                oddsv = ""       # 9999999 is the card's "no price yet" sentinel

            runners.append({
                "horse": clean(name.group(1)),
                "form": clean(form.group(1)) if form else "",
                "odds": oddsv,
                "or": orv,
                "rpr": rprv,
                "ts": tsv,
                "weight": clean(weight.group(1)) if weight else "",
                "days": clean(lastran.group(1)) if lastran else "",
                "course": "Y" if coursew and coursew.group(1) == "1" else "N",
                "distance": "Y" if distw and distw.group(1) == "1" else "N",
                # renamed in the page from "Going" to "Won last time out":
                # this attribute is the LTO flag, not a going preference.
                "going": ("Y" if wonlast.group(1) == "1" else "N") if wonlast else "?",
                "jp": clean(jt[0]).replace("%", "") if len(jt) > 0 else "",
                "tp": clean(jt[1]).replace("%", "") if len(jt) > 1 else "",
                "tips": tips.group(1) if tips and tips.group(1) else "",
                "naps": naps.group(1) if naps and naps.group(1) else "",
            })

        if not runners:
            continue
        _blank_placeholder_odds(runners)
        cards.append({
            "id": f"{course.lower().replace(' ', '-')}-{tm.replace(':', '')}",
            "label": f"{course} {tm}",
            "course": course,
            "time": tm,
            "race": clean(title.group(1)) if title else "",
            "going": going,
            "klass": ("Class " + klass.group(1)) if klass else "",
            "note": dist,
            "runners": runners,
        })
    return cards


def main():
    date, courses = sys.argv[1], sys.argv[2:]
    out = []
    for slug in courses:
        page = get(f"{BASE}/{slug}/{date}")
        if not page:
            print(f"# {slug}: nothing", file=sys.stderr)
            continue
        course = slug.replace("-", " ").title()
        cards = parse_course_page(page, course, date)
        print(f"# {course}: {len(cards)} races, {sum(len(c['runners']) for c in cards)} runners", file=sys.stderr)
        out.extend(cards)
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
