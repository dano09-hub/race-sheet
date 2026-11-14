#!/usr/bin/env python3
"""Market-aware direct top-k place model on true pre-race Betfair prices.

    python3 tooling/backtest_places_betfair.py \
        --db /path/to/raceform.db \
        --prices /path/to/betfair_win_csvs

The free race archive has only the industry SP. Betfair's free daily price
files contain MORNINGWAP (weighted average of bets before 11am GMT), PPWAP
(pre-off weighted average) and BSP. This script joins them to raceform by
date, horse and off-time, then compares:

  * the Betfair market's top-k
  * a market-only direct top-k model
  * a ratings-only model
  * a market + ratings direct top-k model

The 2020-2024 files on the free mirror end in April 2024, so the chronological
test is 2022-07 to 2024-04. No raw price or race rows are written to the repo.
Research dependencies: pandas, numpy, scipy.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_places as bp  # noqa: E402
import backtest_places_market as bpm  # noqa: E402
import backtest_ratings as br  # noqa: E402

ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "data", "place_model_betfair.json")


def norm_horse(value):
    s = str(value).lower().strip()
    s = re.sub(r"\([^)]*\)", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def off_minutes(value):
    m = re.match(r"^(\d{1,2}):(\d{2})", str(value).strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else np.nan


def load_betfair(path):
    files = sorted(glob.glob(os.path.join(path, "*win*.csv")))
    if not files:
        raise SystemExit("no Betfair win CSVs in " + path)
    frames = []
    skipped = 0
    for f in files:
        try:
            d = pd.read_csv(f, dtype=str, on_bad_lines="skip")
        except Exception:
            skipped += 1
            continue
        d.columns = [str(c).strip().upper() for c in d.columns]
        if "EVENT_ID" not in d.columns or "SELECTION_NAME" not in d.columns:
            skipped += 1
            continue
        frames.append(d)
    if not frames:
        raise SystemExit("no usable Betfair CSVs in " + path)
    d = pd.concat(frames, ignore_index=True)
    d["event_dt"] = pd.to_datetime(d["EVENT_DT"], dayfirst=True, errors="coerce")
    d = d.dropna(subset=["event_dt"]).copy()
    d["date"] = d["event_dt"].dt.normalize()
    d["off_min"] = d["event_dt"].dt.hour * 60 + d["event_dt"].dt.minute
    d["horse_norm"] = d["SELECTION_NAME"].map(norm_horse)
    for c in ("BSP", "PPWAP", "MORNINGWAP", "PPTRADEDVOL"):
        d[c] = pd.to_numeric(d.get(c), errors="coerce")
    # One price row per runner/race. Duplicates are rare; keep the most traded.
    d = d.sort_values("PPTRADEDVOL", ascending=False).drop_duplicates(
        ["date", "horse_norm", "off_min"])
    print("Betfair files: %d used, %d skipped; rows: %d; dates: %s to %s" % (
        len(frames), skipped, len(d), d["date"].min().date(), d["date"].max().date()))
    return d[["date", "horse_norm", "off_min", "BSP", "PPWAP", "MORNINGWAP"]]


def join_races(db, prices_path):
    rf = br.load(db)
    rf = rf[(rf["date"] >= "2020-01-01") & (rf["date"] < "2025-01-01")].copy()
    rf["date"] = pd.to_datetime(rf["date"]).dt.normalize()
    rf["horse_norm"] = rf["horse"].map(norm_horse)
    rf["off_min"] = rf["off"].map(off_minutes)
    print("raceform 2020-2024: %d runners, %d races" % (
        len(rf), rf["race_id"].nunique()))

    bf = load_betfair(prices_path)
    bf = bf[(bf["date"] >= "2020-01-01") & (bf["date"] < "2025-01-01")].copy()
    m = rf.merge(bf, on=["date", "horse_norm"], how="left", suffixes=("", "_bf"))
    # raceform writes "3:24" for 15:24; compare the two clocks modulo 12h.
    m["off_diff"] = ((m["off_min"] % 720) - (m["off_min_bf"] % 720)).abs()
    m = m.sort_values(["race_id", "num", "off_diff"]).drop_duplicates(
        ["race_id", "num"], keep="first")
    m["matched"] = m["off_diff"] <= 15
    all_matched = m.groupby("race_id", sort=False)["matched"].transform("all")
    m = m[all_matched].copy()
    print("matched: %d runners, %d races (off-time tolerance 15m)" % (
        len(m), m["race_id"].nunique()))

    valid = pd.Series(True, index=m.index)
    for c in ("MORNINGWAP", "PPWAP", "BSP"):
        valid &= m[c].notna() & (m[c] > 1.0)
    common = valid.groupby(m["race_id"], sort=False).transform("all")
    m = m[common].copy()
    print("common valid prices: %d runners, %d races" % (
        len(m), m["race_id"].nunique()))

    # Build terms from the complete field, then keep races with a paid place.
    m = bp.add_place_labels(m)
    m = m.sort_values(["date", "race_id", "num"], kind="mergesort").reset_index(drop=True)
    m["race_date"] = m["date"]
    print("place races: %d; runners: %d" % (m["race_id"].nunique(), len(m)))
    return m


def price_frame(df, col):
    out = df.copy()
    out["sp_dec"] = out[col]
    out["market_complete"] = True
    return bpm.add_market_features(out)


def split_parts(df):
    d = df["race_date"]
    return {
        "train": df[d < "2022-01-01"],
        "calibration": df[(d >= "2022-01-01") & (d < "2022-07-01")],
        "test": df[d >= "2022-07-01"],
    }


def market_probs(sub, col):
    q = 1.0 / sub[col].to_numpy(dtype=float)
    group = sub["race_id"].to_numpy()
    starts = np.unique(group, return_index=True)[1]
    sizes = np.diff(np.append(starts, len(sub)))
    total = np.add.reduceat(q, starts)
    return q / np.repeat(total, sizes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "raceform.db"))
    ap.add_argument("--prices", required=True,
                    help="directory of Betfair daily win CSV files")
    ap.add_argument("--max-iter", type=int, default=300)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        raise SystemExit("raceform.db not found at %s" % args.db)

    print("loading %s ..." % args.db)
    df = join_races(args.db, args.prices)
    parts = split_parts(df)
    for name, sub in parts.items():
        if sub.empty:
            raise SystemExit("empty %s split" % name)
        print("%-12s %6d races %8d runners" % (name, sub["race_id"].nunique(), len(sub)))

    base, market, both = bpm.build_features()
    results = {}
    test_meta = None

    for label, col in (("morning", "MORNINGWAP"), ("preoff", "PPWAP")):
        print("\n=== %s price (%s) ===" % (label, col))
        priced = price_frame(df, col)
        pparts = split_parts(priced)
        market_cols = market + ["field_size", "handicap", "place_places"]
        market_model = bpm.fit_and_calibrate(
            label + "_market", market_cols, pparts, args.max_iter)
        combined = bpm.fit_and_calibrate(
            label + "_market_ratings", both, pparts, args.max_iter)
        sub = combined["sub_test"]
        starts, sizes = combined["st_test"], combined["sz_test"]
        labels = sub["placed"].to_numpy(dtype=float)
        mkt_p = market_probs(sub, col)
        mkt_capture, _ = bp.topk_capture(mkt_p, starts, sizes, labels)
        mo_capture, _ = bp.topk_capture(market_model["p_test"], starts, sizes, labels)
        mr_capture, _ = bp.topk_capture(combined["p_test"], starts, sizes, labels)
        ew = bpm.each_way_test(sub, starts, sizes, combined["p_test"])
        results[label] = {
            "market_topk_capture_pct": mkt_capture,
            "market_only_topk_capture_pct": mo_capture,
            "market_ratings_topk_capture_pct": mr_capture,
            "market_only": bp.binary_metrics(market_model["p_test"], labels),
            "market_ratings": bp.binary_metrics(combined["p_test"], labels),
            "temperature": combined["temperature"],
            "each_way": ew,
            "features": combined["features"],
            "coefficients": combined["coefficients"],
            "standardisation": combined["standardisation"],
        }
        print("  market top-k %.1f%% | market-only %.1f%% | market+ratings %.1f%%" % (
            mkt_capture, mo_capture, mr_capture))
        if ew["stake_units"]:
            print("  each-way EV>0 %d bets | ROI %+.1f%%" % (
                ew["positive_ev_bets"], ew["roi_pct"]))
        test_meta = (sub, starts, sizes, labels)

    # Ratings-only on exactly the same races as the combined models.
    print("\n=== ratings only ===")
    ratings_parts = split_parts(price_frame(df, "MORNINGWAP"))
    ratings_model = bpm.fit_and_calibrate("ratings_only", base, ratings_parts, args.max_iter)
    sub, starts, sizes, labels = test_meta
    ratings_p, _ = bp.topk_capture(ratings_model["p_test"], starts, sizes, labels)
    ratings_metrics = bp.binary_metrics(ratings_model["p_test"], labels)

    # BSP reference on the same common test races.
    bsp_capture, _ = bp.topk_capture(market_probs(sub, "BSP"), starts, sizes, labels)

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "raceform.db (free Kaggle archive) + Betfair public daily price files",
        "price_source": "Betfair MORNINGWAP, PPWAP and BSP; free public CSV files via Kaggle mirror",
        "date_range": [str(df["date"].min().date()), str(df["date"].max().date())],
        "test_range": [str(parts["test"]["date"].min().date()),
                       str(parts["test"]["date"].max().date())],
        "target": "finished in the paid places under standard GB/IRE each-way terms",
        "splits": {k: {"races": int(v["race_id"].nunique()), "runners": int(len(v))}
                   for k, v in parts.items()},
        "odds": {
            "MORNINGWAP": "Betfair weighted average of bets before 11am GMT",
            "PPWAP": "Betfair weighted average of all bets before the off",
            "BSP": "Betfair Starting Price",
            "not_industry_sp": True,
        },
        "test": {
            "races": int(len(starts)),
            "placed_runners": int(labels.sum()),
            "bsp_market_topk_capture_pct": bsp_capture,
            "ratings_only": dict(ratings_metrics, topk_capture_pct=ratings_p,
                                 temperature=ratings_model["temperature"]),
            "prices": results,
        },
        "caveat": ("Betfair's morning quote is an average of matched exchange bets, "
                   "not a specific bookmaker price available to every user. The mirror "
                   "ends in April 2024. SP is still a reference, not a decision-time "
                   "input. Terms are standard GB/IRE terms."),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nTEST: %d races; BSP top-k %.1f%% | ratings-only %.1f%%" % (
        len(starts), bsp_capture, ratings_p))
    print("\nwrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
