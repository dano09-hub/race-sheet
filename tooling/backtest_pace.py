#!/usr/bin/env python3
"""Causal pace experiment: can prior in-running style add anything?

The free result pages carry an in-running comment for almost every runner.  This
script turns those comments into an early-position label, builds each runner's
pace profile from *previous* runs only, and tests it on a chronological holdout
against the market and the available-factor score.

It is research only.  It does not read or write the live cards, and it does not
change the page's weights.

    python3 tooling/backtest_pace.py
    -> data/backtest_pace.json

Why only seven base factors?  The live 12-factor page needs pre-race OR, RPR and
Topspeed, which the free result archive does not retain.  This test therefore
uses the seven factors that can be reconstructed causally from data/2026-*.json:
market, form, tips, fitness, course, distance and connections.  That limitation
is stated in the report and must not be mistaken for a test of the full page.
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import gzip
import json
import math
import os
import re

import numpy as np
from scipy.optimize import minimize

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATASET = os.path.join(ROOT, "data", "dataset.csv.gz")
RAW_GLOB = os.path.join(ROOT, "data", "2026-*.json")
OUT = os.path.join(ROOT, "data", "backtest_pace.json")

BASE_FACTORS = ("market", "form", "tips", "fitness", "course", "distance", "connections")
CURRENT_WEIGHTS = {"market": 18, "tips": 12, "form": 8, "connections": 6,
                   "course": 4, "distance": 3, "fitness": 3}
FORM_PTS = {"1": 10.0, "2": 7.0, "3": 5.0, "4": 3.0, "5": 2.0, "6": 1.0}
PACE_FEATURES = ("pace_style", "pace_front", "pace_led", "pace_conflict",
                 "pace_front_x_pressure", "pace_led_x_pressure")

# Each pattern contributes one early-position label.  The *earliest* match in
# the opening part of the comment wins, so "held up, headway to lead" stays a
# hold-up horse rather than being counted as a front-runner.
STYLES = (
    (3, r"\bmade all\b"),
    (3, r"\b(?:disputed|joint)\s+lead\b"),
    (3, r"\bled\b"),
    (2, r"\b(?:prominent|tracked\s+leaders?|chased\s+leaders?|pressed\s+leader|pursued\s+leader)\b"),
    (1, r"\b(?:mid[- ]division|midfield|in\s+touch)\b"),
    (0, r"\b(?:held\s+up|waited\s+with|towards\s+rear|in\s+rear|last|behind|outpaced|dwelt|slowly\s+away)\b"),
)


def norm_horse(s):
    s = re.sub(r"\(.*?\)", "", str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_course(s):
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def style_of(comment):
    """Early running style: 0 hold-up/rear, 1 mid, 2 prominent, 3 led."""
    text = re.sub(r"\s+", " ", str(comment or "")).strip().lower()
    if not text:
        return None
    # The opening is the early part of the race.  Looking at the whole comment
    # would turn late headway and late leads into false front-runners.
    opening = text[:180]
    hits = []
    for value, pattern in STYLES:
        m = re.search(pattern, opening)
        if m:
            hits.append((m.start(), value))
    if not hits:
        return None
    hits.sort(key=lambda x: (x[0], -x[1]))
    return hits[0][1]


def weighted_style(history):
    """Most-recent-first weighted mean style, using at most six prior runs."""
    recent = history[-6:][::-1]
    if not recent:
        return None
    weights = [1.0, 0.8, 0.6, 0.4, 0.2, 0.1][:len(recent)]
    return sum(v * w for v, w in zip(recent, weights)) / sum(weights)


def pace_features(history, pressure):
    """Features for one runner from only the runs before today's race."""
    if not history:
        return {
            "pace_style": None, "pace_front": None, "pace_led": None,
            "race_pressure": pressure,
            "pace_conflict": None, "pace_front_x_pressure": None,
            "pace_led_x_pressure": None, "pace_missing": 1,
        }
    recent = history[-6:]
    front = sum(1 for s in recent if s >= 2) / len(recent)
    led = sum(1 for s in recent if s == 3) / len(recent)
    p = pressure if pressure is not None else 0.0
    return {
        "pace_style": weighted_style(history),
        "pace_front": front,
        "pace_led": led,
        "race_pressure": p,
        "pace_conflict": front - p,
        "pace_front_x_pressure": front * p,
        "pace_led_x_pressure": led * p,
        "pace_missing": 0,
    }


def build_pace_index():
    """Walk the raw result days in time order and return pre-race pace rows."""
    history = collections.defaultdict(list)   # normalised horse -> [style]
    index = {}
    counts = collections.Counter()
    for path in sorted(glob.glob(RAW_GLOB)):
        date = os.path.basename(path)[:10]
        with open(path, encoding="utf-8") as f:
            day = json.load(f)
        for meeting in day.get("meetings") or []:
            course = norm_course(meeting.get("course"))
            races = sorted(meeting.get("races") or [], key=lambda r: str(r.get("time", "")))
            for race in races:
                runners = race.get("runners") or []
                known = []
                for r in runners:
                    h = norm_horse(r.get("horse"))
                    hist = history[h]
                    if hist:
                        known.append(sum(1 for s in hist[-6:] if s >= 2) / len(hist[-6:]))
                pressure = float(np.mean(known)) if known else None
                for r in runners:
                    h = norm_horse(r.get("horse"))
                    key = (date, course, str(race.get("time", "")), h)
                    index[key] = pace_features(history[h], pressure)
                    counts["runners"] += 1
                    if history[h]:
                        counts["with_history"] += 1
                    if style_of(r.get("comment")) is not None:
                        counts["comments_usable"] += 1
                # Update only after every runner has been scored: no current
                # comment can leak into this race's features.
                for r in runners:
                    s = style_of(r.get("comment"))
                    if s is not None:
                        history[norm_horse(r.get("horse"))].append(s)
                counts["races"] += 1
    return index, counts


def parse_sp(value):
    s = str(value or "").strip()
    if not s:
        return None
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*[A-Z]*\s*$", s)
    if m and float(m.group(2)):
        return 1.0 + float(m.group(1)) / float(m.group(2))
    try:
        v = float(s)
        return v if v > 1.0 else None
    except ValueError:
        return None


def num(v, default=None):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


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
    return 100.0 * (w + 0.5) / (r + 2.0)


def base_scores(rows):
    """The seven factors available without pre-race OR/RPR/TS, as 0-100 scores."""
    raw = []
    for r in rows:
        sp = parse_sp(r.get("sp"))
        raw.append({
            "market": (1.0 / sp) if sp else None,
            "form": form_score(r.get("form")),
            "tips": num(r.get("tips"), 0.0),
            "fitness": -num(r.get("days_since"), 365.0),
            "course": rate(r.get("course_wins"), r.get("course_runs")),
            "distance": rate(r.get("dist_wins"), r.get("dist_runs")),
            "connections": None if (num(r.get("j_30_wr")) is None and num(r.get("t_30_wr")) is None)
                           else 100.0 * ((num(r.get("j_30_wr"), 0) or 0) * 0.5
                                         + (num(r.get("t_30_wr"), 0) or 0) * 0.5),
        })
    cols = {f: [d[f] for d in raw if d[f] is not None] for f in BASE_FACTORS}
    for r, d in zip(rows, raw):
        scores = {}
        for f in BASE_FACTORS:
            v, vals = d[f], cols[f]
            if v is None or len(vals) < 2:
                scores[f] = None
            else:
                lo, hi = min(vals), max(vals)
                # A field-constant factor tells us nothing and the page drops it.
                scores[f] = None if hi == lo else 100.0 * (v - lo) / (hi - lo)
        r["_base"] = scores
    return rows


def load_races(pace_index):
    races = collections.defaultdict(list)
    with gzip.open(DATASET, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            key = (r["date"], norm_course(r["course"]), str(r["time"]), r["race"])
            races[key].append(r)
    rows = []
    for key in sorted(races):
        # NR / withdrawn rows are not runners and carry no SP; including them
        # would make every race look incompletely priced.
        rs = sorted((r for r in races[key]
                     if str(r.get("pos", "")).strip().upper() not in ("", "NR", "WD", "W")),
                    key=lambda r: r["horse"])
        wins = [i for i, r in enumerate(rs) if r.get("won") == "1"]
        if len(wins) != 1 or len(rs) < 2:
            continue
        base_scores(rs)
        pace_key = lambda r: (key[0], key[1], key[2], norm_horse(r["horse"]))
        pace = [pace_index.get(pace_key(r)) for r in rs]
        dec = [parse_sp(r.get("sp")) for r in rs]
        rows.append({"key": key, "rows": rs, "winner": wins[0], "pace": pace, "dec": dec})
    return rows


def design(races, with_pace=False, pace_only=False):
    X, starts, sizes, winners, dates = [], [], [], [], []
    at = 0
    for rec in races:
        base_cols = [] if pace_only else list(BASE_FACTORS)
        cols = []
        for r in rec["rows"]:
            vals = []
            if not pace_only:
                for name in base_cols:
                    v = r["_base"].get(name)
                    vals.extend([math.nan if v is None else v, 0.0 if v is not None else 1.0])
            if with_pace or pace_only:
                p = rec["pace"][rec["rows"].index(r)] or {}
                for name in PACE_FEATURES:
                    v = p.get(name)
                    vals.append(math.nan if v is None else float(v))
                vals.append(1.0 if (p.get("pace_missing") or p.get("pace_style") is None) else 0.0)
            cols.append(vals)
        X.extend(cols)
        starts.append(at)
        sizes.append(len(cols))
        winners.append(at + rec["winner"])
        dates.append(rec["key"][0])
        at += len(cols)
    return (np.asarray(X, dtype=float), np.asarray(starts, dtype=int),
            np.asarray(sizes, dtype=int), np.asarray(winners, dtype=int), dates)


def softmax(X, beta, starts, sizes):
    z = X @ beta
    zmax = np.maximum.reduceat(z, starts)
    group = np.repeat(np.arange(len(starts)), sizes)
    ex = np.exp(z - zmax[group])
    total = np.add.reduceat(ex, starts)
    return ex / total[group]


def fit_logit(X, starts, sizes, winners, lam=1.0, maxiter=300):
    p = X.shape[1]
    group = np.repeat(np.arange(len(starts)), sizes)
    starts = np.asarray(starts, dtype=int)
    winners = np.asarray(winners, dtype=int)

    def obj(beta):
        z = X @ beta
        zmax = np.maximum.reduceat(z, starts)
        ex = np.exp(z - zmax[group])
        total = np.add.reduceat(ex, starts)
        loss = np.sum(zmax + np.log(total) - z[winners])
        loss += 0.5 * lam * np.dot(beta, beta)
        prob = ex / total[group]
        grad = X.T @ prob
        grad -= X[winners].sum(axis=0)
        grad += lam * beta
        return float(loss), grad

    res = minimize(obj, np.zeros(p), jac=True, method="L-BFGS-B",
                   options={"maxiter": maxiter, "ftol": 1e-10})
    return res.x, res


def metrics(prob, starts, sizes, winners, dec):
    winners = np.asarray(winners, dtype=int)
    logloss = float(-np.mean(np.log(np.clip(prob[winners], 1e-15, 1.0))))
    onehot = np.zeros(len(prob))
    onehot[winners] = 1.0
    diff = (prob - onehot) ** 2
    brier = float(np.mean(np.add.reduceat(diff, starts)))
    top1 = top3 = 0
    returns = []
    group = np.repeat(np.arange(len(starts)), sizes)
    for k in range(len(starts)):
        idx = np.flatnonzero(group == k)
        order = idx[np.argsort(-prob[idx])]
        top = order[0]
        top1 += int(top == winners[k])
        top3 += int(winners[k] in order[:3])
        if np.isfinite(dec[top]):
            returns.append(dec[top] if top == winners[k] else 0.0)
    n = len(starts)
    return {
        "races": int(n),
        "log_loss": logloss,
        "brier": brier,
        "top1": float(100.0 * top1 / n),
        "top3": float(100.0 * top3 / n),
        "roi_sp": (float(np.mean(returns) - 1.0) * 100.0) if returns else None,
        "priced": int(len(returns)),
    }


def fixed_metrics(races):
    """Top-pick metrics for the current seven-factor weighted score (no calibration)."""
    top1 = top3 = 0
    returns = []
    n = 0
    for rec in races:
        scores = []
        for r in rec["rows"]:
            num = den = 0.0
            for name, weight in CURRENT_WEIGHTS.items():
                v = r["_base"].get(name)
                if v is not None:
                    num += v * weight
                    den += weight
            scores.append(num / den if den else -1e9)
        order = np.argsort(-np.asarray(scores))
        top = int(order[0])
        win = rec["winner"]
        n += 1
        top1 += int(top == win)
        top3 += int(win in order[:3])
        if np.isfinite(rec["dec"][top]):
            returns.append(rec["dec"][top] if top == win else 0.0)
    return {
        "races": n,
        "top1": 100.0 * top1 / n if n else None,
        "top3": 100.0 * top3 / n if n else None,
        "roi_sp": (float(np.mean(returns) - 1.0) * 100.0) if returns else None,
        "priced": len(returns),
    }


def market_probs(races):
    probs = []
    for rec in races:
        d = rec["dec"]
        q = np.array([1.0 / d[i] if np.isfinite(d[i]) else 0.0
                      for i in range(len(rec["rows"]))], dtype=float)
        total = q.sum()
        probs.extend(q / total if total else np.zeros(len(q)))
    return np.asarray(probs, dtype=float)


def date_split(dates):
    train = np.array([d < "2026-09-01" for d in dates])
    cal = np.array([("2026-09-01" <= d < "2026-10-16") for d in dates])
    test = np.array([d >= "2026-10-16" for d in dates])
    return train, cal, test


def subset(races, mask):
    return [r for r, keep in zip(races, mask) if keep]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-history", type=int, default=0,
                    help="optional: require this many prior pace runs (default 0)")
    ap.add_argument("--max-iter", type=int, default=300)
    args = ap.parse_args()

    print("building pace history from raw result comments ...")
    pace_index, counts = build_pace_index()
    print("  %d races, %d runners, %d usable comments, %d runners with prior history"
          % (counts["races"], counts["runners"], counts["comments_usable"], counts["with_history"]))

    print("loading result dataset ...")
    races = load_races(pace_index)
    print("  %d result races" % len(races))

    # Keep races where every runner has a usable SP, so market and models are
    # judged on exactly the same races.
    priced = []
    for rec in races:
        d = rec["dec"]
        if all(v is not None and v > 1.0 for v in d):
            rec["dec"] = np.asarray(d, dtype=float)
            priced.append(rec)
    print("  %d races with complete SP" % len(priced))

    if args.min_history:
        priced = [r for r in priced
                  if sum(1 for p in r["pace"] if p and p.get("pace_style") is not None) >= args.min_history]
        print("  %d races satisfy min-history=%d" % (len(priced), args.min_history))

    dates = [r["key"][0] for r in priced]
    train_mask, cal_mask, test_mask = date_split(dates)
    train = subset(priced, train_mask)
    cal = subset(priced, cal_mask)
    test = subset(priced, test_mask)
    print("  train %d (%s..%s), calibration %d, test %d"
          % (len(train), train[0]["key"][0] if train else "-",
             train[-1]["key"][0] if train else "-", len(cal), len(test)))
    if not train or not cal or not test:
        raise SystemExit("not enough races in a split")

    def fit_eval(use_pace=False, pace_only=False, train=train, cal=cal, test=test):
        Xtr, str_tr, siz_tr, win_tr, _ = design(train, with_pace=use_pace, pace_only=pace_only)
        Xca, str_ca, siz_ca, win_ca, _ = design(cal, with_pace=use_pace, pace_only=pace_only)
        Xte, str_te, siz_te, win_te, _ = design(test, with_pace=use_pace, pace_only=pace_only)
        # Fill missing with zero, then standardise on train only.
        Xtr = np.nan_to_num(Xtr, nan=0.0)
        Xca = np.nan_to_num(Xca, nan=0.0)
        Xte = np.nan_to_num(Xte, nan=0.0)
        mean = Xtr.mean(axis=0)
        std = Xtr.std(axis=0)
        std[std == 0] = 1.0
        Xtr = (Xtr - mean) / std
        Xca = (Xca - mean) / std
        Xte = (Xte - mean) / std
        beta, res = fit_logit(Xtr, str_tr, siz_tr, win_tr, maxiter=args.max_iter)
        out = {}
        for name, X, st, sz, win, part in (
                ("calibration", Xca, str_ca, siz_ca, win_ca, cal),
                ("test", Xte, str_te, siz_te, win_te, test)):
            p = softmax(X, beta, st, sz)
            dec = np.concatenate([r["dec"] for r in part])
            out[name] = metrics(p, st, sz, win, dec)
        return out, beta, res

    print("fitting base model ...")
    base, base_beta, base_res = fit_eval(use_pace=False)
    print("fitting base + pace ...")
    paced, pace_beta, pace_res = fit_eval(use_pace=True)
    print("fitting pace-only ...")
    pace_only, pace_only_beta, _ = fit_eval(use_pace=True, pace_only=True)

    # Market and fixed current-score reference on the same test races.
    dec_test = np.concatenate([r["dec"] for r in test])
    starts_test = np.cumsum([0] + [len(r["rows"]) for r in test[:-1]])
    sizes_test = np.asarray([len(r["rows"]) for r in test])
    winners_test = np.asarray([starts_test[i] + test[i]["winner"] for i in range(len(test))])
    mkt = market_probs(test)
    market_test = metrics(mkt, starts_test, sizes_test, winners_test, dec_test)
    current_test = fixed_metrics(test)
    current_all = fixed_metrics(priced)

    # Name the coefficients so the fit is auditable, not just a score.
    design_names = []
    for f in BASE_FACTORS:
        design_names += [f, f + "_missing"]
    pace_names = []
    for f in PACE_FEATURES:
        pace_names.append(f)
    pace_names.append("pace_missing")

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "data/2026-*.json comments + data/dataset.csv.gz",
        "purpose": "research-only test of prior in-running pace; no production change",
        "method": {
            "style_labels": "0 hold-up/rear, 1 mid/in touch, 2 prominent/tracked, 3 led/disputed",
            "history": "last six prior runs; current race comment applied only after all features are emitted",
            "pace_features": list(PACE_FEATURES),
            "base_factors": list(BASE_FACTORS),
            "base_weights_reference": CURRENT_WEIGHTS,
            "splits": {"train": "< 2026-09-01", "calibration": "2026-09-01..2026-10-15",
                       "test": ">= 2026-10-16"},
            "model": "race-normalised conditional logit with ridge 1.0, standardised on train",
        },
        "coverage": {
            "raw_races": counts["races"],
            "raw_runners": counts["runners"],
            "usable_comments": counts["comments_usable"],
            "runners_with_prior_pace": counts["with_history"],
            "score_races": len(races),
            "priced_races": len(priced),
            "train_races": len(train),
            "calibration_races": len(cal),
            "test_races": len(test),
        },
        "metrics": {
            "market": market_test,
            "current_fixed": current_test,
            "base": base["test"],
            "base_pace": paced["test"],
            "pace_only": pace_only["test"],
        },
        "calibration": {
            "base": base["calibration"],
            "base_pace": paced["calibration"],
            "pace_only": pace_only["calibration"],
        },
        "deltas_test": {
            "base_vs_market_log_loss": base["test"]["log_loss"] - market_test["log_loss"],
            "base_pace_vs_market_log_loss": paced["test"]["log_loss"] - market_test["log_loss"],
            "base_pace_vs_base_log_loss": paced["test"]["log_loss"] - base["test"]["log_loss"],
            "base_vs_market_top1": base["test"]["top1"] - market_test["top1"],
            "base_pace_vs_market_top1": paced["test"]["top1"] - market_test["top1"],
            "base_pace_vs_base_top1": paced["test"]["top1"] - base["test"]["top1"],
            "base_vs_market_top3": base["test"]["top3"] - market_test["top3"],
            "base_pace_vs_market_top3": paced["test"]["top3"] - market_test["top3"],
            "base_pace_vs_base_top3": paced["test"]["top3"] - base["test"]["top3"],
        },
        "coefficients": {
            "base": dict(zip(design_names, [float(x) for x in base_beta])),
            "base_pace": dict(zip(design_names + pace_names, [float(x) for x in pace_beta])),
            "pace_only": dict(zip(pace_names, [float(x) for x in pace_only_beta])),
        },
        "current_fixed_all_priced": current_all,
        "caveat": (
            "The live page uses 12 factors, including pre-race OR/RPR/Topspeed, draw and weight. "
            "Those are not in the free result archive, so this test is a seven-factor proxy, not "
            "a test of the full production model. Comments are subjective and are used only from "
            "previous runs. SP is a post-time benchmark. No live card or pick is changed."
        ),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nTEST (%d priced races)" % len(test))
    for label in ("market", "current_fixed", "base", "base_pace", "pace_only"):
        m = out["metrics"][label]
        print("%-14s top1 %5.1f%% | top3 %5.1f%% | logloss %s | Brier %s | ROI %s"
              % (label, m["top1"], m["top3"],
                 ("%.4f" % m["log_loss"]) if "log_loss" in m else "   —  ",
                 ("%.4f" % m["brier"]) if "brier" in m else "   —  ",
                 ("%+6.1f%%" % m["roi_sp"]) if m.get("roi_sp") is not None else "   —  "))
    print("\ndeltas: pace vs base  logloss %+.4f  top1 %+.2f pp  top3 %+.2f pp"
          % (out["deltas_test"]["base_pace_vs_base_log_loss"],
             out["deltas_test"]["base_pace_vs_base_top1"],
             out["deltas_test"]["base_pace_vs_base_top3"]))
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
