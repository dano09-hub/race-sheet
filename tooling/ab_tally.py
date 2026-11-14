#!/usr/bin/env python3
"""Running tally of the with/without-rating A/B across every saved day.

collect() returns the numbers so the page can render them; main() prints them
and says plainly whether the sample is large enough to conclude anything.

usage: python3 tooling/ab_tally.py
"""
import glob, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MIN_RACES = 200
MIN_CHANGED = 20


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def wilson(hits, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d * 100, (c + m) / d * 100)


def collect():
    out = {"days": [], "pending": [], "races": 0, "with": 0, "without": 0,
           "changed": 0, "changed_won": 0, "changed_lost": 0,
           "ci_with": (0.0, 0.0), "ci_without": (0.0, 0.0),
           "enough": False, "verdict": ""}
    snap_dates, day_dates = set(), set()
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "picks_ab", "*.json"))):
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
            if r.get("winner"):
                winners.setdefault((norm(r.get("course")), str(r.get("time", ""))[:5]), r["winner"])

        n = w = wo = ch = cw = cl = 0
        for x in snap.get("rows", []):
            key = (norm(x.get("course")), str(x.get("time", ""))[:5])
            win = winners.get(key)
            if not win:
                continue
            n += 1
            a = x.get("withRating") == win
            b = x.get("withoutRating") == win
            w += a
            wo += b
            if x.get("changed"):
                ch += 1
                cw += (a and not b)
                cl += (b and not a)
        if not n:
            continue
        day_dates.add(date)
        out["days"].append({"date": date, "races": n, "with": w, "without": wo,
                            "changed": ch, "won": cw, "lost": cl})
        out["races"] += n; out["with"] += w; out["without"] += wo
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
                out["verdict"] = ("The rating's interval sits above no-rating: it is "
                                  "adding winners, not just changing picks.")
            elif lo_o > hi_w:
                out["verdict"] = "The rating is hurting: its interval sits below no-rating."
            else:
                out["verdict"] = ("Large enough sample, no separation: the intervals "
                                  "overlap, so the rating has not shown an edge either way.")
        else:
            need_r = max(0, MIN_RACES - out["races"])
            need_c = max(0, MIN_CHANGED - out["changed"])
            out["verdict"] = (f"Not yet meaningful: needs {need_r} more settled races and "
                              f"{need_c} more races where the rating changes the pick.")
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
    if t["pending"]:
        print(f"  waiting for results: {', '.join(t['pending'])}")
    print()
    print(t["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
