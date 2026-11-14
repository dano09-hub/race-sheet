#!/usr/bin/env python3
"""Score a saved with/without-rating A/B against the day's actual winners.

Reads data/picks_ab/<date>.json (written when the A/B was run) and the settled
results in data/results/<date>.json, then reports how many races each version
found. Refuses to invent a number when the day has not been settled yet.

usage: python3 tooling/score_ab.py 2026-11-14
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def main(date):
    abp = os.path.join(ROOT, "data", "picks_ab", f"{date}.json")
    rp = os.path.join(ROOT, "data", "results", f"{date}.json")
    if not os.path.exists(abp):
        print(f"no A/B snapshot for {date} (run the A/B before the races)")
        return 1
    if not os.path.exists(rp):
        print(f"{date} has not been settled yet: no data/results/{date}.json")
        print("nothing scored - run this again once the day's results are in")
        return 2

    ab = json.load(open(abp, encoding="utf-8"))
    rows = json.load(open(rp, encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("races", [])

    winners = {}
    for r in rows:
        w = r.get("winner")
        if not w:
            continue
        winners.setdefault((norm(r.get("course")), str(r.get("time", ""))[:5]), w)

    matched = 0
    with_hits = without_hits = both = neither = 0
    detail = []
    for x in ab.get("rows", []):
        key = (norm(x.get("course")), str(x.get("time", ""))[:5])
        w = winners.get(key)
        if not w:
            continue
        matched += 1
        a = x.get("withRating") == w
        b = x.get("withoutRating") == w
        with_hits += a
        without_hits += b
        both += (a and b)
        neither += (not a and not b)
        detail.append((x.get("course"), x.get("time"), w,
                       x.get("withRating"), a, x.get("withoutRating"), b))

    if not matched:
        print(f"{date}: results exist but no A/B race matched on course + time")
        return 3

    print(f"{date}: {matched} of {len(ab.get('rows', []))} A/B races settled")
    # the rating source differs by day (Racing Post Rating on archived cards,
    # Timeform rating from Racing TV going forward), so name the slot not the feed
    print(f"  with the rating:         {with_hits} winners found "
          f"({100*with_hits/matched:.1f}%)")
    print(f"  without the rating:      {without_hits} winners found "
          f"({100*without_hits/matched:.1f}%)")
    print(f"  both found / neither:    {both} / {neither}")
    print(f"  changed picks that won:  {sum(1 for d in detail if d[4] and not d[6])}")
    print(f"  changed picks that lost: {sum(1 for d in detail if d[6] and not d[4])}")
    for c, t, w, wa, ha, wb, hb in detail:
        if wa != wb:
            print(f"    {c} {t}: winner {w} | with {'HIT' if ha else 'miss'} | "
                  f"without {'HIT' if hb else 'miss'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "2026-11-14"))
