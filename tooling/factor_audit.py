#!/usr/bin/env python3
"""Which factor carries the page's disagreement with the price?

For each of the twelve factors, runners are put in a fixed market-price band and
a score band, then the top score band is compared with the bottom one inside the
same price band. Holding the price fixed removes the market's own bias, so a
factor that is genuinely informative shows a positive difference and a factor the
market has already priced - or one that misleads - shows zero or a negative one.

usage: python3 tooling/factor_audit.py [--csv FILE]
"""
import argparse
import bisect
import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from train_weights import KEYS  # noqa: E402  (one list of factors)

PRICE_EDGES = (0.0, 2.0, 5.0, 10.0, 20.0, 30.0, 40.0, 60.0, 100.0)
SCORE_EDGES = (0.0, 20.0, 40.0, 60.0, 80.0, 100.0)
MIN_CELL = 100


def band(edges, value):
    if value is None or value != value:
        return None
    i = bisect.bisect_right(edges, value) - 1
    return max(0, min(i, len(edges) - 2))


def collect(path):
    cells = {k: [[[0, 0] for _ in SCORE_EDGES[:-1]]
                 for _ in PRICE_EDGES[:-1]] for k in KEYS}
    seen = {k: 0 for k in KEYS}
    races = 0
    group, key = [], None

    def flush(rows):
        nonlocal races
        winners = [r for r in rows if r[2] and r[1] is not None]
        priced = [r for r in rows if r[1] is not None]
        if len(priced) < 2 or len(winners) != 1:
            return
        book = sum(1.0 / r[1] for r in priced)
        if book <= 0:
            return
        races += 1
        for row in rows:
            if row[1] is None:
                continue
            mkt = 100.0 * (1.0 / row[1]) / book
            pb = band(PRICE_EDGES, mkt)
            for i, value in enumerate(row[3]):
                if value is None:
                    continue
                seen[KEYS[i]] += 1
                sb = band(SCORE_EDGES, value)
                if sb is None:
                    continue
                cell = cells[KEYS[i]][pb][sb]
                cell[0] += 1
                cell[1] += 1 if row[2] else 0

    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            this = (row.get("race_id") or
                    (row.get("date"), row.get("course"), row.get("time")))
            if key is not None and this != key:
                flush(group)
                group = []
            key = this
            try:
                odds = float(row["odds"]) if row.get("odds") else None
            except ValueError:
                odds = None
            if odds is not None and odds <= 1.0:
                odds = None
            values = []
            for k in KEYS:
                try:
                    values.append(float(row[k]) if row.get(k) not in (None, "", "None")
                                  else None)
                except ValueError:
                    values.append(None)
            group.append((row.get("date") or "", odds, row.get("win") == "1", values))
    if group:
        flush(group)
    return {"cells": cells, "seen": seen, "races": races, "path": path}


def compare(cells):
    """Pooled top-band minus bottom-band win rate inside each price band."""
    num = den = 0.0
    runners = bands = 0
    for pb in range(min(len(PRICE_EDGES) - 1, len(cells))):
        low = cells[pb][0]
        high = cells[pb][-1]
        if low[0] < MIN_CELL or high[0] < MIN_CELL:
            continue
        p_low = low[1] / low[0]
        p_high = high[1] / high[0]
        var = p_low * (1 - p_low) / low[0] + p_high * (1 - p_high) / high[0]
        if var <= 0:
            continue
        num += (p_high - p_low) / var
        den += 1 / var
        runners += low[0] + high[0]
        bands += 1
    if not den:
        return None
    diff = num / den
    se = math.sqrt(1 / den)
    return {"diff": 100 * diff, "lo": 100 * (diff - 1.96 * se),
            "hi": 100 * (diff + 1.96 * se), "runners": runners, "bands": bands}


def report(result):
    print(f"{result['path']}: {result['races']} priced races with one winner")
    print(f"  top score band (80-100) minus bottom (0-20), inside the same "
          f"market-price band")
    print()
    print(f"{'factor':>13} {'runners':>10} {'bands':>6} {'top-bottom':>11} "
          f"{'95% CI':>18} {'seen':>9}")
    rows = []
    for k in KEYS:
        rows.append((k, compare(result["cells"][k])))
    for k, s in sorted(rows, key=lambda x: (x[1] is None,
                                            -(abs(x[1]["diff"]) if x[1] else 0))):
        if not s:
            print(f"{k:>13} {'—':>10} {'—':>6} {'too few extremes':>28} "
                  f"{result['seen'][k]:>9}")
            continue
        print(f"{k:>13} {s['runners']:>10} {s['bands']:>6} "
              f"{s['diff']:>+10.2f}  {s['lo']:>+7.2f} to {s['hi']:>+6.2f} "
              f"{result['seen'][k]:>9}")
    print("\n  A factor is only measured where both extreme score bands hold "
          f"{MIN_CELL}+ runners in the same price band; the rest need more"
          " history or a finer design.")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(
        os.path.dirname(HERE), "data", "training", "runners.csv"))
    args = ap.parse_args(argv)
    if not os.path.exists(args.csv):
        print(f"no runner file at {args.csv}")
        return 1
    report(collect(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
