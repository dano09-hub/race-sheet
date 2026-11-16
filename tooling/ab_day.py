#!/usr/bin/env python3
"""Snapshot the with/without-signal picks for a day, before anyone knows the result.

The default is the UK/IRE A/B: the twelve-factor model with and without the
Timeform rating in the rpr slot. The same function can snapshot the US card
with and without the HRN speed figure in the ts slot.

Run this on race morning (or any time after the cards exist). It writes
data/<out_dir>/<date>.json, which tooling/ab_tally.py scores against the
settled winners later. The snapshot never reads results, so it cannot peek.

usage: python3 tooling/ab_day.py 2026-11-15
"""
import datetime as dt, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import settle  # noqa: E402


def main(date, metric="rpr", out_dir="picks_ab", label="rating", regions=None):
    cards = settle._enrich(settle._cards_for(date), date)
    if regions:
        cards = [c for c in cards if c.get("region") in regions]
    if not cards:
        print(f"{date}: no cards, nothing to snapshot")
        return 1
    signal_cards = [c for c in cards
                    if any(str(r.get(metric, "")).strip() for r in c.get("runners", []))]
    if not signal_cards:
        print(f"{date}: cards carry no {label}, A/B would be degenerate; skipped")
        return 1

    # The comparison is about the signal, so only races with at least one
    # usable value belong in the snapshot.
    cards = signal_cards

    with_signal = settle.score_cards(cards)
    stripped = []
    for c in cards:
        c2 = json.loads(json.dumps(c))
        for r in c2.get("runners", []):
            r[metric] = ""
        stripped.append(c2)
    without = settle.score_cards(stripped)

    rows, same, changed = [], 0, 0
    seen = set()
    for a, b in zip(with_signal, without):
        if not a.get("pick") or not b.get("pick"):
            continue
        key = (str(a.get("course") or "").strip().lower(),
               str(a.get("time") or "")[:5])
        if key in seen:
            continue  # two cards for one race must not both score the result
        seen.add(key)
        ch = a["pick"] != b["pick"]
        same += (not ch)
        changed += ch
        rows.append({"course": a["course"], "time": a["time"],
                     "withRating": a["pick"], "withoutRating": b["pick"], "changed": ch})

    out = {
        "date": date,
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "note": f"A/B of the twelve-factor model with and without a {label} in the {metric} slot",
        "metric": metric,
        "label": label,
        "cards": len(rows),
        "same": same,
        "changed": changed,
        "ratedRunners": sum(1 for c in cards for r in c.get("runners", [])
                            if str(r.get(metric, "")).strip()),
        "runners": sum(len(c.get("runners", [])) for c in cards),
        "rows": rows,
    }
    os.makedirs(os.path.join(ROOT, "data", out_dir), exist_ok=True)
    path = os.path.join(ROOT, "data", out_dir, f"{date}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1)
    print(f"{date}: {len(rows)} races snapshotted, {changed} picks differ "
          f"({out['ratedRunners']}/{out['runners']} runners with {label}) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()))
