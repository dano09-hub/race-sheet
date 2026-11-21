#!/usr/bin/env python3
"""Is the Model percentage still honest? Read it off the settled ledger.

The page prints a calibrated win chance beside each runner (a softmax over the
field's totals at scale 0.04). Every race settled from now on stores the chance
its pick was shown at, so the claim can be checked without re-scoring anything:
the stated chances are averaged and compared with the Wilson interval around
the pick's actual win rate.

Two samples matter. Each interval is the Wilson interval at the record's own
win rate (`half_widths`), and the distance the printed chance moves between the
0.04 the page applies and the four-year 0.0498 is measured by re-scoring the
archived cards (`scale_gap`), so the two figures the panel compares are never
typed in. At 350 races the interval is about the size of that gap, so the sample
only just begins to separate the scales; at 1,600 it is comfortably narrower. The panel says which of
the three stages it is in rather than calling a wide interval a result.

    python3 tooling/chance_audit.py
"""
import functools
import glob
import json
import os
import statistics
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from calibrate_share import softmax, wilson  # noqa: E402  (one of each)

EARLY = 350
DECISIVE = 1600
SCALE = 0.04          # the softmax scale the page prints
FOUR_YEAR = 0.0498    # the scale fitted on the four-year history


def field_summary(results_dir=None, minimum=50):
    """Whole-field calibration from frozen cards, once there is enough of it."""
    try:
        import field_audit
    except Exception:
        return None
    rows = field_audit.from_snapshots(results_dir)
    if len(rows) < minimum:
        return None
    return field_audit.audit(rows)


def _summary(rows, target=EARLY):
    """The stated chance against the pick's record over a set of settled rows."""
    n = len(rows)
    if not n:
        return {"races": 0, "target": target}
    wins = sum(1 for row in rows if row["hit"])
    stated = sum(row["chance"] for row in rows) / n
    lo, hi = wilson(wins, n)
    return {
        "races": n, "wins": wins, "rate": wins / n, "stated": stated,
        "lo": lo, "hi": hi, "half": (hi - lo) / 2,
        "first": rows[0]["date"], "last": rows[-1]["date"],
        "inside": lo <= stated <= hi,
        "more_early": max(0, EARLY - n),
        "more_decisive": max(0, DECISIVE - n),
        "target": EARLY,
    }


def collect(results_dir=None):
    """Every settled GB/IRE race that stored the chance its pick was shown at.

    This is the twelve-factor record kept in data/results; collect_rated() reads
    the six-input re-score instead.
    """
    root = results_dir or os.path.join(ROOT, "data", "results")
    rows = []
    for iso, races in _days(root):
        for row in races:
            if not _record_row(row):
                continue
            rows.append({"date": iso, "chance": float(row["chance"]),
                         "hit": bool(row.get("hit"))})
    out = _summary(rows)
    out["field"] = field_summary(results_dir)
    return out


def collect_rated(results_dir=None):
    """The same audit over the six-input re-score in data/results_rated."""
    root = results_dir or os.path.join(ROOT, "data", "results_rated")
    rows = []
    for iso, races in _days(root):
        for row in races:
            if not _rated_row(row):
                continue
            rows.append({"date": iso, "chance": float(row["chance"]),
                         "hit": bool(row.get("hit"))})
    out = _summary(rows)
    out["rated"] = True
    return out


def _days(root):
    """(iso, stored races) for every settled day in a results directory."""
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        races = data.get("races", []) if isinstance(data, dict) else data
        yield os.path.basename(path)[:-5], races or []


def _key(race):
    """A race's course and off time: how a stored row names its card."""
    return (race.get("course"), str(race.get("time") or "")[:5])


def _decimals(sem):
    """The decimal places a gap's own sample error supports."""
    return 1 if sem < 0.1 else 0


def _gap_from(fields):
    """The pick's chance at 0.04 and at 0.0498 over a list of field totals.

    The gap is rounded to the precision its own sample supports: a tenth of a
    point is finer than the standard error over a few dozen matched races, so
    such a gap prints in whole points rather than as a measured-looking 3.0.
    """
    if not fields:
        return {"races": 0, "gap": 0.0, "early": 0.0, "late": 0.0,
                "sem": 0.0, "decimals": 1}
    early = [float(max(softmax(np.asarray(f) * SCALE))) for f in fields]
    late = [float(max(softmax(np.asarray(f) * FOUR_YEAR))) for f in fields]
    spread = [100 * (b - a) for a, b in zip(early, late)]
    n = len(spread)
    sem = statistics.stdev(spread) / n ** 0.5 if n > 1 else float("inf")
    decimals = _decimals(sem)
    return {"races": n,
            "early": 100 * sum(early) / n,
            "late": 100 * sum(late) / n,
            "gap": round(sum(spread) / n, decimals),
            "sem": sem, "decimals": decimals}


def gap_points(gap):
    """The measured gap in points, at the precision its sample supports."""
    if not gap or not gap.get("races"):
        return "the gap"
    return f"{gap['gap']:.{gap.get('decimals', 1)}f} points"


def scale_verdict(early, gap):
    """How the interval at a sample size stands against the measured gap.

    One sentence for the page and the README, so the threshold that decides it
    cannot drift between the two.
    """
    if early >= gap:
        return ("the interval at that size is still wider than the gap, so it "
                "catches a gross error but cannot yet tell the scales apart")
    if early >= gap - 1:
        return ("the interval at that size is about the size of the gap, so it "
                "catches a gross error and only just begins to tell the "
                "scales apart")
    return ("the interval at that size is already narrower than the gap, so "
            "it can tell the scales apart")


def _matched_fields(rows, scored_for):
    """Field totals for each row whose stored pick still re-scores.

    A row whose card no longer reproduces its pick is left out: its totals
    belong to a card the page never showed.
    """
    fields = []
    for row in rows:
        got = scored_for(row)
        if not got or got.get("pick") != row["pick"]:
            continue
        totals = [r.get("total") for r in got.get("runners") or []]
        if not totals or any(t is None for t in totals):
            continue
        fields.append([float(t) for t in totals])
    return fields


def _gap_scan(root, keep, match_day):
    """Measure the two scales over every day whose cards can be re-scored.

    `keep` says whether a stored row carries this model's pick and chance;
    `match_day(iso)` returns a row -> re-scored-race lookup for that day, or
    None when its cards are gone.
    """
    fields = []
    for iso, rows in _days(root):
        rows = [row for row in rows if keep(row)]
        if not rows:
            continue
        scored_for = match_day(iso)
        if scored_for:
            fields += _matched_fields(rows, scored_for)
    return _gap_from(fields)


def _record_row(row):
    """The twelve-factor record stores a chance for GB/IRE picks only."""
    return (row.get("region") != "US" and row.get("pick")
            and row.get("chance") is not None)


def _rated_row(row):
    """The six-input re-score stores a chance only for a rated pick."""
    return (row.get("state") == "rated" and row.get("pick")
            and row.get("chance") is not None)


@functools.lru_cache(maxsize=4)
def _scale_gap_record(results_dir=None):
    """The twelve-factor record's pick at 0.04 and at the four-year 0.0498.

    A stored row carries only the 0.04 number, so the other scale needs the
    field's totals: each settled day with an archived card is re-scored with
    the page's own scorer and matched to the pick on record. Only days that
    still have their cards can be read, which is why this covers fewer races
    than the ledger it is quoted beside.
    """
    import backfill_chance      # lazy: it imports refresh, which imports this
    import settle

    def match_day(iso):
        cards = settle._cards_for(iso)
        if not cards:
            return None
        return backfill_chance._resolver(cards, settle.score_cards(cards))

    return _gap_scan(results_dir or os.path.join(ROOT, "data", "results"),
                     _record_row, match_day)


@functools.lru_cache(maxsize=4)
def _scale_gap_rated(results_dir=None):
    """The same gap on the six-input re-score, which carries the page's chance."""
    import rescore_results
    import settle

    def match_day(iso):
        cards = rescore_results.day_cards(iso)
        if not cards:
            return None
        scored = {_key(c): s
                  for c, s in zip(cards, settle.score_rated_cards(cards))
                  if s and s.get("rated")}
        return lambda row: scored.get(_key(row))

    return _gap_scan(results_dir or os.path.join(ROOT, "data", "results_rated"),
                     _rated_row, match_day)


def scale_gap(rated=False, results_dir=None):
    """How far apart 0.04 and 0.0498 print the same picks, in points.

    The gap is the model's own: the six-input re-score spreads its totals
    differently from the twelve-factor record, so the panel quotes the gap of
    the model whose chance it is auditing.
    """
    return (_scale_gap_rated(results_dir) if rated
            else _scale_gap_record(results_dir))


def half_widths(a):
    """The Wilson half-width the record's rate would give at each sample size."""
    if not a or not a.get("rate"):
        return {EARLY: 0.0, DECISIVE: 0.0}
    out = {}
    for n in (EARLY, DECISIVE):
        lo, hi = wilson(round(a["rate"] * n), n)
        out[n] = (hi - lo) / 2
    return out


def verdict(a):
    """'inside', 'low' or 'high': where the stated chance falls."""
    if not a or not a.get("races"):
        return "empty"
    if a["inside"]:
        return "inside"
    return "low" if a["stated"] < a["lo"] else "high"


def main():
    a = collect()
    if not a["races"]:
        print("no settled race has a stored chance yet")
        return 0
    print(f"{a['first']}..{a['last']}: {a['races']} settled GB/IRE races")
    print(f"  pick shown at {a['stated']:.1f}%, won {a['wins']} "
          f"({a['rate'] * 100:.1f}%, 95% CI {a['lo']:.1f}-{a['hi']:.1f}%, "
          f"half-width {a['half']:.1f} points)")
    print(f"  stated chance is {verdict(a)} the interval")
    gap = scale_gap(rated=bool(a.get("rated")))
    widths = half_widths(a)
    if gap["races"]:
        print(f"  the 0.04 and 0.0498 scales put the same picks "
              f"{gap_points(gap)} apart over {gap['races']} rescored races "
              f"({gap['early']:.1f}% against {gap['late']:.1f}%)")
    print(f"  Wilson half-width {widths[EARLY]:.1f} points at {EARLY} races, "
          f"{widths[DECISIVE]:.1f} at {DECISIVE}")
    if a["more_early"]:
        print(f"  informative read in {a['more_early']} more races")
    if a["more_decisive"]:
        print(f"  decisive read in {a['more_decisive']} more races "
              f"(interval about +-{widths[DECISIVE]:.1f} points at "
              f"{DECISIVE})")
    else:
        print(f"  sample is large enough ({DECISIVE}+) to test the scale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
