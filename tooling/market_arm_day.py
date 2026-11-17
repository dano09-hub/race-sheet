#!/usr/bin/env python3
"""Snapshot the market-heavy shadow pick for a day, next to the published pick.

Fitting the twelve weights on four years of results (tooling/import_kaggle.py ->
tooling/train_weights.py) produced one honest gain and one that is not: held-out
top-1 rose 2.3 points, but almost all of it came from moving weight onto the
market (18 -> 51). This arm records that vector's pick every day so the two can
be compared on real races before anyone decides anything.

It never touches the page, the weights or the stored pick. The cards are scored
once by the page's own model, and the arm re-weights the page's own factor scores
with the fitted vector - so there is no second copy of a single factor.

usage:
    python3 tooling/market_arm_day.py 2026-11-18
    python3 tooling/market_arm_day.py --backfill 8
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import settle  # noqa: E402

OUT_DIR = "picks_market_ab"
SUBJECT = "market-heavy weighting"
REGIONS = ("GB", "IRE")

# Fitted on 50,196 races (2023-01-01 .. 2026-11-15), held-out to the last 60 race
# days: log loss 1.8819 -> 1.8117, top-1 32.6% -> 34.9%, level with the market's
# 35.0%. tips, rpr and speed were held at their live values because the source
# cannot observe them; going and weight were driven to zero.
WEIGHTS = {
    "market": 50.8, "form": 5.9, "tips": 5.0, "rpr": 13.0, "rating": 3.4,
    "cd": 3.6, "draw": 0.9, "speed": 12.0, "going": 0.0, "fitness": 3.4,
    "weight": 0.0, "connections": 6.1,
}


def _norm(value):
    text = re.sub(r"\(.*?\)", "", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def rank(parts_list, weights=None):
    """Index of the best runner under `weights`, or None if nothing scores.

    This is the page's own weighted average over the factors a runner actually
    has, applied to the page's own 0-100 factor scores.
    """
    weights = weights or WEIGHTS
    best, best_score = None, None
    for i, parts in enumerate(parts_list):
        num = den = 0.0
        for key, weight in weights.items():
            value = (parts or {}).get(key)
            if value is None or not weight:
                continue
            num += float(value) * weight
            den += weight
        if not den:
            continue
        score = num / den
        if best_score is None or score > best_score:
            best, best_score = i, score
    return best


def pick(card_info, weights=None):
    rows = card_info.get("runners") or []
    i = rank([r.get("parts") or {} for r in rows], weights)
    return rows[i]["horse"] if i is not None else ""


def main(date, force=False):
    path = os.path.join(ROOT, "data", OUT_DIR, f"{date}.json")
    if os.path.exists(path) and not force:
        print(f"{date}: market-arm snapshot already taken, left as-is")
        return 0

    cards = [c for c in settle._enrich(settle._cards_for(date), date)
             if c.get("region") in REGIONS]
    if not cards:
        print(f"{date}: no GB/IRE cards, nothing to snapshot")
        return 1
    scored = settle.score_cards(cards)

    rows, changed, seen, scored_races = [], 0, set(), 0
    for card, info in zip(cards, scored):
        live = info.get("pick") or ""
        arm = pick(info)
        if not live or not arm:
            continue
        key = (_norm(card.get("course")), str(card.get("time") or "")[:5])
        if key in seen:
            continue          # one pick per race, however the archive duplicates it
        seen.add(key)
        scored_races += 1
        differs = arm != live
        changed += differs
        rows.append({"course": card.get("course"), "time": card.get("time"),
                     "withRating": arm, "withoutRating": live, "changed": differs})

    if not rows:
        print(f"{date}: no usable GB/IRE races, snapshot skipped")
        return 1

    out = {
        "date": date,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": ("Market-heavy shadow: the page's own factor scores re-weighted "
                 "with the vector fitted on four years of results"),
        "metric": "weights",
        "label": SUBJECT,
        "weights": WEIGHTS,
        "cards": len(rows),
        "same": scored_races - changed,
        "changed": changed,
        "ratedRunners": sum(len(c.get("runners") or []) for c in cards),
        "runners": sum(len(c.get("runners") or []) for c in cards),
        "rows": rows,
    }
    os.makedirs(os.path.join(ROOT, "data", OUT_DIR), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"{date}: {len(rows)} GB/IRE races shadowed, {changed} picks differ -> {path}")
    return 0


def backfill(days):
    """Snapshot recent settled days that have no arm yet, oldest first."""
    import glob
    dates = sorted(os.path.basename(p)[:-5] for p in
                   glob.glob(os.path.join(ROOT, "data", "results", "*.json")))
    dates = [d for d in dates if re.match(r"^\d{4}-\d{2}-\d{2}$", d)][-days:]
    made = 0
    for date in dates:
        if not os.path.exists(os.path.join(ROOT, "data", OUT_DIR, f"{date}.json")):
            made += main(date) == 0
    print(f"backfilled {made} of {len(dates)} settled days")
    return 0 if made or dates else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=dt.date.today().isoformat())
    ap.add_argument("--backfill", type=int, default=0,
                    help="snapshot the most recent N settled days that lack one")
    ap.add_argument("--force", action="store_true", help="rewrite an existing snapshot")
    args = ap.parse_args()
    if args.backfill:
        sys.exit(backfill(args.backfill))
    sys.exit(main(args.date, force=args.force))
