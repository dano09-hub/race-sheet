#!/usr/bin/env python3
"""Snapshot the with/without-rating picks for a day, before anyone knows the result.

Run this on race morning (or any time after the cards exist). It writes
data/picks_ab/<date>.json, which tooling/score_ab.py and tooling/ab_tally.py
score against the settled winners later. The snapshot never reads results, so
it cannot peek.

usage: python3 tooling/ab_day.py 2026-11-15
"""
import datetime as dt, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import settle  # noqa: E402


def main(date):
    cards = settle._enrich(settle._cards_for(date), date)
    if not cards:
        print(f"{date}: no cards, nothing to snapshot")
        return 1
    rated_cards = [c for c in cards if any(str(r.get("rpr", "")).strip() for r in c.get("runners", []))]
    if not rated_cards:
        print(f"{date}: cards carry no rating, A/B would be degenerate; skipped")
        return 1

    with_rating = settle.score_cards(cards)
    stripped = []
    for c in cards:
        c2 = json.loads(json.dumps(c))
        for r in c2.get("runners", []):
            r["rpr"] = ""
        stripped.append(c2)
    without = settle.score_cards(stripped)

    rows, same, changed = [], 0, 0
    for a, b in zip(with_rating, without):
        if not a.get("pick") or not b.get("pick"):
            continue
        ch = a["pick"] != b["pick"]
        same += (not ch)
        changed += ch
        rows.append({"course": a["course"], "time": a["time"],
                     "withRating": a["pick"], "withoutRating": b["pick"], "changed": ch})

    out = {
        "date": date,
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "note": "A/B of the twelve-factor model with and without a rating in the rpr slot",
        "cards": len(rows),
        "same": same,
        "changed": changed,
        "ratedRunners": sum(1 for c in cards for r in c.get("runners", []) if str(r.get("rpr", "")).strip()),
        "runners": sum(len(c.get("runners", [])) for c in cards),
        "rows": rows,
    }
    os.makedirs(os.path.join(ROOT, "data", "picks_ab"), exist_ok=True)
    path = os.path.join(ROOT, "data", "picks_ab", f"{date}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1)
    print(f"{date}: {len(rows)} races snapshotted, {changed} picks differ "
          f"({out['ratedRunners']}/{out['runners']} runners rated) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()))
