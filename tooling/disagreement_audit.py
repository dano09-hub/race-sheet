#!/usr/bin/env python3
"""Does the page's disagreement with the market predict anything?

For every priced runner, the chance the page prints (a softmax at
CALIBRATION_SCALE over the page's own totals) is compared with the market's
de-vigged implied chance. The gap is bucketed in points; each bucket reports its
win rate, its return at SP for a one-unit stake, and whether it beat the market's
own expectation (a Poisson-binomial z on wins against summed market chance).

A bucket with a positive return where the page disagrees with the price is the
only evidence that the sheet adds something to the market. A flat table is the
honest answer that it does not.

usage: python3 tooling/disagreement_audit.py [--csv FILE] [--scale 0.04]
"""
import argparse
import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from calibrate_share import softmax  # noqa: E402  (one softmax, one scale)

SCALE = 0.04
BANDS = ((-100.0, -10.0), (-10.0, -5.0), (-5.0, 0.0),
         (0.0, 5.0), (5.0, 10.0), (10.0, 100.0))
LABELS = ("below -10", "-10 to -5", "-5 to 0", "0 to +5", "+5 to +10", "above +10")


PRICE_BANDS = ((0.0, 5.0), (5.0, 10.0), (10.0, 20.0), (20.0, 40.0), (40.0, 100.0))
SIDES = ((-100.0, -5.0, "page below"), (-5.0, 5.0, "within 5"),
         (5.0, 100.0, "page above"))


def _blank():
    return {"n": 0, "wins": 0, "ours": 0.0, "mkt": 0.0, "ret": 0.0,
            "exp": 0.0, "var": 0.0}


def _add(bucket, ours, mkt, won, odds):
    bucket["n"] += 1
    bucket["wins"] += won
    bucket["ours"] += ours
    bucket["mkt"] += mkt
    bucket["ret"] += (odds - 1.0) if won else -1.0
    p = mkt / 100.0
    bucket["exp"] += p
    bucket["var"] += p * (1.0 - p)


def band_index(gap):
    for i, (lo, hi) in enumerate(BANDS):
        if lo <= gap < hi:
            return i
    return len(BANDS) - 1


def stratified(pairs, bins=50, minimum=30, threshold=0.0):
    """Win rate of page-above against page-below at the same market price.

    Two runners in one price stratum have the same market chance, so the market
    factor cannot explain a difference. Each stratum contributes a difference
    and its variance; they are pooled by inverse variance.
    """
    if not pairs:
        return None
    pairs.sort()
    size = max(1, len(pairs) // bins)
    num = den = 0.0
    used = runners = 0
    for start in range(0, len(pairs), size):
        chunk = pairs[start:start + size]
        above = [w for _, g, w in chunk if g > threshold]
        below = [w for _, g, w in chunk if g < -threshold]
        if len(above) < minimum or len(below) < minimum:
            continue
        pa, pb = sum(above) / len(above), sum(below) / len(below)
        var = pa * (1 - pa) / len(above) + pb * (1 - pb) / len(below)
        if var <= 0:
            continue
        num += (pa - pb) / var
        den += 1 / var
        used += 1
        runners += len(above) + len(below)
    if not den:
        return None
    diff = num / den
    se = math.sqrt(1 / den)
    return {"diff": 100 * diff, "lo": 100 * (diff - 1.96 * se),
            "hi": 100 * (diff + 1.96 * se), "bins": used, "runners": runners}


def audit(path, scale=SCALE, bins=50):
    """Stream a runner file and return the disagreement table."""
    pairs = []
    bands = [_blank() for _ in BANDS]
    cells = {(p[0], s[0]): _blank() for p in PRICE_BANDS for s in SIDES}
    overall = _blank()
    races = dropped = 0
    group, key = [], None

    def flush(rows):
        nonlocal races, dropped
        rows = [r for r in rows if r[0] is not None]
        priced = [r for r in rows if r[1] is not None]
        winners = [r for r in rows if r[2]]
        if len(priced) < 2 or len(winners) != 1 or winners[0][1] is None:
            dropped += 1
            return
        shares = softmax([scale * r[0] for r in rows])
        book = sum(1.0 / r[1] for r in priced)
        if book <= 0:
            dropped += 1
            return
        races += 1
        for r, s in zip(rows, shares):
            if r[1] is None:
                continue
            ours = 100.0 * float(s)
            mkt = 100.0 * (1.0 / r[1]) / book
            won = 1 if r[2] else 0
            _add(overall, ours, mkt, won, r[1])
            gap = ours - mkt
            pairs.append((mkt, gap, won))
            _add(bands[band_index(gap)], ours, mkt, won, r[1])
            for p_lo, p_hi in PRICE_BANDS:
                if p_lo <= mkt < p_hi:
                    for s_lo, s_hi, _ in SIDES:
                        if s_lo <= gap < s_hi:
                            _add(cells[(p_lo, s_lo)], ours, mkt, won, r[1])
                            break
                    break

    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            this = (row.get("race_id") or
                    (row.get("date"), row.get("course"), row.get("time")))
            if key is not None and this != key:
                flush(group)
                group = []
            key = this
            try:
                total = float(row["total"]) if row.get("total") else None
            except ValueError:
                total = None
            try:
                odds = float(row["odds"]) if row.get("odds") else None
            except ValueError:
                odds = None
            if odds is not None and odds <= 1.0:
                odds = None
            group.append((total, odds, row.get("win") == "1",
                          row.get("date") or ""))
    if group:
        flush(group)
    return {"races": races, "dropped": dropped, "bands": bands,
            "cells": cells, "overall": overall, "path": path,
            "scale": scale,
            "stratified": stratified(pairs, bins=bins),
            "strong": stratified(pairs, bins=bins, threshold=10.0)}


def _stats(b):
    if not b["n"]:
        return None
    out = {"n": b["n"], "wins": b["wins"], "rate": 100.0 * b["wins"] / b["n"],
           "ours": b["ours"] / b["n"], "mkt": b["mkt"] / b["n"],
           "roi": 100.0 * b["ret"] / b["n"]}
    out["z"] = ((b["wins"] - b["exp"]) / math.sqrt(b["var"])
                if b["var"] > 0 else 0.0)
    return out


def report(result):
    print(f"{result['path']}: {result['races']} races with two or more prices "
          f"and one winner ({result['dropped']} skipped)")
    print(f"  printed chance: softmax at {result['scale']}, compared with the "
          f"de-vigged market")
    o = _stats(result["overall"])
    if not o:
        print("  no priced runners")
        return
    print(f"  every priced runner: {o['n']} runners, {o['wins']} won "
          f"({o['rate']:.1f}%), market expected {100.0 * sum(b['exp'] for b in [result['overall']]) / o['n']:.1f}%")
    print(f"  return of backing all of them at SP: {o['roi']:+.1f}%")
    print()
    print(f"{'gap (points)':>13} {'runners':>8} {'won':>6} {'market':>7} "
          f"{'ours':>6} {'ROI':>7} {'z':>7}")
    for label, b in zip(LABELS, result["bands"]):
        s = _stats(b)
        if not s:
            print(f"{label:>13} {0:>8}")
            continue
        print(f"{label:>13} {s['n']:>8} {s['rate']:>5.1f}% {s['mkt']:>6.1f}% "
              f"{s['ours']:>5.1f}% {s['roi']:>+6.1f}% {s['z']:>+6.1f}")
    print()
    print("Inside each price band, so the market's own bias cannot explain it:")
    print(f"{'market':>10} {'side':>11} {'runners':>8} {'won':>6} {'exp':>6} "
          f"{'ROI':>7} {'z':>6}")
    for p_lo, p_hi in PRICE_BANDS:
        for s_lo, _s_hi, name in SIDES:
            s = _stats(result["cells"][(p_lo, s_lo)])
            if not s or s["n"] < 100:
                continue
            print(f"{f'{p_lo:.0f}-{p_hi:.0f}%':>10} {name:>11} {s['n']:>8} "
                  f"{s['rate']:>5.1f}% {s['mkt']:>5.1f}% {s['roi']:>+6.1f}% "
                  f"{s['z']:>+5.1f}")
    for name, st in (("any disagreement", result.get("stratified")),
                     ("10+ points of disagreement", result.get("strong"))):
        if st:
            print()
            print(f"At the same market price, {name} ({st['runners']} runners, "
                  f"{st['bins']} strata): page-above minus page-below = "
                  f"{st['diff']:+.2f} points (95% CI {st['lo']:+.2f} to "
                  f"{st['hi']:+.2f}).")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(
        os.path.dirname(HERE), "data", "training", "runners.csv"))
    ap.add_argument("--scale", type=float, default=SCALE)
    ap.add_argument("--bins", type=int, default=50,
                    help="price strata for the matched comparison")
    args = ap.parse_args(argv)
    if not os.path.exists(args.csv):
        print(f"no runner file at {args.csv}: run tooling/export_training.py first")
        return 1
    report(audit(args.csv, scale=args.scale, bins=args.bins))
    return 0


if __name__ == "__main__":
    sys.exit(main())
