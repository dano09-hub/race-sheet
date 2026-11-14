#!/usr/bin/env python3
"""Leakage-safe test of the rating factors against the SP market.

    python3 tooling/backtest_ratings.py --db /path/to/raceform.db

The public repo intentionally does not ship raceform.db. The free Kaggle
dataset is UK/Ireland results with OR, RPR and Topspeed, but its RPR/TS columns
are the figures earned *in that race*. Using them for the same race would leak
the result, so this test uses:

  * the official rating carried into the race (pre-race)
  * the horse's previous known RPR and Topspeed (lagged)
  * previous finishing positions, days since a run, and prior course/distance wins
  * weight and draw

It fits a race-normalised conditional logit (softmax) and compares it with the
SP market. The market price is used only as a benchmark and in the out-of-sample
Benter blend, never as a model feature. The report is written to
data/backtest_ratings.json; no raw Kaggle data is written to the repo.

Research-only dependencies: pandas, numpy, scipy.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "data", "backtest_ratings.json")

# The file contains UK/IRE plus international meetings. UK courses carry no
# country suffix, Irish courses end in (IRE), and UK/Irish all-weather tracks
# end in (AW). Everything else is excluded.
SQL = """
SELECT date, course, race_id, off, race_name, type, class AS race_class,
       dist, going, ran, num, pos, draw, horse, age, wgt, sp,
       [or] AS or_rating, rpr, ts, jockey, trainer
FROM data
WHERE (course NOT LIKE '%(%' OR course LIKE '%(AW)%' OR course LIKE '%(IRE)%')
  AND date >= '2016-01-01' AND date <= '2025-12-31'
"""

# (column, lower_is_better)
FEATURES = [
    ("or_rating", False),
    ("prev_rpr", False),
    ("prev_ts", False),
    ("prev_pos", True),
    ("prev_pos2", True),
    ("days_since", True),
    ("weight_lb", True),
    ("draw", True),
    ("course_win_before", False),
    ("distance_win_before", False),
]


def parse_odds(value):
    """Fractional/EVS SP to decimal, or NaN."""
    s = str(value or "").strip().lower()
    if not s:
        return np.nan
    if s in ("evs", "evens"):
        return 2.0
    if "/" in s:
        bits = s.replace("f", "").split("/")
        try:
            return 1.0 + float(bits[0]) / float(bits[1])
        except (ValueError, ZeroDivisionError):
            return np.nan
    try:
        v = float(s)
        return v if v > 1.0 else np.nan
    except ValueError:
        return np.nan


def parse_weight(value):
    """'11-10' (stone-pounds) to pounds."""
    s = str(value or "").strip()
    if "-" not in s:
        return np.nan
    a, b = s.split("-", 1)
    try:
        return float(a) * 14.0 + float(b)
    except ValueError:
        return np.nan


def load(db):
    con = sqlite3.connect(db)
    try:
        df = pd.read_sql_query(SQL, con)
    finally:
        con.close()
    if df.empty:
        raise SystemExit("no GB/IRE rows found in " + db)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "race_id", "horse", "pos"])
    df["race_id"] = df["race_id"].astype(np.int64)
    df["pos"] = pd.to_numeric(df["pos"], errors="coerce")
    # Keep non-finishers as losers: dropping them would remove exactly the
    # horses the model failed to rank and bias every race in the model's favour.
    df["won"] = (df["pos"] == 1).astype(np.int8)
    df["sp_dec"] = df["sp"].map(parse_odds)
    df["weight_lb"] = df["wgt"].map(parse_weight)
    df["or_rating"] = pd.to_numeric(df["or_rating"], errors="coerce")
    df["rpr"] = pd.to_numeric(df["rpr"], errors="coerce")
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df["draw"] = pd.to_numeric(df["draw"], errors="coerce")
    df["age"] = pd.to_numeric(df["age"], errors="coerce")

    # Keep only races with exactly one winner and at least two runners.
    winners = df.groupby("race_id", sort=False)["won"].transform("sum")
    sizes = df.groupby("race_id", sort=False)["horse"].transform("size")
    df = df[(winners == 1) & (sizes >= 2)].copy()

    # Feature construction is chronological per horse. ffill makes a missing
    # latest rating fall back to the last figure the horse actually earned;
    # shift(1) then guarantees the figure predates the current race.
    df = df.sort_values(["horse", "date", "off"], kind="mergesort")
    horse = df["horse"]
    for col in ("rpr", "ts", "or_rating"):
        known = df.groupby("horse", sort=False)[col].ffill()
        df["prev_" + col] = known.groupby(horse, sort=False).shift(1)
    df["prev_pos"] = df.groupby("horse", sort=False)["pos"].shift(1)
    df["prev_pos2"] = df.groupby("horse", sort=False)["pos"].shift(2)
    prev_date = df.groupby("horse", sort=False)["date"].shift(1)
    df["days_since"] = (df["date"] - prev_date).dt.days
    # Cap very long absences before normalising; they behave differently from
    # a normal break.
    df["days_since"] = df["days_since"].clip(upper=730)

    df["course_runs_before"] = df.groupby(["horse", "course"], sort=False).cumcount()
    df["course_wins_before"] = (
        df.groupby(["horse", "course"], sort=False)["won"].cumsum() - df["won"]
    )
    df["distance_runs_before"] = df.groupby(["horse", "dist"], sort=False).cumcount()
    df["distance_wins_before"] = (
        df.groupby(["horse", "dist"], sort=False)["won"].cumsum() - df["won"]
    )
    df["course_win_before"] = (df["course_wins_before"] > 0).astype(float)
    df["distance_win_before"] = (df["distance_wins_before"] > 0).astype(float)

    # Within-race normalisation mirrors the page's 0-100 factor scores. A
    # constant factor is left null: it cannot separate runners.
    for col, lower in FEATURES:
        raw = df[col]
        grp = df.groupby("race_id", sort=False)[col]
        lo = grp.transform("min")
        hi = grp.transform("max")
        span = hi - lo
        score = pd.Series(np.nan, index=df.index, dtype=float)
        ok = span > 0
        if lower:
            score[ok] = 100.0 * (hi[ok] - raw[ok]) / span[ok]
        else:
            score[ok] = 100.0 * (raw[ok] - lo[ok]) / span[ok]
        df[col + "_score"] = score
        df[col + "_missing"] = raw.isna().astype(float)

    # The market is only valid when every runner has a usable SP. This keeps
    # the benchmark and the blend on the same race set.
    valid_sp = df["sp_dec"].notna() & (df["sp_dec"] > 1.0)
    df["market_complete"] = valid_sp.groupby(df["race_id"], sort=False).transform("all")

    df = df.sort_values(["date", "race_id", "num"], kind="mergesort").reset_index(drop=True)
    df["race_date"] = df["date"]
    return df


def pack(sub, feature_cols):
    """Return X, race starts/sizes and winner indices for a date slice."""
    sub = sub.sort_values(["race_id", "num"], kind="mergesort").reset_index(drop=True)
    race_ids, starts = np.unique(sub["race_id"].to_numpy(), return_index=True)
    sizes = np.diff(np.append(starts, len(sub))).astype(int)
    winner_idx = np.flatnonzero(sub["won"].to_numpy() == 1)
    if len(winner_idx) != len(race_ids):
        raise ValueError("race pack lost a winner")
    X = sub[feature_cols].to_numpy(dtype=np.float64)
    X = np.nan_to_num(X, nan=0.0)
    return sub, X, starts, sizes, winner_idx, race_ids


def softmax(X, beta, starts, sizes):
    z = X @ beta
    zmax = np.maximum.reduceat(z, starts)
    group = np.repeat(np.arange(len(starts)), sizes)
    ex = np.exp(z - zmax[group])
    total = np.add.reduceat(ex, starts)
    return ex / total[group], z, zmax, total, group, ex


def fit_logit(X, starts, sizes, winner_idx, lam=1.0, maxiter=300):
    p = X.shape[1]
    group = np.repeat(np.arange(len(starts)), sizes)
    starts = np.asarray(starts, dtype=int)
    winner_idx = np.asarray(winner_idx, dtype=int)

    def obj(beta):
        z = X @ beta
        zmax = np.maximum.reduceat(z, starts)
        ex = np.exp(z - zmax[group])
        total = np.add.reduceat(ex, starts)
        loss = np.sum(zmax + np.log(total) - z[winner_idx])
        loss += 0.5 * lam * np.dot(beta[1:], beta[1:])
        prob = ex / total[group]
        grad = X.T @ prob
        grad -= X[winner_idx].sum(axis=0)
        grad[1:] += lam * beta[1:]
        return float(loss), grad

    res = minimize(obj, np.zeros(p), jac=True, method="L-BFGS-B",
                   options={"maxiter": maxiter, "ftol": 1e-10})
    return res.x, res


def metrics(prob, starts, sizes, winner_idx, dec):
    winner_idx = np.asarray(winner_idx, dtype=int)
    logloss = float(-np.mean(np.log(np.clip(prob[winner_idx], 1e-15, 1.0))))
    onehot = np.zeros(len(prob))
    onehot[winner_idx] = 1.0
    diff = (prob - onehot) ** 2
    # Race-level Brier: sum the runner errors within each race, then average
    # over races. A raw row sum would grow with the number of runners.
    brier = float(np.mean(np.add.reduceat(diff, starts)))
    top1 = top3 = 0
    returns = []
    group = np.repeat(np.arange(len(starts)), sizes)
    for k in range(len(starts)):
        idx = np.flatnonzero(group == k)
        if not len(idx):
            continue
        order = idx[np.argsort(-prob[idx])]
        top = order[0]
        top1 += int(top == winner_idx[k])
        top3 += int(winner_idx[k] in order[:3])
        if np.isfinite(dec[top]):
            returns.append(dec[top] if top == winner_idx[k] else 0.0)
    n = len(starts)
    roi = (float(np.mean(returns) - 1.0) * 100.0) if returns else float("nan")
    return {
        "races": int(n),
        "log_loss": logloss,
        "brier": brier,
        "top1": float(100.0 * top1 / n),
        "top3": float(100.0 * top3 / n),
        "roi_sp": roi,
        "priced": int(len(returns)),
    }


def market_probs(sub):
    q = 1.0 / sub["sp_dec"].to_numpy(dtype=float)
    group = sub["race_id"].to_numpy()
    starts = np.unique(group, return_index=True)[1]
    sizes = np.diff(np.append(starts, len(sub)))
    total = np.add.reduceat(q, starts)
    return q / np.repeat(total, sizes)


def split_by_date(df):
    d = df["race_date"]
    return {
        "train": df[d < "2023-01-01"],
        "calibration": df[(d >= "2023-01-01") & (d < "2024-07-01")],
        "test": df[d >= "2024-07-01"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "raceform.db"),
                    help="path to raceform.db (free Kaggle download; not in repo)")
    ap.add_argument("--max-iter", type=int, default=300)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        raise SystemExit("raceform.db not found at %s\nDownload the free Kaggle dataset or pass --db." % args.db)

    print("loading %s ..." % args.db)
    df = load(args.db)
    print("GB/IRE rows: %d | races: %d | dates: %s to %s" % (
        len(df), df["race_id"].nunique(), df["date"].min().date(), df["date"].max().date()))

    feature_cols = [c + "_score" for c, _ in FEATURES] + [c + "_missing" for c, _ in FEATURES]
    parts = split_by_date(df)
    packed = {}
    for name, sub in parts.items():
        if sub.empty:
            raise SystemExit("empty %s split" % name)
        packed[name] = pack(sub, feature_cols)
        print("%-12s %6d races %8d runners" % (name, len(packed[name][5]), len(sub)))

    _, Xtr, str_tr, siz_tr, win_tr, _ = packed["train"]
    mean = Xtr.mean(axis=0)
    std = Xtr.std(axis=0)
    std[std == 0] = 1.0

    def scale(X):
        return (X - mean) / std

    print("fitting conditional logit on %d races ..." % len(str_tr))
    beta, res = fit_logit(scale(Xtr), str_tr, siz_tr, win_tr, maxiter=args.max_iter)
    print("fit: %s | iterations=%d" % (res.message, res.nit))

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "raceform.db (free Kaggle UK/Ireland archive)",
        "date_range": [str(df["date"].min().date()), str(df["date"].max().date())],
        "splits": {k: {"races": int(len(v[5])), "runners": int(len(v[0]))}
                   for k, v in packed.items()},
        "features": {c: {"weight": float(b), "lower_is_better": lo}
                     for (c, lo), b in zip(FEATURES, beta[:len(FEATURES)])},
        "missing_flags": {c: float(b) for (c, _), b in
                          zip(FEATURES, beta[len(FEATURES):])},
        "caveat": ("RPR/TS are the figures earned in the race, so the model uses "
                   "the previous known figures. SP is a post-time benchmark, not "
                   "a price available at the decision time."),
    }

    # Evaluate on the test races where every runner has an SP, so model and
    # market are compared on exactly the same races.
    for name in ("calibration", "test"):
        sub, X, starts, sizes, win, _ = packed[name]
        mkt_ok = sub["market_complete"].to_numpy()
        # Keep whole races only.
        race_ok = pd.Series(mkt_ok).groupby(sub["race_id"].to_numpy()).transform("all").to_numpy()
        sub2 = sub[race_ok].reset_index(drop=True)
        sub2, X2, st2, sz2, w2, _ = pack(sub2, feature_cols)
        model_p, *_ = softmax(scale(X2), beta, st2, sz2)
        mkt_p = market_probs(sub2)
        dec = sub2["sp_dec"].to_numpy(dtype=float)
        out[name] = {
            "market": metrics(mkt_p, st2, sz2, w2, dec),
            "ratings_only": metrics(model_p, st2, sz2, w2, dec),
        }
        if name == "calibration":
            best_w, best_ll = 1.0, float("inf")
            for w in np.arange(0.0, 1.0001, 0.05):
                raw = np.power(np.clip(model_p, 1e-15, 1.0), w) * np.power(np.clip(mkt_p, 1e-15, 1.0), 1.0 - w)
                # Renormalise within race
                gid = np.repeat(np.arange(len(st2)), sz2)
                tot = np.add.reduceat(raw, st2)
                p = raw / tot[gid]
                ll = -np.mean(np.log(np.clip(p[w2], 1e-15, 1.0)))
                if ll < best_ll:
                    best_ll, best_w = ll, float(w)
            out["blend_weight"] = best_w
            raw = np.power(np.clip(model_p, 1e-15, 1.0), best_w) * np.power(np.clip(mkt_p, 1e-15, 1.0), 1.0 - best_w)
            gid = np.repeat(np.arange(len(st2)), sz2)
            p = raw / np.add.reduceat(raw, st2)[gid]
            out[name]["blend"] = metrics(p, st2, sz2, w2, dec)

    # Test blend uses the weight chosen only on calibration.
    sub, X, starts, sizes, win, _ = packed["test"]
    mkt_ok = sub["market_complete"].to_numpy()
    race_ok = pd.Series(mkt_ok).groupby(sub["race_id"].to_numpy()).transform("all").to_numpy()
    sub2 = sub[race_ok].reset_index(drop=True)
    sub2, X2, st2, sz2, w2, _ = pack(sub2, feature_cols)
    model_p, *_ = softmax(scale(X2), beta, st2, sz2)
    mkt_p = market_probs(sub2)
    dec = sub2["sp_dec"].to_numpy(dtype=float)
    w = out["blend_weight"]
    raw = np.power(np.clip(model_p, 1e-15, 1.0), w) * np.power(np.clip(mkt_p, 1e-15, 1.0), 1.0 - w)
    gid = np.repeat(np.arange(len(st2)), sz2)
    out["test"]["blend"] = metrics(raw / np.add.reduceat(raw, st2)[gid], st2, sz2, w2, dec)
    out["test"]["blend_weight"] = w

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nTEST — %d priced races, weight selected on calibration = %.2f" % (
        out["test"]["market"]["races"], w))
    for label in ("market", "ratings_only", "blend"):
        m = out["test"][label]
        print("%-13s top1 %5.1f%% | top3 %5.1f%% | logloss %.4f | Brier %.4f | ROI %+6.1f%%" % (
            label, m["top1"], m["top3"], m["log_loss"], m["brier"], m["roi_sp"]))
    print("\nwrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
