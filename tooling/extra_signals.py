#!/usr/bin/env python3
"""Add two signals the model never had: going preference and trip suitability.

Both are built from results STRICTLY BEFORE each race (horse state is updated only
after a whole race is written), so they are safe to back-test. Output keeps the
existing factor scores and adds `going` and `trip`.

    python3 tooling/extra_signals.py   -> data/scores_gb.csv.gz
"""
import csv, gzip, os, re, statistics
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "data", "dataset.csv.gz")
OUT = os.path.join(ROOT, "data", "scores_gb.csv.gz")
FORM_PTS = {"1": 10.0, "2": 7.0, "3": 5.0, "4": 3.0, "5": 2.0, "6": 1.0}
FACTORS = ["market", "form", "tips", "fitness", "course", "distance", "connections",
           "going", "trip"]


def num(v, d=None):
    try:
        return float(str(v).strip())
    except Exception:
        return d


def yards(dist):
    m = re.match(r"\s*(\d+)m\s*(\d+)f\s*(\d+)y", str(dist or ""))
    if m:
        return int(m.group(1)) * 1760 + int(m.group(2)) * 220 + int(m.group(3))
    m = re.match(r"\s*(\d+)f\s*(\d+)y", str(dist or ""))
    if m:
        return int(m.group(1)) * 220 + int(m.group(2))
    return None


def going_token(g):
    t = re.sub(r"[^A-Za-z ]", " ", str(g or "")).strip().split()
    return t[0].upper() if t else None


def sp_odds(sp):
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", str(sp or ""))
    if m and float(m.group(2)):
        return 1 + float(m.group(1)) / float(m.group(2))
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*$", str(sp or ""))
    return float(m.group(1)) if m else None


def form_score(s):
    s = str(s or "").strip()
    if not s or s in ("-", "–"):
        return None
    pts, n = 0.0, 0
    for ch in s.replace("/", "")[:6]:
        pts += FORM_PTS.get(ch, 0.0)
        n += 1
    return 100.0 * pts / (10.0 * n) if n else None


def rate(w, r):
    if not r:
        return None
    return 100.0 * (w + 0.5) / (r + 2.0)


def main():
    races = defaultdict(list)
    with gzip.open(SRC, "rt") as f:
        for row in csv.DictReader(f):
            races[(row["date"], row["time"], row["course"], row["race"])].append(row)
    order = sorted(races)
    print(f"{len(order)} races")

    going_hist = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # horse -> going -> [w,r]
    dist_hist = defaultdict(list)                                   # horse -> winning yards

    written = runners = 0
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "course", "time", "race", "horse", "won", "placed", "field"] + FACTORS)
        for key in order:
            rows = races[key]
            today_going = going_token(rows[0].get("going"))
            today_yards = yards(rows[0].get("distance"))
            raw = []
            for r in rows:
                h = r["horse"]
                g = going_hist[h].get(today_going) if today_going else None
                wins = dist_hist[h]
                trip = None
                if today_yards and wins:
                    trip = -abs(today_yards - statistics.median(wins))
                raw.append({
                    "market": (1.0 / sp_odds(r["sp"])) if sp_odds(r["sp"]) else None,
                    "form": form_score(r["form"]),
                    "tips": 100.0 - 100.0 * (num(r["tips_rank"], 99) - 1) / 8.0,
                    "fitness": -num(r["days_since"], 365.0),
                    "course": rate(num(r["course_wins"], 0), num(r["course_runs"], 0)),
                    "distance": rate(num(r["dist_wins"], 0), num(r["dist_runs"], 0)),
                    "connections": 100.0 * ((num(r["j_30_wr"], 0) or 0) * .5 + (num(r["t_30_wr"], 0) or 0) * .5)
                                   if num(r["j_30_wr"]) is not None or num(r["t_30_wr"]) is not None else None,
                    "going": rate(g[0], g[1]) if g else None,
                    "trip": trip,
                })
            cols = {fac: [d[fac] for d in raw if d[fac] is not None] for fac in FACTORS}
            for r, d in zip(rows, raw):
                out = []
                for fac in FACTORS:
                    v, vals = d[fac], cols[fac]
                    if v is None or len(vals) < 2:
                        out.append("")
                    else:
                        lo, hi = min(vals), max(vals)
                        out.append("50.0" if hi == lo else f"{100.0*(v-lo)/(hi-lo):.1f}")
                w.writerow([r["date"], r["course"], r["time"], r["race"], r["horse"],
                            r["won"], r["placed"], r.get("field", len(rows))] + out)
                runners += 1
            # state updates AFTER the race is written - no look-ahead
            for r in rows:
                h = r["horse"]
                won = r["won"] == "1"
                if today_going:
                    cell = going_hist[h][today_going]
                    cell[1] += 1
                    cell[0] += won
                y = today_yards
                if y and won:
                    dist_hist[h].append(y)
            written += 1

    print(f"wrote {written} races / {runners} runners -> data/scores_gb.csv.gz")
    for fac in ("going", "trip"):
        have = sum(1 for _ in ())  # coverage reported below
    print("new columns: going, trip")


if __name__ == "__main__":
    main()
