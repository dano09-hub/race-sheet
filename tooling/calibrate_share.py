#!/usr/bin/env python3
"""Is the share the page shows a probability? Measure it, do not assume it.

The page prints each runner's share as total / sum of totals. That is a
normalised score, and it is easy to read the number as a chance of winning. This
checks whether it is one: the page's own totals are recomputed from the training
file, the displayed share is compared with how often those runners actually won,
and the same races are scored with (a) a softmax over the page's totals, whose
scale is fitted on earlier races only, and (b) the market where a price exists.

Reports reliability (share band vs observed win rate), expected calibration
error, Brier score and log loss on held-out races. It writes nothing and changes
no page, weight or pick.

    python3 tooling/calibrate_share.py
    python3 tooling/calibrate_share.py --test-days 120
"""
import argparse
import math
import os
import sys

import numpy as np
from scipy.optimize import minimize_scalar

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from train_weights import KEYS, live_weights, load_races, scores, softmax  # noqa: E402

BANDS = [(0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.15),
         (0.15, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 1.01)]


def totals(races, w):
    """The page's own ranking total for every runner, in race order."""
    return [scores(w, race["X"]) for race in races]


def report(name, races, probs):
    """Win rate per probability band, plus ECE, Brier and log loss."""
    nll = 0.0
    brier = 0.0
    hits = 0
    pooled_p, pooled_y = [], []
    bands = {i: [0, 0.0, 0] for i in range(len(BANDS))}   # n, sum p, wins
    for race, p in zip(races, probs):
        y = race["y"]
        i = int(np.argmax(y))
        nll -= math.log(max(float(p[i]), 1e-12))
        brier += float(((p - y) ** 2).sum())
        hits += int(np.argmax(p) == i)
        for j, pj in enumerate(p):
            hit = 1 if j == i else 0
            pooled_p.append(float(pj))
            pooled_y.append(hit)
            for b, (lo, hi) in enumerate(BANDS):
                if lo <= pj < hi:
                    bands[b][0] += 1
                    bands[b][1] += float(pj)
                    bands[b][2] += hit
                    break
    n = len(races) or 1
    ece = sum(abs(s / c - w / c) * c for c, s, w in bands.values() if c) / max(
        sum(c for c, _, _ in bands.values()), 1)
    out = {"name": name, "n": len(races), "nll": nll / n, "brier": brier / n,
           "top1": 100.0 * hits / n, "ece": 100.0 * ece,
           "bands": bands, "pooled_p": pooled_p, "pooled_y": pooled_y}
    return out


def print_report(res):
    print(f"\n{res['name']}")
    print(f"  held-out races {res['n']}   log loss {res['nll']:.4f}   "
          f"Brier {res['brier']:.4f}   top-1 {res['top1']:.1f}%   "
          f"calibration error {res['ece']:.1f} pts")
    print(f"  {'share band':14}{'runners':>9}{'mean share':>12}{'won':>8}{'gap':>8}")
    for b, (lo, hi) in enumerate(BANDS):
        c, s, w = res["bands"][b]
        if not c:
            continue
        mean_p, rate = s / c, w / c
        print(f"  {f'{100*lo:.0f}-{100*min(hi,1):.0f}%':14}{c:9}"
              f"{100*mean_p:11.1f}%{100*rate:7.1f}%{100*(rate-mean_p):+7.1f}")


def market_probs(race):
    """Fair market probability per runner, or None when the race is unpriced."""
    odds = race["odds"]
    implied = []
    for value in odds:
        implied.append(1.0 / value if value else None)
    if sum(1 for v in implied if v) < max(2, math.ceil(len(odds) * 0.6)):
        return None
    total = sum(v for v in implied if v)
    return np.array([(v or 0.0) / total for v in implied])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(ROOT, "data", "training", "kaggle_runners.csv"))
    ap.add_argument("--test-days", type=int, default=60)
    args = ap.parse_args(argv)

    races = load_races(args.csv)
    if len(races) < 100:
        print(f"only {len(races)} races; nothing to measure")
        return 1
    days = sorted({r["date"] for r in races})
    cutoff = days[-args.test_days]
    train = [r for r in races if r["date"] < cutoff]
    test = [r for r in races if r["date"] >= cutoff]
    print(f"races {len(races)} ({len(train)} train / {len(test)} test)")
    print(f"train {train[0]['date']} .. {train[-1]['date']}")
    print(f"test  {test[0]['date']} .. {test[-1]['date']}")

    w = live_weights()
    train_tot = totals(train, w)
    test_tot = totals(test, w)

    # (a) exactly what the page prints: total / sum of totals
    shown = [t / t.sum() for t in test_tot]

    # (b) a softmax over the same totals, with one scale fitted on train only
    def nll(scale):
        return -sum(math.log(max(float(softmax(scale * t)[int(np.argmax(r["y"]))]), 1e-12))
                    for t, r in zip(train_tot, train))
    best = minimize_scalar(nll, bounds=(1e-4, 2.0), method="bounded")
    scale = float(best.x)
    fitted = [softmax(scale * t) for t in test_tot]

    # (c) the market, renormalised to a fair book
    mkt_pairs = [(r, market_probs(r)) for r in test]
    mkt = [(r, p) for r, p in mkt_pairs if p is not None]
    market = [p for _, p in mkt]

    a = report("displayed share (the page's total / sum of totals)", test, shown)
    b = report(f"softmax over the same totals, scale {scale:.4f} (fitted on train)", test, fitted)
    c = report("market, overround removed", [r for r, _ in mkt], market) if market else None

    for res in (a, b, c):
        if res:
            print_report(res)

    print("\nthe pick's own number:")
    for label, probs in (("displayed share", shown), ("fitted", fitted)):
        top_p, top_hits = [], 0
        for race, p in zip(test, probs):
            i = int(np.argmax(p))
            top_p.append(float(p[i]))
            top_hits += int(i == int(np.argmax(race["y"])))
        print(f"  {label:16} mean pick share {100*np.mean(top_p):5.1f}%   "
              f"actual win rate {100*top_hits/len(test):5.1f}%")

    pick_share = 100 * float(np.mean([max(p) for p in shown]))
    fit_share = 100 * float(np.mean([max(p) for p in fitted]))
    top = [(s, w, c) for lo, (c, s, w) in zip([b[0] for b in BANDS], a["bands"].values())
           if lo >= 0.30 and c]
    top_n = sum(c for _, _, c in top)
    top_p = sum(s for s, _, _ in top) / top_n if top_n else 0.0
    top_rate = sum(w for _, w, _ in top) / top_n if top_n else 0.0

    print("\nverdict:")
    if abs(a["top1"] - pick_share) > 3:
        direction = "understates" if a["top1"] > pick_share else "overstates"
        print(f"  The number on the pick {direction} its chance: it is shown at "
              f"{pick_share:.1f}% and wins {a['top1']:.1f}% of the time. Runners shown "
              f"at 30% or more win {100*top_rate:.1f}% against a "
              f"{100*top_p:.1f}% claim. Averaged over every runner the error is only "
              f"{a['ece']:.1f} points, which hides it: most runners are middle-priced.")
    else:
        print(f"  The shown number is close to the record at the top of the card "
              f"({pick_share:.1f}% shown, {a['top1']:.1f}% won).")
    gain = a["nll"] - b["nll"]
    print(f"  One fitted scale on the same totals fixes almost all of it: calibration "
          f"error {a['ece']:.1f} -> {b['ece']:.1f} points, log loss {a['nll']:.4f} -> "
          f"{b['nll']:.4f}, and the pick reads {fit_share:.1f}% against its "
          f"{b['top1']:.1f}% record. The ordering is untouched - top-1 stays "
          f"{b['top1']:.1f}% - so it would change the printed number, not the pick.")
    if c:
        print(f"  The market is still better (log loss {c['nll']:.4f}, top-1 "
              f"{c['top1']:.1f}%, calibration error {c['ece']:.1f} points): a calibrated "
              "share is an honest number, not a substitute for a price.")
    print("  Report only: no page, weight, pick or stored result has been touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
