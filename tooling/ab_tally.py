#!/usr/bin/env python3
"""Running tally of the with/without-rating A/B across every saved day.

collect() returns the numbers so the page can render them; main() prints them
and says plainly whether the sample is large enough to conclude anything.

usage: python3 tooling/ab_tally.py
"""
import glob, html, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MIN_RACES = 200
MIN_CHANGED = 20


def norm(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"\(.*?\)", "", s).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def time_key(row):
    """Race time, falling back to the time embedded in the race label."""
    t = str(row.get("time") or "").strip()
    if not t:
        m = re.search(r"(\d{1,2}:\d{2})", str(row.get("race") or ""))
        t = m.group(1) if m else ""
    return t[:5]


def wilson(hits, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d * 100, (c + m) / d * 100)


def decimal_odds(odds):
    """A fractional or decimal price as a decimal, or None when unusable."""
    s = str(odds or "").strip().lower()
    if not s or s in ("sp", "-", "f"):
        return None
    if s in ("evs", "evens"):
        return 2.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        try:
            return 1 + float(m.group(1)) / float(m.group(2))
        except (ValueError, ZeroDivisionError):
            return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*$", s)
    if m:
        v = float(m.group(1))
        return v if v > 1.0 else None
    return None


def card_favourites(date, regions=("GB", "IRE")):
    """Shortest-priced runner in the archived pre-race card, keyed by race."""
    path = os.path.join(ROOT, "data", "cards", "by_date", f"{date}.json")
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cards = []
    if isinstance(data, dict):
        for reg in regions:
            cards += data.get(reg) or []
    else:
        cards = data or []
    out = {}
    for c in cards:
        key = (norm(c.get("course")), str(c.get("time", ""))[:5])
        priced = []
        for r in c.get("runners", []):
            d = decimal_odds(r.get("odds"))
            if d is not None:
                priced.append((d, norm(r.get("horse"))))
        if len(priced) < 2:
            continue
        priced.sort()
        if abs(priced[0][0] - priced[1][0]) < 1e-9:
            continue  # joint favourites: no single market pick
        out[key] = priced[0][1]
    return out


def collect(snap_dir="picks_ab", regions=("GB", "IRE"), subject="rating"):
    out = {"days": [], "pending": [], "races": 0, "with": 0, "without": 0,
           "with_placed": 0, "without_placed": 0,
           "changed": 0, "changed_won": 0, "changed_lost": 0,
           "bench_races": 0, "fav_wins": 0, "pick_hits": 0, "pick_fav": 0,
           "ci_with": (0.0, 0.0), "ci_without": (0.0, 0.0), "subject": subject,
           "enough": False, "verdict": ""}
    snap_dates, day_dates = set(), set()
    for p in sorted(glob.glob(os.path.join(ROOT, "data", snap_dir, "*.json"))):
        date = os.path.basename(p)[:-5]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            continue            # test_/alias snapshots are not whole days
        snap_dates.add(date)
        snap = json.load(open(p, encoding="utf-8"))
        rp = os.path.join(ROOT, "data", "results", f"{date}.json")
        if not os.path.exists(rp):
            continue
        rows = json.load(open(rp, encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("races", [])
        winners = {}
        for r in rows:
            if not r.get("winner"):
                continue
            reg = r.get("region")
            if regions and reg is not None and reg not in regions:
                continue
            winners.setdefault((norm(r.get("course")), time_key(r)), r)
        favs = card_favourites(date, regions=regions)

        n = w = wo = wp = wop = ch = cw = cl = 0
        for x in snap.get("rows", []):
            key = (norm(x.get("course")), time_key(x))
            row = winners.get(key)
            if not row:
                continue
            win = row.get("winner")
            places = [norm(p) for p in (row.get("places") or [])][:3]
            n += 1
            a = norm(x.get("withRating")) == norm(win)
            b = norm(x.get("withoutRating")) == norm(win)
            w += a
            wo += b
            wp += norm(x.get("withRating")) in places
            wop += norm(x.get("withoutRating")) in places
            if x.get("changed"):
                ch += 1
                cw += (a and not b)
                cl += (b and not a)
            fav = favs.get(key)
            pick = row.get("pick")
            if fav and pick:
                out["bench_races"] += 1
                out["fav_wins"] += (fav == norm(win))
                out["pick_hits"] += (norm(pick) == norm(win))
                out["pick_fav"] += (norm(pick) == fav)
        if not n:
            continue
        day_dates.add(date)
        out["days"].append({"date": date, "races": n, "with": w, "without": wo,
                            "with_placed": wp, "without_placed": wop,
                            "changed": ch, "won": cw, "lost": cl})
        out["races"] += n; out["with"] += w; out["without"] += wo
        out["with_placed"] += wp; out["without_placed"] += wop
        out["changed"] += ch; out["changed_won"] += cw; out["changed_lost"] += cl

    out["pending"] = sorted(snap_dates - day_dates)
    if out["races"]:
        out["ci_with"] = wilson(out["with"], out["races"])
        out["ci_without"] = wilson(out["without"], out["races"])
        out["enough"] = (out["races"] >= MIN_RACES and out["changed"] >= MIN_CHANGED)
        lo_w, hi_w = out["ci_with"]
        lo_o, hi_o = out["ci_without"]
        if out["enough"]:
            if lo_w > hi_o:
                out["verdict"] = (f"The {subject}'s interval sits above no-{subject}: it is "
                                  "adding winners, not just changing picks.")
            elif lo_o > hi_w:
                out["verdict"] = (f"The {subject} is hurting: its interval sits below "
                                  f"no-{subject}.")
            else:
                out["verdict"] = ("Large enough sample, no separation: the intervals "
                                  f"overlap, so the {subject} has not shown an edge either way.")
        else:
            need_r = max(0, MIN_RACES - out["races"])
            need_c = max(0, MIN_CHANGED - out["changed"])
            out["verdict"] = (f"Not yet meaningful: needs {need_r} more settled races and "
                              f"{need_c} more races where the {subject} changes the pick.")
    else:
        out["verdict"] = "No settled A/B days yet."
    return out


def main():
    t = collect()
    if not t["days"] and not t["pending"]:
        print("no A/B snapshots yet (run tooling/ab_day.py on a race day)")
        return 1
    print(f"{'date':12} {'races':>5} {'with':>5} {'without':>7} {'changed':>7} {'won':>4} {'lost':>4}")
    for d in t["days"]:
        print(f"{d['date']:12} {d['races']:5} {d['with']:5} {d['without']:7} "
              f"{d['changed']:7} {d['won']:4} {d['lost']:4}")
    print()
    if t["races"]:
        print(f"settled days {len(t['days'])} | races {t['races']}")
        print(f"  with rating:    {t['with']:4} winners "
              f"({100*t['with']/t['races']:5.1f}%, 95% CI "
              f"{t['ci_with'][0]:.1f}-{t['ci_with'][1]:.1f}%)")
        print(f"  without rating: {t['without']:4} winners "
              f"({100*t['without']/t['races']:5.1f}%, 95% CI "
              f"{t['ci_without'][0]:.1f}-{t['ci_without'][1]:.1f}%)")
        print(f"  races where the rating changed the pick: {t['changed']} "
              f"(rating won {t['changed_won']}, lost {t['changed_lost']})")
        if t["bench_races"]:
            b = t["bench_races"]
            print(f"  benchmark:      on the {b} races with a stored pick and a "
                  f"pre-race price, favourite won {t['fav_wins']} "
                  f"({100*t['fav_wins']/b:.1f}%), our pick won {t['pick_hits']} "
                  f"({100*t['pick_hits']/b:.1f}%), we backed the favourite "
                  f"{t['pick_fav']} times")
    if t["pending"]:
        print(f"  waiting for results: {', '.join(t['pending'])}")
    print()
    print(t["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
