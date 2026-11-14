#!/usr/bin/env python3
"""Score archived cards against settled results.

    python3 tooling/measure.py                 # every day we have both halves for
    python3 tooling/measure.py 2026-11-13

Reads data/cards/<date>.json (written by refresh.py) and data/results/<date>.json
({course: {race_no: {winner, places:[...]}}}), replays the page's ranking for each
card, and reports how often the top pick won, was placed, and how often the race
was inside our top 3 -- overall and per region. This is the only honest way to
tune weights: measure, then change one thing.
"""
import json, os, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def load(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rank(runners, weights):
    """Mirror of the page's model: weighted mean of the factors a card supplies."""
    def num(v):
        try:
            return float(str(v).strip())
        except Exception:
            return None
    vals = {}
    for i, r in enumerate(runners):
        parts = {}
        odds = num(r.get("odds"))
        parts["market"] = (1 / odds) if odds and odds > 0 else None
        parts["rating"] = num(r.get("or"))
        parts["speed"] = num(r.get("ts"))
        parts["rpr"] = num(r.get("rpr"))
        parts["draw"] = -num(r.get("draw")) if num(r.get("draw")) else None
        parts["fitness"] = -num(r.get("days")) if num(r.get("days")) else None
        vals[i] = parts
    scored = []
    for i in range(len(runners)):
        num_sum = den = 0.0
        for k, w in weights.items():
            v = vals[i].get(k)
            if v is None:
                continue
            col = [vals[j].get(k) for j in vals if vals[j].get(k) is not None]
            lo, hi = min(col), max(col)
            n = 50.0 if hi == lo else 100.0 * (v - lo) / (hi - lo)
            num_sum += n * w
            den += w
        scored.append((num_sum / den if den else 50.0, i))
    scored.sort(reverse=True)
    return [i for _, i in scored]


WEIGHTS = {"market": 18, "rating": 15, "rpr": 13, "speed": 12, "draw": 4, "fitness": 3}


def report(date, cards, results):
    rows = []
    for region, rs in cards.items():
        for card in rs:
            res = (results.get(card.get("course")) or {}).get(str(card.get("label", "")))
            if not res:
                continue
            order = rank(card["runners"], WEIGHTS)
            names = [card["runners"][i]["horse"] for i in order]
            winner, places = res.get("winner"), set(res.get("places") or [])
            rows.append({
                "region": region, "course": card.get("course"),
                "won": names[0] == winner if winner else False,
                "top3": bool(set(names[:3]) & places),
                "placed": names[0] in places,
            })
    return rows


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else None
    carddir = os.path.join(ROOT, "data", "cards")
    if not os.path.isdir(carddir):
        print("no archived cards yet - run tooling/refresh.py first")
        return
    allrows = []
    for f in sorted(os.listdir(carddir)):
        date = f[:-5]
        if want and date != want:
            continue
        cards = load(os.path.join(carddir, f))
        results = load(os.path.join(ROOT, "data", "results", f))
        if not cards or not results:
            continue
        rows = report(date, cards, results)
        allrows += rows
        print(f"{date}: {len(rows)} scored races")
    if not allrows:
        print("no day has both a card archive and results yet")
        return
    n = len(allrows)
    won = sum(r["won"] for r in allrows)
    placed = sum(r["placed"] for r in allrows)
    top3 = sum(r["top3"] for r in allrows)
    print(f"\noverall: {n} races | top pick won {won} ({100*won/n:.0f}%) | "
          f"placed {placed} ({100*placed/n:.0f}%) | winner in our top 3 {top3} ({100*top3/n:.0f}%)")
    for region in sorted({r["region"] for r in allrows}):
        sub = [r for r in allrows if r["region"] == region]
        if not sub:
            continue
        k = len(sub)
        print(f"  {region:12} {k:4} races | won {sum(x['won'] for x in sub):3} "
              f"({100*sum(x['won'] for x in sub)/k:.0f}%) | top3 "
              f"{100*sum(x['top3'] for x in sub)/k:.0f}%")


if __name__ == "__main__":
    main()
