#!/usr/bin/env python3
"""Chronological holdout tune: fit weights on early races, report on later ones.

    python3 tooling/tune_weights.py

Splits data/scores_gb.csv.gz by date (first 70% of days train, last 30% holdout),
grid-searches the weights on TRAIN only, then reports the winner on HOLDOUT next to
the current production weights. Nothing is shipped from here automatically.
"""
import csv, gzip, itertools, os, sys
from collections import defaultdict

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "data", "scores_gb.csv.gz")
FACTORS = ["market", "form", "tips", "fitness", "course", "distance", "connections"]
CURRENT = {"market": 18, "tips": 12, "form": 8, "connections": 6, "course": 4, "distance": 3, "fitness": 3}


def load():
    races = defaultdict(list)
    with gzip.open(SRC, "rt") as f:
        for r in csv.DictReader(f):
            races[(r["date"], r["course"], r["time"], r["race"])].append(r)
    out = []
    for key in sorted(races):
        rows = races[key]
        win = [i for i, r in enumerate(rows) if r["won"] == "1"]
        if not win:
            continue
        m = np.full((len(rows), len(FACTORS)), np.nan)
        for i, r in enumerate(rows):
            for j, fac in enumerate(FACTORS):
                v = r.get(fac)
                if v not in ("", None):
                    m[i, j] = float(v)
        out.append((key[0], m, win[0]))
    return out


def evaluate(races, w):
    """Rank only runners that actually have the factors, and skip races too thin to judge.

    Blank factors must NOT become a neutral 50: that let a runner with no data beat a
    runner with poor data, and it was the real source of the earlier absurd rates.
    """
    wv = np.array([w.get(f, 0) for f in FACTORS], dtype=float)
    hit = top3 = n = 0
    for _, m, win in races:
        num = np.nansum(m * wv, axis=1)
        den = np.nansum((~np.isnan(m)) * wv, axis=1)
        have = den > 0
        idx = np.where(have)[0]
        if len(idx) < 2 or not have[win]:
            continue                      # unjudgeable race: coverage too thin
        score = np.where(have, num / np.where(have, den, 1), -1e9)
        order = idx[np.argsort(-score[idx])]
        n += 1
        hit += int(order[0] == win)
        top3 += int(win in order[:3])
    if not n:
        return (0.0, 0.0)
    return (100.0 * hit / n, 100.0 * top3 / n)


def main():
    races = load()
    days = sorted({d for d, _, _ in races})
    cut = days[int(len(days) * 0.7)]
    train = [r for r in races if r[0] < cut]
    hold = [r for r in races if r[0] >= cut]
    print(f"{len(races)} races | train {len(train)} (to {cut}) | holdout {len(hold)}")

    grid = itertools.product([10, 15, 20, 25], [0, 5, 10], [0, 5, 10], [0, 3, 6])
    best, best_score = None, -1
    for market, tips, form, conn in grid:
        w = {"market": market, "tips": tips, "form": form, "connections": conn,
             "course": 4, "distance": 3, "fitness": 3}
        won, t3 = evaluate(train, w)
        if won > best_score:
            best, best_score = (w, won, t3), won
    w, tw, tt3 = best
    print(f"\nbest on TRAIN: {w}\n  train won {tw:.1f}% top3 {tt3:.1f}%")
    hw, ht3 = evaluate(hold, w)
    print(f"  HOLDOUT won {hw:.1f}% top3 {ht3:.1f}%")
    cw, ct3 = evaluate(hold, CURRENT)
    print(f"current weights HOLDOUT won {cw:.1f}% top3 {ct3:.1f}%")
    chw, cht3 = evaluate(train, CURRENT)
    print(f"current weights TRAIN   won {chw:.1f}% top3 {cht3:.1f}%")


if __name__ == "__main__":
    main()
