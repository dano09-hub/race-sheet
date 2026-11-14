#!/usr/bin/env python3
"""Market-aware direct top-k place model.

    python3 tooling/backtest_places_market.py --db /path/to/raceform.db

The free archive only contains the starting price, not the current morning
price. This test therefore uses the race-normalised SP as the odds proxy:
it is the same kind of input the live page gets from the current card, but it
is sharper and later than a real decision-time price. Do not read the result
as an edge over SP.

Three direct top-k binary models are fitted on the same priced races:

  market only      normalised SP probability + logit + terms
  ratings only     the pre-race rating features + terms
  market + ratings both sets

Each model gets a temperature and a per-race offset chosen only on the
calibration period, so its place probabilities sum to the paid places. The
untouched test period is compared with the SP top-k baseline on exactly the
same races. Research dependencies: pandas, numpy, scipy.
"""
import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_places as bp  # noqa: E402
import backtest_ratings as br  # noqa: E402
import eachway  # noqa: E402

ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "data", "place_model_market.json")


def add_market_features(df):
    df = df.copy()
    dec = pd.to_numeric(df["sp_dec"], errors="coerce").to_numpy(dtype=float)
    q = np.where(np.isfinite(dec) & (dec > 1.0), 1.0 / dec, np.nan)
    df["market_raw"] = q
    group = df.groupby("race_id", sort=False)["market_raw"]
    total = group.transform("sum")
    df["market_prob"] = df["market_raw"] / total
    p = df["market_prob"].clip(1e-6, 1.0 - 1e-6)
    df["market_logit"] = np.log(p / (1.0 - p))
    # A plain rank is useful when prices are missing but a partial market is
    # available; complete-market rows are still the only ones used here.
    df["market_rank"] = group.rank(method="first")
    return df


def build_features():
    base = ([c + "_score" for c, _ in br.FEATURES]
            + [c + "_missing" for c, _ in br.FEATURES]
            + ["field_size", "handicap", "place_places"])
    market = ["market_prob", "market_logit"]
    return base, market, base + market


def model_pack(sub, cols):
    sub, X, starts, sizes, _, _ = br.pack(sub, cols)
    return sub, X, starts, sizes


def fit_and_calibrate(name, cols, parts, max_iter):
    print("fitting %s ..." % name)
    sub_tr, Xtr, st_tr, sz_tr = model_pack(parts["train"], cols)
    ytr = sub_tr["placed"].to_numpy(dtype=float)
    mean = Xtr.mean(axis=0)
    std = Xtr.std(axis=0)
    std[std == 0] = 1.0

    def scale(X):
        return (X - mean) / std

    beta, res = bp.fit_binary(scale(Xtr), ytr, maxiter=max_iter)
    print("  fit: %s | iterations=%d" % (res.message, res.nit))

    sub_cal, Xc, st_c, sz_c = model_pack(parts["calibration"], cols)
    places_cal = sub_cal.iloc[st_c]["place_places"].to_numpy(dtype=float)
    ycal = sub_cal["placed"].to_numpy(dtype=float)
    z_cal = scale(Xc) @ beta
    best_t, best_ll = 1.0, float("inf")
    for t in np.arange(0.5, 3.01, 0.05):
        p = bp.offset_probs(z_cal / t, st_c, sz_c, places_cal)
        ll = bp.binary_metrics(p, ycal)["log_loss"]
        if ll < best_ll:
            best_ll, best_t = ll, float(t)
    print("  temperature %.2f | calibration log loss %.4f" % (best_t, best_ll))

    sub_test, Xt, st_t, sz_t = model_pack(parts["test"], cols)
    places_t = sub_test.iloc[st_t]["place_places"].to_numpy(dtype=float)
    p_test = bp.offset_probs((scale(Xt) @ beta) / best_t, st_t, sz_t, places_t)
    return {
        "features": cols,
        "coefficients": {c: float(b) for c, b in zip(cols, beta)},
        "standardisation": {
            "mean": {c: float(v) for c, v in zip(cols, mean)},
            "std": {c: float(v) for c, v in zip(cols, std)},
        },
        "temperature": best_t,
        "calibration_log_loss": best_ll,
        "sub_test": sub_test,
        "p_test": p_test,
        "st_test": st_t,
        "sz_test": sz_t,
    }


def each_way_test(sub, starts, sizes, p_place):
    """Settle positive-EV each-way bets using the market win probability."""
    positions = sub["pos"].to_numpy(dtype=float)
    winners = positions == 1.0
    bets = profit = stake = 0
    for a, size in zip(starts, sizes):
        k = int(sub.iloc[a]["place_places"])
        frac = float(sub.iloc[a]["place_fraction"])
        if k <= 0 or frac <= 0:
            continue
        dec = sub["sp_dec"].to_numpy(dtype=float)[a:a + size]
        q = 1.0 / dec
        q = q / q.sum()
        for i in range(size):
            ev = eachway.each_way_ev(float(q[i]), float(p_place[a + i]),
                                     float(dec[i]), frac)
            if ev is None or ev <= 0:
                continue
            bets += 1
            stake += 2.0
            dplace = eachway.place_decimal(float(dec[i]), frac)
            if winners[a + i]:
                profit += float(dec[i]) + dplace - 2.0
            elif positions[a + i] <= k:
                profit += dplace - 2.0
            else:
                profit -= 2.0
    return {
        "positive_ev_bets": bets,
        "stake_units": stake,
        "profit_units": profit,
        "roi_pct": (100.0 * profit / stake) if stake else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "raceform.db"))
    ap.add_argument("--max-iter", type=int, default=300)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        raise SystemExit("raceform.db not found at %s\nDownload the free Kaggle dataset or pass --db." % args.db)

    print("loading %s ..." % args.db)
    df = add_market_features(bp.add_place_labels(br.load(args.db)))
    # Fair comparison: every model sees exactly the priced races.
    df = df[df["market_complete"]].copy()
    print("priced place races: %d | runners: %d | dates: %s to %s" % (
        df["race_id"].nunique(), len(df), df["date"].min().date(), df["date"].max().date()))

    base, market, both = build_features()
    d = df["race_date"]
    parts = {
        "train": df[d < "2023-01-01"],
        "calibration": df[(d >= "2023-01-01") & (d < "2024-07-01")],
        "test": df[d >= "2024-07-01"],
    }
    for name, sub in parts.items():
        print("%-12s %6d races %8d runners" % (name, sub["race_id"].nunique(), len(sub)))

    models = {
        "market_only": fit_and_calibrate("market_only", market + ["field_size", "handicap", "place_places"],
                                         parts, args.max_iter),
        "ratings_only": fit_and_calibrate("ratings_only", base, parts, args.max_iter),
        "market_ratings": fit_and_calibrate("market_ratings", both, parts, args.max_iter),
    }

    test_sub = models["market_ratings"]["sub_test"]
    starts = models["market_ratings"]["st_test"]
    sizes = models["market_ratings"]["sz_test"]
    labels = test_sub["placed"].to_numpy(dtype=float)
    market_p = br.market_probs(test_sub)
    market_capture, market_races = bp.topk_capture(market_p, starts, sizes, labels)

    results = {}
    for name, m in models.items():
        p = m["p_test"]
        capture, races = bp.topk_capture(p, starts, sizes, labels)
        results[name] = dict(
            bp.binary_metrics(p, labels),
            topk_capture_pct=capture,
            topk_races=races,
            temperature=m["temperature"],
        )

    # Market-only and market+ratings EV both use the same market win probability.
    ew = {
        "market_only": each_way_test(test_sub, starts, sizes, models["market_only"]["p_test"]),
        "market_ratings": each_way_test(test_sub, starts, sizes, models["market_ratings"]["p_test"]),
    }

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "raceform.db (free Kaggle UK/Ireland archive)",
        "odds_proxy": "starting price (SP), race-normalised; not a decision-time price",
        "date_range": [str(df["date"].min().date()), str(df["date"].max().date())],
        "target": "finished in the paid places under standard GB/IRE each-way terms",
        "splits": {k: {"races": int(v["race_id"].nunique()), "runners": int(len(v))}
                   for k, v in parts.items()},
        "models": {k: {
            "features": v["features"],
            "coefficients": v["coefficients"],
            "standardisation": v["standardisation"],
            "temperature": v["temperature"],
            "calibration_log_loss": v["calibration_log_loss"],
        } for k, v in models.items()},
        "test": {
            "races": int(len(starts)),
            "placed_runners": int(labels.sum()),
            "market_topk_capture_pct": market_capture,
            "market_topk_races": market_races,
            "models": results,
            "each_way": ew,
            "market_topk_is_sp_rank": True,
        },
        "caveat": ("SP is post-time and sharper than a current card price. The test "
                   "shows whether ratings add anything to the market's place ranking; "
                   "it is not a claim of a live pre-race edge. Terms are standard "
                   "GB/IRE terms, not enhanced bookmaker offers."),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nTEST: %d priced races; SP top-k %.1f%%" % (len(starts), market_capture))
    for name in ("market_only", "ratings_only", "market_ratings"):
        m = results[name]
        print("%-14s top-k %5.1f%% | logloss %.4f | Brier %.4f | ECE %.4f" % (
            name, m["topk_capture_pct"], m["log_loss"], m["brier"], m["ece"]))
    for name in ("market_only", "market_ratings"):
        e = ew[name]
        if e["stake_units"]:
            print("%-14s EV>0 %6d bets | ROI %+6.1f%%" % (
                name, e["positive_ev_bets"], e["roi_pct"]))
    print("\nwrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
