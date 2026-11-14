#!/usr/bin/env python3
"""Course/going front-runner bias: does a prior front-runner improve where the
local history says front-runners outperform the market?

This is a separate hypothesis from the individual-horse pace test.  For every
race it asks:

    did prior front-runners at this course / going / course+going win more often
    than their SP-implied chance?

The statistic is built only from earlier races.  It is market-adjusted (actual
front-runner wins minus the normalised SP probability those same runners had)
and shrunk toward the global front-runner excess, so a course with three runners
does not produce a huge bias.  The current race's result is applied only after
all features for all runners have been emitted.

    python3 tooling/backtest_course_bias.py
    -> data/backtest_course_bias.json

No live card, weight or pick is changed.  The base model is the seven-factor
proxy from backtest_pace.py; the live twelve-factor page cannot be reconstructed
from the free result archive (no pre-race OR/RPR/Topspeed, draw or weight).
"""
import argparse
import collections
import datetime as dt
import glob
import json
import math
import os
import re

import numpy as np

import backtest_pace as bp

OUT = os.path.join(bp.ROOT, "data", "backtest_course_bias.json")
ALPHA = 20.0                     # pseudo-runners of shrinkage
FRONT_THRESHOLD = 0.5           # prior led-rate needed to call a horse a front-runner

BASE_FEATURES = list(bp.BASE_FACTORS)
FRONT_FEATURES = ("front", "front_known")
BIAS_FEATURES = ("front_course_bias", "front_going_bias", "front_cg_bias")

VARIANTS = {
    "base": (),
    "base_front": FRONT_FEATURES,
    "base_front_course": FRONT_FEATURES + ("front_course_bias",),
    "base_front_going": FRONT_FEATURES + ("front_going_bias",),
    "base_front_cg": FRONT_FEATURES + ("front_cg_bias",),
    "base_front_all": FRONT_FEATURES + BIAS_FEATURES,
}


def going_token(value):
    text = re.sub(r"[^A-Za-z ]", " ", str(value or "")).strip().upper()
    return text.split()[0] if text else None


def blank_stat():
    return {"runs": 0, "wins": 0, "market": 0.0}


def shrunk_excess(stat, prior, alpha=ALPHA):
    """Prior front-runner excess, shrunk toward the global excess.

    A runner with no local sample returns the global prior, so its local bias
    is zero.  The numerator is wins minus the SP probability the same front
    runners carried; positive means they beat the market.
    """
    if not stat or not stat["runs"]:
        return prior
    return (stat["wins"] - stat["market"] + prior * alpha) / (stat["runs"] + alpha)


def pos_num(value):
    m = re.match(r"^\s*(\d+)", str(value or ""))
    return int(m.group(1)) if m else None


def is_actual_runner(r):
    return str(r.get("pos", "")).strip().upper() not in ("", "NR", "WD", "W")


def build_bias_index(pace_index):
    """Return pre-race course/going bias features for every raw runner."""
    stats = {"global": blank_stat(), "course": {}, "going": {}, "cg": {}}
    index = {}
    counts = collections.Counter()
    for path in sorted(glob.glob(bp.RAW_GLOB)):
        date = os.path.basename(path)[:10]
        with open(path, encoding="utf-8") as f:
            day = json.load(f)
        for meeting in day.get("meetings") or []:
            course = bp.norm_course(meeting.get("course"))
            for race in sorted(meeting.get("races") or [], key=lambda r: str(r.get("time", ""))):
                runners = [r for r in race.get("runners") or [] if is_actual_runner(r)]
                if len(runners) < 2:
                    continue
                going = going_token(race.get("going"))
                dec = [bp.parse_sp(r.get("sp")) for r in runners]
                complete = all(v is not None and v > 1.0 for v in dec)

                global_stat = stats["global"]
                g_runs = global_stat["runs"]
                prior = ((global_stat["wins"] - global_stat["market"]) / g_runs) if g_runs else 0.0
                course_stat = stats["course"].get(course)
                going_stat = stats["going"].get(going) if going else None
                cg_stat = stats["cg"].get((course, going)) if going else None
                course_ex = shrunk_excess(course_stat, prior)
                going_ex = shrunk_excess(going_stat, prior)
                cg_ex = shrunk_excess(cg_stat, prior)

                front_flags = []
                for i, r in enumerate(runners):
                    key = (date, course, str(race.get("time", "")), bp.norm_horse(r.get("horse")))
                    p = pace_index.get(key) or {}
                    led = p.get("pace_led")
                    known = led is not None
                    is_front = known and float(led) >= FRONT_THRESHOLD
                    front_flags.append(is_front)
                    counts["runners"] += 1
                    counts["front_runners"] += int(is_front)
                    counts["front_known"] += int(known)
                    index[key] = {
                        "front": 1.0 if is_front else 0.0,
                        "front_known": 1.0 if known else 0.0,
                        "front_course_bias": (course_ex - prior) if is_front else 0.0,
                        "front_going_bias": (going_ex - prior) if is_front else 0.0,
                        "front_cg_bias": (cg_ex - prior) if is_front else 0.0,
                    }

                # Update the historical bias after every runner's feature has
                # been written, using only races with a complete priced field.
                if complete:
                    winners = [i for i, r in enumerate(runners) if pos_num(r.get("pos")) == 1]
                    if len(winners) == 1:
                        q = np.asarray([1.0 / v for v in dec], dtype=float)
                        q = q / q.sum()
                        cstat = stats["course"].setdefault(course, blank_stat())
                        gstat = stats["going"].setdefault(going, blank_stat()) if going else None
                        cgstat = stats["cg"].setdefault((course, going), blank_stat()) if going else None
                        win = winners[0]
                        for i, front in enumerate(front_flags):
                            if not front:
                                continue
                            for stat in (global_stat, cstat, gstat, cgstat):
                                if stat is None:
                                    continue
                                stat["runs"] += 1
                                stat["wins"] += int(i == win)
                                stat["market"] += float(q[i])
                        counts["complete_races"] += 1
                counts["races"] += 1
    counts["global_front_runs"] = stats["global"]["runs"]
    counts["courses_with_stats"] = sum(1 for s in stats["course"].values() if s["runs"])
    counts["goings_with_stats"] = sum(1 for s in stats["going"].values() if s["runs"])
    counts["cg_with_stats"] = sum(1 for s in stats["cg"].values() if s["runs"])
    return index, counts


def attach_bias(races, index):
    empty = {"front": 0.0, "front_known": 0.0,
             "front_course_bias": 0.0, "front_going_bias": 0.0, "front_cg_bias": 0.0}
    for rec in races:
        key = rec["key"]
        rec["bias"] = [
            index.get((key[0], key[1], key[2], bp.norm_horse(r["horse"])), empty)
            for r in rec["rows"]
        ]
    return races


def design(races, extras=()):
    X, starts, sizes, winners, dates = [], [], [], [], []
    at = 0
    for rec in races:
        for i, r in enumerate(rec["rows"]):
            vals = []
            for f in BASE_FEATURES:
                v = r["_base"].get(f)
                vals.extend([math.nan if v is None else float(v), 0.0 if v is not None else 1.0])
            for f in extras:
                vals.append(float(rec["bias"][i].get(f, 0.0)))
            X.append(vals)
        starts.append(at)
        sizes.append(len(rec["rows"]))
        winners.append(at + rec["winner"])
        dates.append(rec["key"][0])
        at += len(rec["rows"])
    return np.asarray(X, dtype=float), np.asarray(starts, dtype=int), \
        np.asarray(sizes, dtype=int), np.asarray(winners, dtype=int), dates


def fit_eval(train, cal, test, extras, max_iter=300):
    Xtr, str_tr, siz_tr, win_tr, _ = design(train, extras)
    Xca, str_ca, siz_ca, win_ca, _ = design(cal, extras)
    Xte, str_te, siz_te, win_te, _ = design(test, extras)
    Xtr = np.nan_to_num(Xtr, nan=0.0)
    Xca = np.nan_to_num(Xca, nan=0.0)
    Xte = np.nan_to_num(Xte, nan=0.0)
    mean = Xtr.mean(axis=0)
    std = Xtr.std(axis=0)
    std[std == 0] = 1.0
    Xtr = (Xtr - mean) / std
    Xca = (Xca - mean) / std
    Xte = (Xte - mean) / std
    beta, _ = bp.fit_logit(Xtr, str_tr, siz_tr, win_tr, maxiter=max_iter)
    dec_ca = np.concatenate([r["dec"] for r in cal])
    dec_te = np.concatenate([r["dec"] for r in test])
    out = {}
    for name, X, st, sz, win, part, dec in (
            ("calibration", Xca, str_ca, siz_ca, win_ca, cal, dec_ca),
            ("test", Xte, str_te, siz_te, win_te, test, dec_te)):
        out[name] = bp.metrics(bp.softmax(X, beta, st, sz), st, sz, win, dec)
    return out, beta


def test_arrays(test):
    starts = np.cumsum([0] + [len(r["rows"]) for r in test[:-1]])
    sizes = np.asarray([len(r["rows"]) for r in test])
    winners = np.asarray([starts[i] + test[i]["winner"] for i in range(len(test))])
    dec = np.concatenate([r["dec"] for r in test])
    return starts, sizes, winners, dec


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-iter", type=int, default=300)
    args = ap.parse_args()

    print("building prior pace history ...")
    pace_index, pace_counts = bp.build_pace_index()
    print("  %d raw races, %d runners, %d with prior pace"
          % (pace_counts["races"], pace_counts["runners"], pace_counts["with_history"]))

    print("building causal course/going front-runner bias ...")
    bias_index, bias_counts = build_bias_index(pace_index)
    print("  %d complete priced races, %d global front runs, %d courses / %d goings / %d course-going cells"
          % (bias_counts["complete_races"], bias_counts["global_front_runs"],
             bias_counts["courses_with_stats"], bias_counts["goings_with_stats"],
             bias_counts["cg_with_stats"]))

    races = attach_bias(bp.load_races(pace_index), bias_index)
    priced = []
    for rec in races:
        d = rec["dec"]
        if all(v is not None and v > 1.0 for v in d):
            rec["dec"] = np.asarray(d, dtype=float)
            priced.append(rec)
    dates = [r["key"][0] for r in priced]
    train_mask, cal_mask, test_mask = bp.date_split(dates)
    train, cal, test = (bp.subset(priced, train_mask), bp.subset(priced, cal_mask),
                        bp.subset(priced, test_mask))
    print("  train %d, calibration %d, test %d" % (len(train), len(cal), len(test)))
    if not train or not cal or not test:
        raise SystemExit("not enough races in a split")

    models = {}
    for name, extras in VARIANTS.items():
        print("fitting %s ..." % name)
        result, beta = fit_eval(train, cal, test, extras, max_iter=args.max_iter)
        models[name] = {"test": result["test"], "calibration": result["calibration"],
                        "coefficients": dict(zip(
                            [x for f in BASE_FEATURES for x in (f, f + "_missing")] + list(extras),
                            [float(v) for v in beta]))}

    starts_test, sizes_test, winners_test, dec_test = test_arrays(test)
    market = bp.metrics(bp.market_probs(test), starts_test, sizes_test, winners_test, dec_test)
    current = bp.fixed_metrics(test)

    def delta(a, b, metric):
        return models[a]["test"][metric] - models[b]["test"][metric]

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "data/2026-*.json comments + data/dataset.csv.gz",
        "purpose": "research-only test of market-adjusted course/going front-runner bias",
        "method": {
            "definition": "prior front-runner = horse led/disputed in at least half of its last-six starts",
            "bias": "prior wins minus normalised SP probability for front-runners, shrunk toward global with alpha=20",
            "features": list(FRONT_FEATURES + BIAS_FEATURES),
            "base_factors": BASE_FEATURES,
            "splits": {"train": "< 2026-09-01", "calibration": "2026-09-01..2026-10-15",
                       "test": ">= 2026-10-16"},
            "model": "conditional logit, ridge 1.0, standardised on train",
        },
        "coverage": {
            "raw_races": pace_counts["races"],
            "raw_runners": pace_counts["runners"],
            "runners_with_prior_pace": pace_counts["with_history"],
            "front_runners": bias_counts["front_runners"],
            "front_known": bias_counts["front_known"],
            "complete_priced_races": bias_counts["complete_races"],
            "global_front_runs": bias_counts["global_front_runs"],
            "courses_with_stats": bias_counts["courses_with_stats"],
            "goings_with_stats": bias_counts["goings_with_stats"],
            "cg_with_stats": bias_counts["cg_with_stats"],
            "priced_races": len(priced),
            "train_races": len(train),
            "calibration_races": len(cal),
            "test_races": len(test),
        },
        "metrics": {
            "market": market,
            "current_fixed": current,
            **{name: models[name]["test"] for name in VARIANTS},
        },
        "calibration": {name: models[name]["calibration"] for name in VARIANTS},
        "deltas_test": {
            "front_vs_base_log_loss": delta("base_front", "base", "log_loss"),
            "course_vs_front_log_loss": delta("base_front_course", "base_front", "log_loss"),
            "going_vs_front_log_loss": delta("base_front_going", "base_front", "log_loss"),
            "cg_vs_front_log_loss": delta("base_front_cg", "base_front", "log_loss"),
            "all_vs_front_log_loss": delta("base_front_all", "base_front", "log_loss"),
            "front_vs_base_top1": delta("base_front", "base", "top1"),
            "course_vs_front_top1": delta("base_front_course", "base_front", "top1"),
            "going_vs_front_top1": delta("base_front_going", "base_front", "top1"),
            "cg_vs_front_top1": delta("base_front_cg", "base_front", "top1"),
            "all_vs_front_top1": delta("base_front_all", "base_front", "top1"),
            "front_vs_base_top3": delta("base_front", "base", "top3"),
            "course_vs_front_top3": delta("base_front_course", "base_front", "top3"),
            "going_vs_front_top3": delta("base_front_going", "base_front", "top3"),
            "cg_vs_front_top3": delta("base_front_cg", "base_front", "top3"),
            "all_vs_front_top3": delta("base_front_all", "base_front", "top3"),
        },
        "coefficients": {name: models[name]["coefficients"] for name in VARIANTS},
        "caveat": (
            "The live page has 12 factors; this is the seven-factor result-archive proxy. "
            "The bias is market-adjusted and shrunk (alpha=20), so small course/going cells "
            "cannot dominate. SP is a post-time benchmark. No live card, weight or pick changes."
        ),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nTEST (%d priced races)" % len(test))
    for label in ("market", "current_fixed", "base", "base_front", "base_front_course",
                  "base_front_going", "base_front_cg", "base_front_all"):
        m = out["metrics"][label]
        print("%-18s top1 %5.1f%% | top3 %5.1f%% | logloss %s | Brier %s | ROI %s"
              % (label, m["top1"], m["top3"],
                 ("%.4f" % m["log_loss"]) if "log_loss" in m else "   —  ",
                 ("%.4f" % m["brier"]) if "brier" in m else "   —  ",
                 ("%+6.1f%%" % m["roi_sp"]) if m.get("roi_sp") is not None else "   —  "))
    print("\nbias deltas vs base_front: course logloss %+.4f, going %+.4f, cg %+.4f, all %+.4f"
          % (out["deltas_test"]["course_vs_front_log_loss"],
             out["deltas_test"]["going_vs_front_log_loss"],
             out["deltas_test"]["cg_vs_front_log_loss"],
             out["deltas_test"]["all_vs_front_log_loss"]))
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
