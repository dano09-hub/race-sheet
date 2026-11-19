#!/usr/bin/env python3
"""Replay the bet-slip rule on settled days and record how each slip finished.

The slip tab is win-only: one leg per race, the model's top pick, a card price
and a model chance above the market's. This module rebuilds that leg pool from
the archived pre-race card for each settled day, keeps only races whose stored
result names the same pick, and settles the standard slip structures at the
archived card prices.

Nothing is re-scored after the result: the pick in the archive is the pick the
page showed at the off. A race without a stored result, or one whose stored
pick differs from the rebuilt pick, is left out rather than guessed. That is
why a quiet day can carry only one or two slips.

usage:
    python3 tooling/slip_results.py            # print the JSON summary
    python3 tooling/slip_results.py --write    # replace the page's data block
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import settle  # noqa: E402

START = "/* SLIP_RESULTS_START */"
END = "/* SLIP_RESULTS_END */"
SCREEN_START = "/* SCREEN_RESULTS_START */"
SCREEN_END = "/* SCREEN_RESULTS_END */"
SCREENS = ("longshots", "overhyped", "placebankers")

# The named structures, in the order the live tab generates them. `kind` decides
# which combinations the slip actually stakes: a straight multiple stakes one
# bet, every lucky/full-cover slip stakes every non-empty combination.
LADDER = [
    ("Single", "single", 1),
    ("Double", "straight", 2),
    ("Treble", "straight", 3),
    ("Four-fold accumulator", "straight", 4),
    ("Lucky 15", "cover", 4),
    ("Lucky 31", "cover", 5),
    ("Lucky 63", "cover", 6),
    ("Trixie", "cover", 3),
    ("Patent", "cover", 3),
    ("Yankee", "cover", 4),
    ("Canadian", "cover", 5),
    ("Heinz", "cover", 6),
    ("Super Heinz", "cover", 7),
    ("Goliath", "cover", 8),
]


def _result_index(rows):
    """Look up a settled row by course and pick, with the off time when known."""
    by_pick = {}
    for r in rows:
        pick = r.get("pick")
        if not pick:
            continue
        by_pick.setdefault((r.get("course"), pick), []).append(r)
    return by_pick


def _result_for(card, horse, by_pick):
    """The settled row for this exact card pick, or None.

    Course and horse must both match. When both sides carry an off time it must
    match too, so two races at one meeting cannot settle the wrong leg.
    """
    cands = by_pick.get((card.get("course"), horse)) or []
    for r in cands:
        rt, ct = str(r.get("time") or "").strip(), str(card.get("time") or "").strip()
        if rt and ct and rt != ct:
            continue
        if str(r.get("pick") or "") != horse:
            continue
        return r
    return None


def _finished(r):
    """A stored win/loss is required; a blank pick_finish is not settled."""
    if isinstance(r.get("hit"), bool):
        return True
    finish = str(r.get("pick_finish") or "").strip()
    return bool(finish)


def _won(r):
    if isinstance(r.get("hit"), bool):
        return r["hit"]
    return str(r.get("pick_finish") or "").strip().lower() == "1st"


def _combos(kind, n):
    if kind == "single":
        return [(0,)]
    if kind == "straight":
        return [tuple(range(n))]
    out = []
    for size in range(1, n + 1):
        out.extend(itertools.combinations(range(n), size))
    return out


def _slip_rows(legs):
    """Every slip the live tab would generate from this leg pool."""
    rows, no = [], 0
    for name, kind, need in LADDER:
        if len(legs) < need:
            continue
        # The live tab generates three singles; the ladder row covers each.
        if name == "Single":
            for i in range(min(3, len(legs))):
                no += 1
                rows.append({"y": name, "n": no, "legs": [i],
                             "b": 1, "wb": 1 if legs[i]["w"] else 0,
                             "ret": round(legs[i]["p"], 4) if legs[i]["w"] else 0.0})
            continue
        no += 1
        chosen = list(range(need))
        combos = _combos(kind, need)
        wb, ret = 0, 0.0
        for combo in combos:
            if all(legs[i]["w"] for i in combo):
                wb += 1
                prod = 1.0
                for i in combo:
                    prod *= legs[i]["p"]
                ret += prod
        status = "won" if wb == len(combos) else ("lost" if wb == 0 else "part")
        rows.append({"y": name, "n": no, "legs": chosen,
                     "b": len(combos), "wb": wb, "ret": round(ret, 4),
                     "o": status})
    return rows


def day_block(date):
    """One settled day's value legs and the slips built from them."""
    rpath = os.path.join(ROOT, "data", "results", f"{date}.json")
    cpath = os.path.join(ROOT, "data", "cards", "by_date", f"{date}.json")
    if not (os.path.exists(rpath) and os.path.exists(cpath)):
        return None
    try:
        data = json.load(open(rpath, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = data.get("races", []) if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        return None
    cards = settle._cards_for(date)
    if not cards:
        return None
    picks = settle.score_cards(cards)
    by_pick = _result_index(rows)

    legs = []
    for card, p in zip(cards, picks):
        if card.get("day") and card.get("day") != date:
            continue
        scored = p.get("runners") or []
        if not scored:
            continue
        top = scored[0]
        horse = top.get("horse") or ""
        dec, share, implied = top.get("dec"), top.get("share"), top.get("implied")
        if not horse or dec is None or share is None or implied is None:
            continue
        if dec <= 1.0 or share <= implied:
            continue
        r = _result_for(card, horse, by_pick)
        if not r or not _finished(r):
            continue
        legs.append({
            "c": card.get("course") or "",
            "t": card.get("time") or "",
            "h": horse,
            "r": "US" if str(card.get("region") or "").upper() == "US" else "GB/IRE",
            "p": round(float(dec), 4),
            "f": str(r.get("pick_finish") or "").strip(),
            "w": 1 if _won(r) else 0,
            "e": round(float(share - implied), 4),
        })
    if not legs:
        return None
    legs.sort(key=lambda x: -x["e"])
    seen, uniq = set(), []
    for x in legs:
        key = (x["c"], x["t"], x["h"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(x)
    legs = uniq[:8]
    slips = _slip_rows(legs)
    if not slips:
        return None
    return {"d": date, "l": legs, "s": slips}


def build():
    """Every settled day that has at least one replayable slip, newest first."""
    import glob
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json")),
                       reverse=True):
        date = os.path.basename(path)[:-5]
        block = day_block(date)
        if block:
            out.append(block)
    return out


def _screen_day(date):
    """Replay the three bet screens on one settled day from its archived card.

    The rules match the live tabs exactly: longshots need a price of 8+, a
    top-four model rank, a 4%+ win chance and a positive edge; overhyped needs
    the market favourite the model ranks third or worse, or rates five points
    below the price; place bankers need model #1 and market #1 with a 20%+ win
    chance in a field of five or more. A row enters only when the stored result
    names the same pick as the archived card.
    """
    rpath = os.path.join(ROOT, "data", "results", f"{date}.json")
    cpath = os.path.join(ROOT, "data", "cards", "by_date", f"{date}.json")
    if not (os.path.exists(rpath) and os.path.exists(cpath)):
        return None
    try:
        data = json.load(open(rpath, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = data.get("races", []) if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        return None
    cards = settle._cards_for(date)
    if not cards:
        return None
    picks = settle.score_cards(cards)
    by_pick = _result_index(rows)
    out = {k: [] for k in SCREENS}
    for card, p in zip(cards, picks):
        if card.get("day") and card.get("day") != date:
            continue
        scored = p.get("runners") or []
        if not scored:
            continue
        field = len(card.get("runners") or [])
        for x in scored:
            horse = x.get("horse") or ""
            dec, share, implied = x.get("dec"), x.get("share"), x.get("implied")
            if not horse or dec is None or share is None or implied is None:
                continue
            edge = share - implied
            r = _result_for(card, horse, by_pick)
            if not r or not _finished(r):
                continue
            region = "US" if str(card.get("region") or "").upper() == "US" else "GB/IRE"
            base = {"c": card.get("course") or "", "t": card.get("time") or "",
                    "h": horse, "r": region, "p": round(float(dec), 4),
                    "f": str(r.get("pick_finish") or "").strip(),
                    "w": 1 if _won(r) else 0}
            if dec >= 8 and (x.get("rank") or 99) <= 4 and share >= 0.04 and edge > 0:
                out["longshots"].append(dict(base))
            if x.get("mktRank") == 1 and ((x.get("rank") or 99) >= 3 or edge <= -0.05):
                out["overhyped"].append(dict(
                    base, k=x.get("rank"), e=round(float(edge), 4),
                    pl=1 if r.get("placed") is True else 0))
            if (x.get("rank") == 1 and field >= 5 and share >= 0.20
                    and x.get("mktRank") == 1):
                out["placebankers"].append(dict(
                    base, pl=1 if r.get("placed") is True else 0,
                    pp=int(r.get("place_places") or 0),
                    pf=float(r.get("place_fraction") or 0.0),
                    us=1 if region == "US" else 0))
    if not any(out.values()):
        return None
    out["longshots"].sort(key=lambda x: (-x["p"], x["c"], x["t"]))
    out["overhyped"].sort(key=lambda x: (x["e"], x["c"], x["t"]))
    out["placebankers"].sort(key=lambda x: (x["c"], x["t"]))
    for key in SCREENS:
        seen, uniq = set(), []
        for x in out[key]:
            k = (x["c"], x["t"], x["h"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        out[key] = uniq
    return out


def build_screens():
    """Every settled day that has at least one replayable screen pick."""
    import glob
    out = {k: [] for k in SCREENS}
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json")),
                       reverse=True):
        date = os.path.basename(path)[:-5]
        day = _screen_day(date)
        if not day:
            continue
        for key in SCREENS:
            if day[key]:
                out[key].append({"d": date, "l": day[key]})
    return out


def render_block(days=None):
    days = build() if days is None else days
    if not days:
        return START + "\n  var SLIP_RESULTS = [];\n  " + END
    payload = json.dumps(days, separators=(",", ":"), ensure_ascii=False)
    return START + "\n  var SLIP_RESULTS = " + payload + ";\n  " + END


def render_screen_block(data=None):
    data = build_screens() if data is None else data
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return SCREEN_START + "\n  var SCREEN_RESULTS = " + payload + ";\n  " + SCREEN_END


def _replace(html, start, end, block):
    if start not in html or end not in html:
        return html
    head, rest = html.split(start, 1)
    _, tail = rest.split(end, 1)
    return head + block + tail


def replace_block(html, days=None):
    """Replace the page's data blocks in memory, leaving every other byte."""
    html = _replace(html, START, END, render_block(days))
    return _replace(html, SCREEN_START, SCREEN_END, render_screen_block())


def write_page(days=None):
    page_path = os.path.join(ROOT, "index.html")
    html = open(page_path, encoding="utf-8").read()
    if START not in html or END not in html:
        raise SystemExit("slip results markers are missing from index.html")
    if SCREEN_START not in html or SCREEN_END not in html:
        raise SystemExit("screen results markers are missing from index.html")
    # Build the whole page before opening it: a scoring failure must not leave
    # a truncated index.html behind.
    out = replace_block(html, days)
    open(page_path, "w", encoding="utf-8").write(out)
    return len(build() if days is None else days)


def main(argv):
    if "--write" in argv:
        n = write_page()
        screens = build_screens()
        print(f"slip results: wrote {n} settled days")
        print("screen results: " + ", ".join(
            f"{k} {len(v)} days" for k, v in screens.items()))
        return 0
    days = build()
    print(json.dumps(days, indent=1)[:4000])
    print(f"slip results: {len(days)} settled days")
    screens = build_screens()
    print("screen results: " + ", ".join(
        f"{k} {len(v)} days" for k, v in screens.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
