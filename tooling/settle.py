#!/usr/bin/env python3
"""Score a settled day from archived cards with the sheet's own model.

The twelve-factor scorer (FACTORS, compute(), PICK_FOR) lives in index.html and
is the only copy: tooling/score_cards.js runs that script in node against a DOM
stub and hands back the pick. Nothing is reimplemented here, so a pick rebuilt
from the archive cannot drift from the pick the page showed on the day.
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNNER = os.path.join(HERE, "score_cards.js")
RUNNER_RATED = os.path.join(HERE, "score_rated.js")


def _enrich(cards, date):
    """Merge Racing TV ratings, days and course/trip badges onto the cards.

    The archive keeps what the page held on the day; the RTV pull adds the
    fields Sporting Life does not publish. Matching is course + horse, since the
    two sources round times differently.
    """
    import re as _re
    rtv_path = os.path.join(ROOT, "data", "cards", f"{date}_rtv.json")
    if not os.path.exists(rtv_path):
        return cards
    try:
        rtv = json.load(open(rtv_path, encoding="utf-8")) or []
    except Exception:
        return cards

    def norm(s):
        s = _re.sub(r"\(.*?\)", "", str(s or "")).lower()
        s = _re.sub(r"[^a-z0-9 ]", "", s)
        return _re.sub(r"\s+", " ", s).strip()

    pool = {}
    for c in rtv:
        for r in c.get("runners", []):
            pool.setdefault((norm(c.get("course")), norm(r.get("horse"))), r)
    hits = 0
    for c in cards:
        for r in c.get("runners", []):
            src = pool.get((norm(c.get("course")), norm(r.get("horse"))))
            if not src:
                continue
            # Never merge "rpr" here: Racing TV's number is Timeform, not the
            # Racing Post Rating the card's rpr field holds.
            for k in ("days", "form", "weight", "course", "distance", "tips"):
                if not str(r.get(k, "") or "").strip() and str(src.get(k, "") or "").strip():
                    r[k] = src[k]
            r.setdefault("tf_rating", src.get("tf_rating", ""))
            r.setdefault("tf_pick", src.get("tf_pick", ""))
            hits += 1
    if hits:
        print(f"  enriched {hits} runners from Racing TV")
    return cards


def _cards_for(date):
    archive = os.path.join(ROOT, "data", "cards", "by_date", f"{date}.json")
    legacy = os.path.join(ROOT, "data", "cards", f"{date}_sl.json")
    if os.path.exists(archive):
        data = json.load(open(archive, encoding="utf-8"))
        if isinstance(data, dict):
            out = []
            for region in ("GB", "IRE", "US", "AU"):
                out.extend(data.get(region) or [])
            return out
        return data
    for name in (f"{date}_merged.json", f"{date}_rtv.json"):
        path = os.path.join(ROOT, "data", "cards", name)
        if os.path.exists(path):
            got = json.load(open(path, encoding="utf-8"))
            if got:
                return got
    return json.load(open(legacy, encoding="utf-8"))


def score_cards(cards):
    """Return a list of picks in card order, using the page's model."""
    if not cards:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "cards.json")
        dst = os.path.join(tmp, "picks.json")
        json.dump(cards, open(src, "w", encoding="utf-8"))
        subprocess.run(["node", RUNNER, src, dst, os.path.join(ROOT, "index.html")],
                       check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        return json.load(open(dst, encoding="utf-8"))


def score_rated_cards(cards):
    """Return the page's strict six-input scoring for each card, in order.

    A card with any gap comes back rated=False with the missing inputs named;
    it never receives an inferred pick.
    """
    if not cards:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "cards.json")
        dst = os.path.join(tmp, "rated.json")
        json.dump(cards, open(src, "w", encoding="utf-8"))
        subprocess.run(["node", RUNNER_RATED, src, dst, os.path.join(ROOT, "index.html")],
                       check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        return json.load(open(dst, encoding="utf-8"))


def scored(card):
    """The page's scored rows for one card, or None when it cannot rate it."""
    got = score_cards([card])
    return got[0] if got else None


def snapshot_of(p):
    """The page's own numbers for a card, kept beside the pick it produced.

    Stored at settlement so a later backfill reads what the model saw at the off
    — the totals and shares it printed, and the factor scores behind them —
    instead of re-scraping a card that may have moved since.
    """
    runners = []
    for r in p.get("runners", []):
        share = r.get("share")
        runners.append({
            "horse": r.get("horse", ""),
            "odds": r.get("odds", ""),
            "total": r.get("total"),
            "share": round(float(share), 5) if share is not None else None,
            "parts": r.get("parts") or {},
        })
    return {"course": p.get("course", ""), "time": p.get("time", ""),
            "pick": p.get("pick", ""), "chance": p.get("chance"),
            "runners": runners}


def pick_for(card):
    """Single-card convenience wrapper: (horse, chance percentage)."""
    p = scored(card) or {}
    return (p.get("pick") or None), p.get("chance")


def main(date):
    rpath = os.path.join(ROOT, "data", "results", f"{date}.json")
    if not os.path.exists(rpath):
        print(f"no settled results for {date}; nothing to pick")
        return
    cards = _enrich(_cards_for(date), date)
    picks = score_cards(cards)
    res = json.load(open(rpath, encoding="utf-8"))
    rows = res["races"] if isinstance(res, dict) else res

    by_time, by_winner = {}, {}
    for card, p in zip(cards, picks):
        if not p["pick"]:
            continue
        by_time[(p["course"], p["time"])] = p["pick"]
        for r in card.get("runners", []):
            if r["horse"] == p["pick"]:
                pass
        by_winner.setdefault(p["course"], []).append((card, p["pick"]))

    filled = 0
    for row in rows:
        if row.get("pick"):
            continue
        pick = by_time.get((row["course"], row["time"]))
        if not pick:
            for card, p in by_winner.get(row["course"], []):
                if any(r["horse"] == row["winner"] for r in card.get("runners", [])):
                    pick = p
                    break
        if not pick:
            continue
        row["pick"] = pick
        row["pick_finish"] = row.get("pick_finish") or ""
        row["hit"] = (pick == row["winner"])
        filled += 1

    out = res if isinstance(res, dict) else rows
    json.dump(out, open(rpath, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{filled} of {len(rows)} races got a pick from archived cards")
    print("hits:", sum(1 for r in rows if r.get("hit")))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-11-13")
