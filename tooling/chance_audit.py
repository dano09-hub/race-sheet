#!/usr/bin/env python3
"""Is the Model percentage still honest? Read it off the settled ledger.

The page prints a calibrated win chance beside each runner (a softmax over the
field's totals at scale 0.04). Every race settled from now on stores the chance
its pick was shown at, so the claim can be checked without re-scoring anything:
the stated chances are averaged and compared with the Wilson interval around
the pick's actual win rate.

Two samples matter. At 350 races the interval is about +-4.5 points, which can
catch a gross error but cannot choose between the 0.04 and 0.0498 scales, whose
pick chances differ by about four points. At 1600 races the interval is about
+-2 points and the question can actually be settled. The panel says which of
the three stages it is in rather than calling a wide interval a result.

    python3 tooling/chance_audit.py
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EARLY = 350
DECISIVE = 1600


def wilson(wins, n, z=1.96):
    """Wilson score interval for a binomial rate, as fractions."""
    if not n:
        return (0.0, 0.0)
    p = wins / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def collect(results_dir=None):
    """Every settled GB/IRE race that stored the chance its pick was shown at."""
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
    n = len(rows)
    if not n:
        return {"races": 0, "target": EARLY}
    wins = sum(1 for row in rows if row["hit"])
    stated = sum(row["chance"] for row in rows) / n
    lo, hi = wilson(wins, n)
    return {
        "races": n, "wins": wins, "rate": wins / n, "stated": stated,
        "lo": lo * 100, "hi": hi * 100, "half": (hi - lo) * 50,
        "first": rows[0]["date"], "last": rows[-1]["date"],
        "inside": lo * 100 <= stated <= hi * 100,
        "more_early": max(0, EARLY - n),
        "more_decisive": max(0, DECISIVE - n),
        "target": EARLY,
    }


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
