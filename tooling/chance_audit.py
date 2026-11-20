#!/usr/bin/env python3
"""Is the Model percentage still honest? Read it off the settled ledger.

The page prints a calibrated win chance beside each runner (a softmax over the
field's totals at scale 0.04). Every race settled from now on stores the chance
its pick was shown at, so the claim can be checked without re-scoring anything:
the stated chances are averaged and compared with the Wilson interval around
the pick's actual win rate.

Two samples matter. At 350 races the interval is about +-4.8 points, and the
0.04 and 0.0498 scales put the same picks four and a half to five points apart,
so the interval is about the size of the gap it has to resolve: that sample
catches a gross error and only just begins to tell the scales apart. At 1600
races the interval is about +-2.3 points and the question can actually be
settled. The panel says which of
the three stages it is in rather than calling a wide interval a result.

    python3 tooling/chance_audit.py
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from calibrate_share import wilson  # noqa: E402  (percentages, one definition)

EARLY = 350
DECISIVE = 1600


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
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        races = data.get("races", []) if isinstance(data, dict) else data
        for row in races:
            if row.get("region") == "US":
                continue
            if not row.get("pick") or row.get("chance") is None:
                continue
            rows.append({"date": os.path.basename(path)[:-5],
                         "chance": float(row["chance"]),
                         "hit": bool(row.get("hit"))})
    out = _summary(rows)
    out["field"] = field_summary(results_dir)
    return out


def collect_rated(results_dir=None):
    """The same audit over the six-input re-score in data/results_rated."""
    root = results_dir or os.path.join(ROOT, "data", "results_rated")
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for row in data.get("races") or []:
            if row.get("state") != "rated":
                continue
            if not row.get("pick") or row.get("chance") is None:
                continue
            rows.append({"date": os.path.basename(path)[:-5],
                         "chance": float(row["chance"]),
                         "hit": bool(row.get("hit"))})
    out = _summary(rows)
    out["rated"] = True
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
    if a["more_early"]:
        print(f"  informative read in {a['more_early']} more races")
    if a["more_decisive"]:
        print(f"  decisive read in {a['more_decisive']} more races "
              f"(interval about +-2 points at {DECISIVE})")
    else:
        print(f"  sample is large enough ({DECISIVE}+) to test the scale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
