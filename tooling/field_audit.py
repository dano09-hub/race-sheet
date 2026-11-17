#!/usr/bin/env python3
"""Does the printed share hold for the whole field, not just the pick?

The pick-level audit checks one horse per race. This checks every runner: the
printed chance is bucketed and compared with how often runners in that bucket
actually won, with a Wilson interval on each bucket. Two sources:

  * the card snapshots settlement now stores beside each pick (default), which
    are the page's own totals and shares frozen at the off;
  * the live-archive runner CSV, for an immediate read before snapshots build up.

It changes nothing and writes no page.

    python3 tooling/field_audit.py
    python3 tooling/field_audit.py --csv data/training/runners.csv
"""
import argparse
import glob
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from calibrate_share import wilson  # noqa: E402  (percentages, one definition)

SCALE = 0.04
BANDS = [(0.0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 1.01)]


def from_snapshots(results_dir=None):
    """(probabilities, winner index) for every race with a frozen card."""
    root = results_dir or os.path.join(ROOT, "data", "results")
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        races = data.get("races", []) if isinstance(data, dict) else data
        for row in races:
            if row.get("region") == "US":
                continue
            card = row.get("card") or {}
            runners = card.get("runners") or []
            shares = [(r.get("horse"), r.get("share")) for r in runners
                      if r.get("share") is not None]
            if not shares:
                continue
            names = [h for h, _ in shares]
            winner = row.get("winner")
            if winner not in names:
                continue
            rows.append((np.array([float(p) for _, p in shares]),
                         names.index(winner)))
    return rows


def from_csv(path):
    """(probabilities, winner index) for every race in a runner export."""
    import train_weights as tw
    w = tw.live_weights()
    rows = []
    for race in tw.load_races(path):
        totals = tw.scores(w, race["X"])
        rows.append((tw.softmax(SCALE * np.array(totals)),
                     int(np.argmax(race["y"]))))
    return rows


def audit(rows):
    """Band the printed chances and compare with what actually won."""
    bands = [[0, 0.0, 0] for _ in BANDS]          # runners, sum p, wins
    nll = brier = 0.0
    hits = 0
    for p, win in rows:
        nll -= math.log(max(float(p[win]), 1e-12))
        y = np.zeros(len(p))
        y[win] = 1.0
        brier += float(((p - y) ** 2).sum())
        hits += int(np.argmax(p) == win)
        for i, pi in enumerate(p):
            for b, (lo, hi) in enumerate(BANDS):
                if lo <= pi < hi:
                    bands[b][0] += 1
                    bands[b][1] += float(pi)
                    bands[b][2] += int(i == win)
                    break
    n = len(rows)
    if not n:
        return {"races": 0, "runners": 0}
    band_rows = []
    for (lo, hi), (count, total_p, wins) in zip(BANDS, bands):
        if not count:
            continue
        ilo, ihi = wilson(wins, count)
        band_rows.append({
            "label": f"{int(lo * 100)}-{int(min(hi, 1.0) * 100)}%",
            "runners": count, "stated": 100 * total_p / count,
            "won": 100 * wins / count, "lo": ilo, "hi": ihi,
        })
    runners = sum(len(p) for p, _ in rows)
    return {
        "races": n, "runners": runners, "hits": hits,
        "nll": nll / n, "brier": brier / runners,
        "ece": sum(abs(b["stated"] - b["won"]) * b["runners"] for b in band_rows) / runners,
        "stated": sum(float(p.sum()) for p, _ in rows) / n,
        "rate": hits / n,
        "bands": band_rows,
    }


def rows_for(args):
    if args.csv:
        return from_csv(args.csv), args.csv
    return from_snapshots(), "stored card snapshots"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="runner export to audit instead of snapshots")
    args = ap.parse_args()
    rows, source = rows_for(args)
    a = audit(rows)
    if not a["races"]:
        print(f"no races with a printed share in {source} yet")
        return 0
    print(f"{source}: {a['races']} races, {a['runners']} runners")
    print(f"  pick top-1 {100 * a['rate']:.1f}% | log loss {a['nll']:.4f} | "
          f"Brier {a['brier']:.4f} | ECE {a['ece']:.1f} points")
    print(f"  {'printed':>10} {'runners':>8} {'won':>7} {'95% CI':>15}")
    for b in a["bands"]:
        print(f"  {b['label']:>10} {b['runners']:>8} {b['won']:>6.1f}% "
              f"{b['lo']:>6.1f}-{b['hi']:>5.1f}  (stated {b['stated']:.1f}%)")
    worst = max(a["bands"], key=lambda b: abs(b["stated"] - b["won"]))
    inside = worst["lo"] <= worst["stated"] <= worst["hi"]
    print(f"  widest gap: {worst['label']} stated {worst['stated']:.1f}%, "
          f"won {worst['won']:.1f}% - {'inside' if inside else 'outside'} its interval")
    return 0


if __name__ == "__main__":
    sys.exit(main())
