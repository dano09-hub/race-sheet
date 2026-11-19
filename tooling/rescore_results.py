#!/usr/bin/env python3
"""Re-score settled races with the page's strict six-input model.

Reads each settled day in data/results (UK/IRE/US) and data/results_hk, scores
the archived card with tooling/score_rated.js -- the page's own SCORE_RATED_CARD
-- and writes data/results_rated/<date>.json. Nothing in data/results is
changed: the pre-race pick on record stays exactly where it is, and the re-score
is a separate, clearly labelled view.

A race whose archived card is missing, or whose card has any runner missing any
of the six inputs, is written as not rated with the gap named. No pick is
inferred for it. Each unrated race also records `blocker`: the one field missing
from most runners in that race, with `blocker_only` set when it is the only gap
anywhere in the field, so the run can say which single input would unlock the
most races.

usage: python3 tooling/rescore_results.py [YYYY-MM-DD ...]
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNNER = os.path.join(HERE, "score_rated.js")
OUT_DIR = os.path.join(ROOT, "data", "results_rated")

REGIONS = ("GB", "IRE", "US", "AU", "HK", "FR", "CL")
POS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th",
       7: "7th", 8: "8th", 9: "9th", 10: "10th"}


def norm(value):
    s = str(value or "").lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def day_cards(date):
    """Every archived card for the racing day, tagged with its region group."""
    path = os.path.join(ROOT, "data", "cards", "by_date", date + ".json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return data
    cards = []
    for region in REGIONS:
        for card in data.get(region) or []:
            card = dict(card)
            card["region"] = card.get("region") or ("IRE" if region == "IRE" else region)
            cards.append(card)
    return cards


def score(cards):
    if not cards:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "cards.json")
        dst = os.path.join(tmp, "rated.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(cards, f)
        subprocess.run(["node", RUNNER, src, dst, os.path.join(ROOT, "index.html")],
                       check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        with open(dst, encoding="utf-8") as f:
            return json.load(f)


def result_rows(date):
    """(region, row) for every settled race on the day."""
    rows = []
    path = os.path.join(ROOT, "data", "results", date + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data = data.get("races", []) if isinstance(data, dict) else data
        for row in data:
            rows.append((row.get("region") or "", row))
    hk = os.path.join(ROOT, "data", "results_hk", date + ".json")
    if os.path.exists(hk):
        with open(hk, encoding="utf-8") as f:
            data = json.load(f)
        data = data.get("races", []) if isinstance(data, dict) else data
        for row in data:
            rows.append(("HK", row))
    return rows


def finish_of(row, horse):
    """The horse's finishing position as a label, or '' when not on file."""
    want = norm(horse)
    finish = row.get("finish") or {}
    for name, pos in finish.items():
        if norm(name) == want:
            try:
                pos = int(pos)
            except (TypeError, ValueError):
                return str(pos)
            return POS.get(pos, "unplaced")
    for key in ("order", "places"):
        seq = row.get(key) or []
        for i, name in enumerate(seq):
            if norm(name) == want:
                return POS.get(i + 1, "unplaced")
    if row.get("winner") and norm(row["winner"]) == want:
        return "1st"
    return ""


def place_terms(row):
    """How many places the stored result pays, or None for win-only."""
    pp = row.get("place_places")
    if pp == 0:
        return None
    if pp is not None:
        try:
            return int(pp)
        except (TypeError, ValueError):
            pass
    if (row.get("region") or "").upper() == "US":
        return 3          # the page's US convention
    if (row.get("region") or "").upper() == "HK":
        return 3
    return None


def blocker_of(entry):
    """The one input that keeps this race out, and whether it is the only one.

    The page orders a card's gaps by how many runners lack the field, so the
    first entry is the field the most horses are missing. Filling it alone only
    rates the race when no other field is missing anywhere in the field.
    """
    if entry["state"] == "rated":
        return "", False
    gaps = entry.get("missing") or []
    what = (gaps[0].get("what") if gaps else "") or "unrecorded gap"
    # The scorer's own failure and an unnamed gap are not inputs, so filling
    # nothing would rate those races: they never count as a sole gap.
    only = (entry["state"] == "incomplete" and len(gaps) == 1
            and what not in ("a factor did not score", "unrecorded gap"))
    return what, only


def position_number(label):
    m = re.match(r"(\d+)", str(label or ""))
    return int(m.group(1)) if m else None


def pick_card(cards, row):
    course, time, winner = norm(row.get("course")), str(row.get("time") or "")[:5], norm(row.get("winner"))
    pool = [c for c in cards if norm(c.get("course")) == course]
    for card in pool:
        if str(card.get("time") or "")[:5] == time:
            return card
    for card in pool:
        if any(norm(r.get("horse")) == winner for r in card.get("runners", [])):
            return card
    return None


def day(date):
    cards = day_cards(date)
    scored = score(cards)
    by_id = {id(c): s for c, s in zip(cards, scored)}
    out = []
    for region, row in result_rows(date):
        card = pick_card(cards, row)
        got = by_id.get(id(card)) if card else None
        entry = {
            "course": row.get("course", ""),
            "time": str(row.get("time") or "")[:5],
            "winner": row.get("winner", ""),
            "region": region or (card or {}).get("region", ""),
            "state": "no-card",
            "pick": "",
            "chance": None,
            "finish": "",
            "hit": False,
            "placed": None,
            "missing": [],
        }
        if not card:
            entry["missing"] = [{"what": "no archived card", "n": 0}]
        elif not got or not got.get("rated"):
            entry["state"] = "incomplete"
            entry["missing"] = (got or {}).get("missing") or []
        else:
            pick = got.get("pick") or ""
            finish = finish_of(row, pick) if pick else ""
            terms = place_terms(row)
            pos = position_number(finish)
            entry.update({
                "state": "rated",
                "pick": pick,
                "chance": got.get("chance"),
                "finish": finish,
                "hit": bool(pick) and norm(pick) == norm(row.get("winner")),
                "place_places": terms,
                "placed": (pos <= terms) if (pos and terms) else None,
            })
        entry["blocker"], entry["blocker_only"] = blocker_of(entry)
        out.append(entry)
    return {"date": date, "model": "six-input", "races": out}


def print_breakdown(tally, no_card, unrated):
    """Rank the inputs that block unrated races, the ones that unlock first."""
    if not unrated:
        print("every settled race carries a six-input pick")
        return
    rows = sorted(tally.items(), key=lambda kv: (-kv[1]["only"], -kv[1]["races"], kv[0]))
    width = max([len(k) for k in tally] + [len("no archived card")])
    print()
    print(f"what keeps the {unrated} unrated races out "
          "(field missing from most runners in the race)")
    if rows:
        print(f"  {'':<{width}}  {'races':>6}  {'rates alone':>11}  regions")
    for what, slot in rows:
        regions = ", ".join(sorted(slot["regions"])) or "unlabelled"
        print(f"  {what:<{width}}  {slot['races']:>6}  {slot['only']:>11}  {regions}")
    if no_card["races"]:
        regions = ", ".join(sorted(no_card["regions"])) or "unlabelled"
        print(f"  {'no archived card':<{width}}  {no_card['races']:>6}  "
              f"{'':>11}  {regions}")
    blocked = sum(v["races"] for v in tally.values())
    single = sum(v["only"] for v in tally.values())
    if not blocked:
        print("  every unrated race here has no card to score")
        return
    print(f"  a single field rates {single} of them; the other "
          f"{blocked - single} miss more than one input"
          + (f", and {no_card['races']} have no card to score" if no_card["races"] else ""))


def main(dates):
    if not dates:
        dates = sorted(os.path.basename(p)[:-5]
                       for p in glob.glob(os.path.join(ROOT, "data", "results", "*.json")))
        dates += sorted(os.path.basename(p)[:-5]
                        for p in glob.glob(os.path.join(ROOT, "data", "results_hk", "*.json"))
                        if os.path.basename(p)[:-5] not in dates)
    os.makedirs(OUT_DIR, exist_ok=True)
    total = rated = 0
    tally, no_card = {}, {"races": 0, "regions": set()}
    for date in sorted(dates):
        got = day(date)
        total += len(got["races"])
        rated += sum(1 for r in got["races"] if r["state"] == "rated")
        for r in got["races"]:
            if r["state"] == "rated":
                continue
            region = r.get("region") or ""
            if r["state"] == "no-card":
                no_card["races"] += 1
                no_card["regions"].add(region)
                continue
            slot = tally.setdefault(r["blocker"],
                                    {"races": 0, "only": 0, "regions": set()})
            slot["races"] += 1
            slot["only"] += 1 if r["blocker_only"] else 0
            slot["regions"].add(region)
        path = os.path.join(OUT_DIR, date + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(got, f, ensure_ascii=False, indent=1)
        day_rated = [r for r in got["races"] if r["state"] == "rated"]
        wins = sum(1 for r in day_rated if r["hit"])
        print(f"{date}: {len(day_rated)}/{len(got['races'])} rated, "
              f"{wins} winner{'s' if wins != 1 else ''}")
    print(f"total: {rated} of {total} settled races carry a six-input pick")
    print_breakdown(tally, no_card, total - rated)


if __name__ == "__main__":
    main(sys.argv[1:])
