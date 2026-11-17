#!/usr/bin/env python3
"""One row per runner, ready to fit a model: the page's own factor scores.

Every archived card is scored with the page's model (tooling/score_cards.js) and
joined to its settled result, so each row carries the twelve 0-100 factor scores,
the finishing position, the pre-race price and a winner label. This is the input
for tooling/train_weights.py. Nothing here changes the page, a pick or a stored
result.

    python3 tooling/export_training.py
    python3 tooling/export_training.py --out data/training/runners.csv

Columns: date, region, course, time, race_id, horse, field, pos, win, odds,
market_prob, rank, total, then one column per factor.
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import settle  # noqa: E402

FACTOR_KEYS = ["market", "form", "tips", "rpr", "rating", "cd", "draw",
               "speed", "going", "fitness", "weight", "connections"]


def _norm(value):
    text = re.sub(r"\(.*?\)", "", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def decimal_odds(value):
    """A fractional or decimal price as a decimal, or None when unusable."""
    s = str(value or "").strip().lower()
    if not s or s in ("sp", "-", "f", "9999999"):
        return None
    if s in ("evs", "evens"):
        return 2.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        try:
            return 1 + float(m.group(1)) / float(m.group(2))
        except (ValueError, ZeroDivisionError):
            return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if 1.0 < v < 100000 else None


def load_results(date):
    path = os.path.join(ROOT, "data", "results", f"{date}.json")
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(rows, dict):
        rows = rows.get("races", [])
    return rows or []


def position_index(rows, date):
    """(course, time) -> {horse: finishing position}.

    US rows carry their own finish map; the UK/IRE chart in data/<date>.json has
    every runner's position.
    """
    out = {}
    for row in rows:
        key = (_norm(row.get("course")), str(row.get("time") or "")[:5])
        finish = row.get("finish")
        if isinstance(finish, dict):
            out.setdefault(key, {}).update(
                {_norm(h): p for h, p in finish.items() if isinstance(p, int)})
    path = os.path.join(ROOT, "data", f"{date}.json")
    if os.path.exists(path):
        try:
            full = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            full = {}
        for meeting in full.get("meetings") or []:
            course = meeting.get("course", "")
            for race in meeting.get("races") or []:
                key = (_norm(course), str(race.get("time") or "")[:5])
                bucket = out.setdefault(key, {})
                for runner in race.get("runners") or []:
                    m = re.match(r"\s*(\d+)", str(runner.get("pos") or ""))
                    if m:
                        bucket.setdefault(_norm(runner.get("horse")), int(m.group(1)))
    return out


def find_result(rows, card):
    """The settled row for a card: same off time first, then the same winner."""
    for row in rows:
        if _norm(row.get("course")) != _norm(card.get("course")):
            continue
        if str(row.get("time") or "")[:5] == str(card.get("time") or "")[:5]:
            return row
    for row in rows:
        if _norm(row.get("course")) != _norm(card.get("course")):
            continue
        if any(_norm(x.get("horse")) == _norm(row.get("winner"))
               for x in card.get("runners") or []):
            return row
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "training", "runners.csv"))
    ap.add_argument("--dates", nargs="*", help="only these ISO dates")
    args = ap.parse_args(argv)

    wanted = set(args.dates or [])
    rows_out = []
    races = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "cards", "by_date", "*.json"))):
        date = os.path.basename(path)[:-5]
        if wanted and date not in wanted:
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        cards, seen = [], set()
        for region, group in data.items():
            if str(region).upper().startswith("AU") or str(region).upper() == "HK":
                continue
            for card in group or []:
                key = (_norm(card.get("course")), str(card.get("time") or "")[:5])
                if key in seen or not card.get("runners"):
                    continue
                seen.add(key)
                cards.append(card)
        if not cards:
            continue
        scored = settle.score_cards(cards)
        results = load_results(date)
        positions = position_index(results, date)
        for card, result in zip(cards, scored):
            row = find_result(results, card)
            if not row or not row.get("winner"):
                continue
            winner = _norm(row["winner"])
            key = (_norm(card.get("course")), str(card.get("time") or "")[:5])
            posmap = positions.get(key, {})
            field = len(result.get("runners") or [])
            races += 1
            for runner in result.get("runners") or []:
                horse = _norm(runner.get("horse"))
                parts = runner.get("parts") or {}
                odds = decimal_odds(runner.get("odds"))
                rec = {
                    "date": date,
                    "region": "US" if card.get("region") == "US" else "GB/IRE",
                    "course": card.get("course", ""),
                    "time": str(card.get("time") or "")[:5],
                    "race_id": f"{date}|{_norm(card.get('course'))}|{str(card.get('time') or '')[:5]}",
                    "horse": runner.get("horse", ""),
                    "field": field,
                    "pos": posmap.get(horse, ""),
                    "win": 1 if horse == winner else 0,
                    "odds": f"{odds:.2f}" if odds else "",
                    "market_prob": f"{1/odds:.4f}" if odds else "",
                    "rank": runner.get("rank", ""),
                    "total": runner.get("total", ""),
                }
                for k in FACTOR_KEYS:
                    v = parts.get(k)
                    rec[k] = "" if v is None else v
                rows_out.append(rec)

    if not rows_out:
        print("no settled runners to export")
        return 1
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0]))
        writer.writeheader()
        writer.writerows(rows_out)
    priced = sum(1 for r in rows_out if r["market_prob"])
    print(f"{races} settled races, {len(rows_out)} runners ({priced} priced, "
          f"{100*priced/len(rows_out):.0f}%) -> {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
