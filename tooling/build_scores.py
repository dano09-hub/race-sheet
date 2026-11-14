#!/usr/bin/env python3
"""Rebuild factor scores for every settled British race in data/dataset.csv.gz.

The dataset stores raw features (SP, tips rank, days off, strike rates, form), not
the page's own outputs, so this reconstructs the 0-100 per-race scores the model
would have given each runner. Output feeds weight tuning.

    python3 tooling/build_scores.py            -> data/scores_gb.csv.gz
"""
import csv, gzip, os, re, sys
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "data", "dataset.csv.gz")
OUT = os.path.join(ROOT, "data", "scores_gb.csv.gz")

FORM_PTS = {"1": 10.0, "2": 7.0, "3": 5.0, "4": 3.0, "5": 2.0, "6": 1.0}


def num(v, default=None):
    try:
        return float(str(v).strip())
    except Exception:
        return default


def dec_odds(sp, dec):
    # NOTE: the `dec` column is unusable - median 13.0, max 99999, and it tracks the
    # result. Using it leaked winners into the scores (market-only "won" 52%). Parse SP only.
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


def rate(wins, runs):
    w, r = num(wins, 0.0), num(runs, 0.0)
    if not r:
        return None
    return 100.0 * (w + 0.5) / (r + 2.0)      # shrunk, so 1-from-1 isn't a god


def raw(row):
    return {
        "market": dec_odds(row.get("sp"), row.get("dec")),
        "form": form_score(row.get("form")),
        "tips": 100.0 - 100.0 * (num(row.get("tips_rank"), 99) - 1) / 8.0,
        "fitness": -num(row.get("days_since"), 365.0),
        "course": rate(row.get("course_wins"), row.get("course_runs")),
        "distance": rate(row.get("dist_wins"), row.get("dist_runs")),
        "connections": None if num(row.get("j_30_wr")) is None and num(row.get("t_30_wr")) is None
                       else 100.0 * ((num(row.get("j_30_wr"), 0) or 0) * 0.5 + (num(row.get("t_30_wr"), 0) or 0) * 0.5),
        "price_inv": None,
    }


def main():
    races = defaultdict(list)
    with gzip.open(SRC, "rt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["date"], row["course"], row["time"], row["race"])
            races[key].append(row)
    print(f"{len(races)} races, {sum(len(v) for v in races.values())} runners")

    factors = ["market", "form", "tips", "fitness", "course", "distance", "connections"]
    written = runners = 0
    cover = defaultdict(int)
    with gzip.open(OUT, "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "course", "time", "race", "horse", "won", "placed", "field"]
                   + factors + ["f_" + x for x in factors])
        for (date, course, time, race), rows in races.items():
            raws = []
            for row in rows:
                d = raw(row)
                d["price_inv"] = (1.0 / d["market"]) if d["market"] else None
                d["market"] = d["price_inv"]
                raws.append(d)
            cols = {}
            for fac in factors:
                vals = [r[fac] for r in raws if r[fac] is not None]
                cols[fac] = vals
                cover[fac] += len(vals)
            for row, d in zip(rows, raws):
                scores = []
                for fac in factors:
                    v = d[fac]
                    vals = cols[fac]
                    if v is None or len(vals) < 2:
                        scores.append("")
                        continue
                    lo, hi = min(vals), max(vals)
                    scores.append("50.0" if hi == lo else f"{100.0 * (v - lo) / (hi - lo):.1f}")
                w.writerow([date, course, time, race, row["horse"], row["won"], row["placed"],
                            row.get("field", len(rows))] + scores)
                runners += 1
            written += 1

    print(f"wrote {written} races / {runners} runners -> data/scores_gb.csv.gz")
    for fac in factors:
        print(f"  {fac:12} present {cover[fac]:6}/{runners} ({100*cover[fac]/runners:.0f}%)")
    print("size:", os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
