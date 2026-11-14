#!/usr/bin/env python3
"""Direct top-k place model, calibrated on actual each-way terms.

    python3 tooling/backtest_places.py --db /path/to/raceform.db

Uses the free Kaggle UK/Ireland archive and the same leakage-safe pre-race
features as backtest_ratings.py. The target is "finished in the paid places"
under standard GB/IRE each-way terms. A binary top-k model is fitted, then a
single temperature and a per-race offset are chosen on a chronological
calibration period so the probabilities sum to the number of paid places in
every race. The untouched test period is reported next to the SP-market top-k
baseline.

No raw Kaggle rows are written to the repo. Research dependencies:
pandas, numpy, scipy.
"""
import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_ratings as br  # noqa: E402
import eachway  # noqa: E402

ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "data", "place_model.json")


def add_place_labels(df):
    df = df.copy()
    df["field_size"] = df.groupby("race_id", sort=False)["horse"].transform("size")
    name = df["race_name"].fillna("")
    df["handicap"] = name.str.contains(r"handicap|h'cap|hcap", case=False, regex=True).astype(float)
    terms = [eachway.place_terms(n, bool(h))
             for n, h in zip(df["field_size"], df["handicap"])]
    df["place_places"] = [t[0] for t in terms]
    df["place_fraction"] = [t[1] for t in terms]
    df["placed"] = ((df["pos"] <= df["place_places"]) & (df["place_places"] > 0)).astype(int)
    return df[df["place_places"] > 0].copy()


def fit_binary(X, y, lam=1.0, maxiter=300):
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, p = X.shape

    def obj(beta):
        z = X @ beta
        loss = np.mean(np.logaddexp(0.0, z) - y * z)
        loss += 0.5 * lam * np.dot(beta[1:], beta[1:]) / n
        prob = expit(z)
        grad = X.T @ (prob - y) / n
        grad[1:] += lam * beta[1:] / n
        return float(loss), grad

    res = minimize(obj, np.zeros(p), jac=True, method="L-BFGS-B",
                   options={"maxiter": maxiter, "ftol": 1e-10})
    return res.x, res


def offset_probs(z, starts, sizes, places):
    """sigmoid(z - c_race), with c chosen so each race sums to its places."""
    out = np.empty_like(z, dtype=float)
    for i, (a, size) in enumerate(zip(starts, sizes)):
        b = a + size
        k = float(places[i])
        if k <= 0:
            out[a:b] = 0.0
            continue
        if k >= size:
            out[a:b] = 1.0
            continue
        lo, hi = -60.0, 60.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            s = float(expit(z[a:b] - mid).sum())
            if s > k:
                lo = mid
            else:
                hi = mid
        out[a:b] = expit(z[a:b] - 0.5 * (lo + hi))
    return out


def binary_metrics(p, labels):
    p = np.clip(p, 1e-15, 1 - 1e-15)
    y = labels.astype(float)
    logloss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    brier = float(np.mean((p - y) ** 2))
    bins = np.linspace(0.0, 1.0, 11)
    ece = 0.0
    for a, b in zip(bins[:-1], bins[1:]):
        m = (p >= a) & (p < b)
        if m.any():
            ece += m.mean() * abs(float(p[m].mean() - y[m].mean()))
    return {"log_loss": logloss, "brier": brier, "ece": float(ece)}


def topk_capture(p, starts, sizes, labels):
    hits = actual = races = 0
    for a, size in zip(starts, sizes):
        b = a + size
        idx = np.arange(a, b)
        k = int(labels[idx].sum())
        if k <= 0:
            continue
        chosen = idx[np.argsort(-p[idx])[:k]]
        hits += int(labels[chosen].sum())
        actual += k
        races += 1
    return (100.0 * hits / actual) if actual else float("nan"), races


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "raceform.db"))
    ap.add_argument("--max-iter", type=int, default=300)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        raise SystemExit("raceform.db not found at %s\nDownload the free Kaggle dataset or pass --db." % args.db)

    print("loading %s ..." % args.db)
    df = add_place_labels(br.load(args.db))
    print("place races: %d | runners: %d | dates: %s to %s" % (
        df["race_id"].nunique(), len(df), df["date"].min().date(), df["date"].max().date()))

    feature_cols = ([c + "_score" for c, _ in br.FEATURES]
                    + [c + "_missing" for c, _ in br.FEATURES]
                    + ["field_size", "handicap", "place_places"])
    d = df["race_date"]
    parts = {
        "train": df[d < "2023-01-01"],
        "calibration": df[(d >= "2023-01-01") & (d < "2024-07-01")],
        "test": df[d >= "2024-07-01"],
    }
    packed = {}
    for name, sub in parts.items():
        if sub.empty:
            raise SystemExit("empty %s split" % name)
        packed[name] = br.pack(sub, feature_cols)
        print("%-12s %6d races %8d runners" % (name, len(packed[name][5]), len(sub)))

    _, Xtr, st_tr, sz_tr, _, _ = packed["train"]
    sub_tr, _, _, _, _, _ = packed["train"]
    ytr = sub_tr["placed"].to_numpy(dtype=float)
    mean = Xtr.mean(axis=0)
    std = Xtr.std(axis=0)
    std[std == 0] = 1.0

    def scale(X):
        return (X - mean) / std

    print("fitting binary top-k model ...")
    beta, res = fit_binary(scale(Xtr), ytr, maxiter=args.max_iter)
    print("fit: %s | iterations=%d" % (res.message, res.nit))

    sub_cal, Xc, st_c, sz_c, _, _ = packed["calibration"]
    z_cal = scale(Xc) @ beta
    places_cal = sub_cal.iloc[st_c]["place_places"].to_numpy(dtype=float)
    ycal = sub_cal["placed"].to_numpy(dtype=float)
    best_t, best_ll = 1.0, float("inf")
    for t in np.arange(0.5, 3.01, 0.05):
        p = offset_probs(z_cal / t, st_c, sz_c, places_cal)
        ll = binary_metrics(p, ycal)["log_loss"]
        if ll < best_ll:
            best_ll, best_t = ll, float(t)
    print("calibration temperature: %.2f | calibration log loss %.4f" % (best_t, best_ll))

    # Test the model on every place race, then on the priced subset so the
    # market top-k is compared on exactly the same races.
    sub_test, Xt, st_t, sz_t, _, _ = packed["test"]
    places_test = sub_test.iloc[st_t]["place_places"].to_numpy(dtype=float)
    ytest = sub_test["placed"].to_numpy(dtype=float)
    ptest = offset_probs((scale(Xt) @ beta) / best_t, st_t, sz_t, places_test)
    model_all = binary_metrics(ptest, ytest)
    model_capture, model_races = topk_capture(ptest, st_t, sz_t, ytest)

    priced = sub_test["market_complete"].to_numpy()
    keep = pd.Series(priced).groupby(sub_test["race_id"].to_numpy()).transform("all").to_numpy()
    sub_p = sub_test[keep].reset_index(drop=True)
    sub_p, Xp, st_p, sz_p, _, _ = br.pack(sub_p, feature_cols)
    yp = sub_p["placed"].to_numpy(dtype=float)
    places_p = sub_p.iloc[st_p]["place_places"].to_numpy(dtype=float)
    z_p = scale(Xp) @ beta
    p_p = offset_probs(z_p / best_t, st_p, sz_p, places_p)
    market_p = br.market_probs(sub_p)
    model_capture_p, model_p_races = topk_capture(p_p, st_p, sz_p, yp)
    market_capture, market_races = topk_capture(market_p, st_p, sz_p, yp)

    # Each-way EV uses the calibrated place model and the market-implied win
    # probability. That is a place-model test, not a claim about a win edge.
    ew_bets = ew_profit = ew_stake = 0
    ew_pos = 0
    positions = sub_p["pos"].to_numpy(dtype=float)
    winners = (positions == 1.0)
    for a, size in zip(st_p, sz_p):
        b = a + size
        k = int(sub_p.iloc[a]["place_places"])
        frac = float(sub_p.iloc[a]["place_fraction"])
        if k <= 0 or frac <= 0:
            continue
        dec = sub_p["sp_dec"].to_numpy(dtype=float)[a:b]
        q = 1.0 / dec
        q = q / q.sum()
        pp = p_p[a:b]
        yy = yp[a:b]
        for i in range(size):
            ev = eachway.each_way_ev(float(q[i]), float(pp[i]), float(dec[i]), frac)
            if ev is None or ev <= 0:
                continue
            ew_pos += 1
            ew_bets += 1
            ew_stake += 2.0
            dplace = eachway.place_decimal(float(dec[i]), frac)
            if winners[a + i]:
                ew_profit += float(dec[i]) + dplace - 2.0
            elif yy[i] == 1.0:
                ew_profit += dplace - 2.0
            else:
                ew_profit -= 2.0

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "raceform.db (free Kaggle UK/Ireland archive)",
        "date_range": [str(df["date"].min().date()), str(df["date"].max().date())],
        "target": "finished in the paid places under standard GB/IRE each-way terms",
        "splits": {k: {"races": int(len(v[5])), "runners": int(len(v[0]))}
                   for k, v in packed.items()},
        "features": {c: float(b) for c, b in zip(feature_cols, beta)},
        "temperature": best_t,
        "test": {
            "races": int(len(st_t)),
            "placed_runners": int(ytest.sum()),
            "model": dict(model_all, topk_capture_pct=model_capture, topk_races=model_races),
            "priced_races": int(len(st_p)),
            "priced_model_capture_pct": model_capture_p,
            "priced_market_capture_pct": market_capture,
            "each_way": {
                "positive_ev_bets": ew_pos,
                "stake_units": ew_stake,
                "profit_units": ew_profit,
                "roi_pct": (100.0 * ew_profit / ew_stake) if ew_stake else None,
            },
        },
        "caveat": ("SP is a post-time benchmark. The archive's RPR/TS are earned in "
                   "the race, so the model uses the previous known figures. Terms are "
                   "standard GB/IRE terms; enhanced bookmaker terms differ."),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nTEST: %d place races; model log loss %.4f | Brier %.4f | ECE %.4f" % (
        len(st_t), model_all["log_loss"], model_all["brier"], model_all["ece"]))
    print("Top-k capture: model %.1f%% | market (priced races) %.1f%%" % (
        model_capture, market_capture))
    if ew_stake:
        print("Each-way EV > 0: %d bets | profit %.1f units | ROI %+.1f%%" % (
            ew_bets, ew_profit, 100.0 * ew_profit / ew_stake))
    print("\nwrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
