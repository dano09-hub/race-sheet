#!/usr/bin/env python3
"""Fit the twelve weights on settled races and test them by date.

This is a conditional logit: one race is one choice, the page's own 0-100 factor
scores are the covariates, and a runner's score is the page's weighted average
(weights renormalised over the factors that runner actually has). The fit is
compared against the live FACTORS weights on a later slice of dates, and against
the market where a pre-race price exists.

It writes nothing: no weights are applied to the page or to a stored pick.

    python3 tooling/train_weights.py
    python3 tooling/train_weights.py --test-days 2 --l2 0.5
"""
import argparse
import csv
import json
import math
import os
import re
import sys

import numpy as np
from scipy.optimize import minimize, minimize_scalar

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KEYS = ["market", "form", "tips", "rpr", "rating", "cd", "draw",
        "speed", "going", "fitness", "weight", "connections"]


def load_races(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    groups = {}
    for row in rows:
        # race_id separates two divisions off at the same time; older exports
        # without one still group by date, course and off time.
        groups.setdefault((row["date"], row["course"], row["time"],
                           row.get("race_id") or ""), []).append(row)
    races = []
    for key in sorted(groups):
        group = groups[key]
        if not any(r["win"] == "1" for r in group):
            continue
        X = np.array([[float(r[k]) if r[k] not in ("", "None") else np.nan
                       for k in KEYS] for r in group])
        y = np.array([1.0 if r["win"] == "1" else 0.0 for r in group])
        odds = [float(r["odds"]) if r["odds"] else None for r in group]
        races.append({"date": key[0], "X": X, "y": y, "odds": odds,
                      "market": np.array([np.nan if r["market"] == "" else float(r["market"])
                                          for r in group])})
    return races


def scores(w, X):
    """The page's weighted average over the factors a runner actually has."""
    mask = ~np.isnan(X)
    num = np.nansum(np.where(mask, X, 0.0) * w, axis=1)
    den = (mask * w).sum(axis=1)
    return np.where(den > 0, num / np.where(den > 0, den, 1.0), 0.0)


def softmax(s):
    s = s - np.max(s)
    p = np.exp(s)
    return p / p.sum()


def _split(theta):
    """Twelve weights plus one scale: the page's average is a ranking score,
    so a fitted scale is what turns it into a probability."""
    return theta[:-1], float(theta[-1])


def nll_grad(theta, races, l2):
    w, scale = _split(theta)
    loss, grad = 0.0, np.zeros_like(theta)
    for race in races:
        X, y = race["X"], race["y"]
        mask = ~np.isnan(X)
        Xf = np.where(mask, X, 0.0)
        den = (mask * w).sum(axis=1)
        den = np.where(den > 0, den, 1.0)
        base = np.where(den > 0, (Xf * w).sum(axis=1) / den, 0.0)
        p = softmax(scale * base)
        i = int(np.argmax(y))
        loss -= math.log(max(p[i], 1e-12))
        ds = scale * mask * (Xf - base[:, None]) / den[:, None]
        grad[:-1] += (p[:, None] * ds).sum(axis=0) - ds[i]
        grad[-1] += float((p * base).sum() - base[i])
    loss += l2 * float(np.dot(w, w))
    grad[:-1] += 2 * l2 * w
    return loss, grad


def evaluate(w, scale, races):
    """NLL and top-1 over all races, plus the same over priced races only."""
    rows = []
    for race in races:
        s = scale * scores(w, race["X"])
        p = softmax(s)
        i = int(np.argmax(race["y"]))
        rows.append({"nll": -math.log(max(p[i], 1e-12)),
                     "hit": int(np.argmax(s) == i),
                     "priced": not np.all(np.isnan(race["market"]))})
    n = len(rows) or 1
    priced = [r for r in rows if r["priced"]] or [{"nll": 0.0, "hit": 0}]
    return {"nll": sum(r["nll"] for r in rows) / n,
            "acc": sum(r["hit"] for r in rows) / n,
            "p_nll": sum(r["nll"] for r in priced) / len(priced),
            "p_acc": sum(r["hit"] for r in priced) / len(priced),
            "priced": len(priced)}


def calibrate(w, races):
    """One scale for a fixed ranking, fitted on the same races the model saw,
    so both sides of the comparison are equally confident."""
    def loss(scale):
        total = 0.0
        for race in races:
            p = softmax(scale * scores(w, race["X"]))
            total -= math.log(max(p[int(np.argmax(race["y"]))], 1e-12))
        return total

    res = minimize_scalar(loss, bounds=(1e-4, 20.0), method="bounded")
    return float(res.x)


def market_accuracy(races):
    hits = n = 0
    for race in races:
        m = race["market"]
        if np.all(np.isnan(m)):
            continue
        n += 1
        hits += int(np.nanargmax(m) == int(np.argmax(race["y"])))
    return (hits / n if n else 0.0, n)


def live_weights():
    """Read FACTORS from the page so the baseline is the deployed model."""
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    block = re.search(r"var FACTORS = \[(.*?)\n  \];", html, re.S)
    found = {}
    if block:
        for name, weight in re.findall(r"\['(\w+)',\s*'[^']*',\s*(\d+)\]", block.group(1)):
            found[name] = float(weight)
    if len(found) != len(KEYS):
        raise SystemExit(f"could not read the twelve weights from index.html: {found}")
    return np.array([found[k] for k in KEYS])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(ROOT, "data", "training", "runners.csv"))
    ap.add_argument("--test-days", type=int, default=1,
                    help="number of latest race days held out for the test")
    ap.add_argument("--l2", type=float, default=0.5)
    args = ap.parse_args(argv)

    races = load_races(args.csv)
    if len(races) < 50:
        print(f"only {len(races)} settled races; a fit would be noise. "
              "Add more days or an external dataset first.")
        return 1
    days = sorted({r["date"] for r in races})
    if len(days) <= args.test_days:
        print(f"need more than {args.test_days} race days, have {len(days)}")
        return 1
    cutoff = days[-args.test_days]
    train = [r for r in races if r["date"] < cutoff]
    test = [r for r in races if r["date"] >= cutoff]

    base = live_weights()
    start = np.append(base / base.sum(), 0.1)
    res = minimize(lambda t: nll_grad(t, train, args.l2), start, jac=True,
                   method="L-BFGS-B",
                   bounds=[(0.0, None)] * len(KEYS) + [(1e-4, 20.0)],
                   options={"maxiter": 2000, "ftol": 1e-10})
    fit_w, fit_scale = _split(res.x)
    fit_w = fit_w / fit_w.sum() * base.sum()

    base_scale = calibrate(base / base.sum(), train)
    b = evaluate(base / base.sum(), base_scale, test)
    f = evaluate(fit_w / fit_w.sum(), fit_scale, test)
    mkt_acc, mkt_n = market_accuracy(test)

    print(f"races: {len(races)} settled  ({len(train)} train / {len(test)} test)")
    print(f"train dates: {train[0]['date']} .. {train[-1]['date']}")
    print(f"test dates:  {test[0]['date']} .. {test[-1]['date']}")
    print(f"l2: {args.l2}   fitted scale: {fit_scale:.3f}   live scale: {base_scale:.3f}")
    print(f"optimizer: {res.success} ({res.message})")
    print()
    print(f"{'':14}{'all NLL':>9}{'top-1':>8}{'priced NLL':>12}{'top-1':>8}")
    print(f"{'live weights':14}{b['nll']:9.4f}{100*b['acc']:7.1f}%{b['p_nll']:12.4f}{100*b['p_acc']:7.1f}%")
    print(f"{'fitted':14}{f['nll']:9.4f}{100*f['acc']:7.1f}%{f['p_nll']:12.4f}{100*f['p_acc']:7.1f}%")
    if mkt_n:
        print(f"{'market':14}{'':9}{'':8}{'':12}{100*mkt_acc:7.1f}%  ({mkt_n} priced races)")
    print()
    print("fitted weights (scaled to the live total, then rounded):")
    factor = base.sum() / fit_w.sum()
    for k, live, fit in zip(KEYS, base, fit_w * factor):
        print(f"  {k:12} {live:5.1f} -> {fit:5.1f}")
    print()
    if len(train) < 300 or len(test) < 100:
        print(f"Only {len(train)} training and {len(test)} held-out races: the fit is a "
              "pipeline test, not evidence. Differences of a few points are noise.")
    if f["nll"] < b["nll"] and f["acc"] >= b["acc"]:
        print("The fitted weights beat the live ones on both held-out measures. That is a "
              "candidate for a shadow arm, not a change to the page.")
    elif f["nll"] < b["nll"]:
        print("Held-out log loss improved but top-1 did not: a calibration gain, not a "
              "better ranking. Not a candidate.")
    else:
        print("The fitted weights did not beat the live ones out of sample; the live "
              "weights stay. More races are needed before this can settle anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
