#!/usr/bin/env python3
"""Append current each-way price snapshots from the sheet's archived cards.

    python3 tooling/collect_eachway_prices.py --date 2026-11-14
    python3 tooling/collect_eachway_prices.py --cards data/cards/2026-11-14.json

The cards carry a current win price, not a separate place market. For a
standard each-way race the place price is derived from that win price and the
race's place terms:

    place decimal = 1 + fraction * (win decimal - 1)

The JSONL records say so explicitly (`place_odds_kind`). If a card later
carries a quoted place price, that value is used and labelled
`bookmaker_quoted`. This collector is append-only and keyed by date, course,
time, horse and price, so running it repeatedly does not duplicate a snapshot.

It is deliberately not wired into the scoring model: it only creates the
forward price history needed to test a place model later.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
import eachway  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "data", "eachway_prices.jsonl")


def parse_decimal(value):
    s = str(value or "").strip().lower()
    if not s:
        return None
    if s in ("evs", "evens"):
        return 2.0
    if "/" in s:
        bits = s.replace("f", "").split("/", 1)
        try:
            dec = 1.0 + float(bits[0]) / float(bits[1])
        except (ValueError, ZeroDivisionError):
            return None
    else:
        try:
            dec = float(s)
        except ValueError:
            return None
    if not 1.0 < dec < 999999:
        return None
    return dec


def is_handicap(card):
    text = " ".join(str(card.get(k) or "") for k in ("race", "label"))
    return bool(re.search(r"handicap|h'cap|hcap", text, re.I))


def load_cards(path, fallback_date):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    if isinstance(raw, dict):
        for region, cards in raw.items():
            if not isinstance(cards, list):
                continue
            for c in cards:
                if isinstance(c, dict):
                    c = dict(c)
                    c.setdefault("region", "GB" if str(region).startswith("GB") else region)
                    c.setdefault("day", fallback_date)
                    out.append(c)
    elif isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict):
                c = dict(c)
                c.setdefault("day", fallback_date)
                out.append(c)
    return out


def collect(cards, date, source):
    records = []
    for card in cards:
        region = str(card.get("region") or "")
        if region and region not in ("GB", "IRE", "GB_tomorrow"):
            continue
        runners = [r for r in card.get("runners") or [] if isinstance(r, dict)]
        field = len(runners)
        if field < 2:
            continue
        handicap = is_handicap(card)
        places, fraction = eachway.place_terms(field, handicap)
        race = card.get("race") or card.get("label") or ""
        for runner in runners:
            win = parse_decimal(runner.get("odds"))
            if win is None:
                continue
            quoted = parse_decimal(runner.get("place_odds") or runner.get("place_decimal"))
            if quoted is not None and quoted > 1.0:
                place = quoted
                kind = "bookmaker_quoted"
            elif places > 0 and fraction > 0:
                place = eachway.place_decimal(win, fraction)
                kind = "derived_standard_terms"
            else:
                place = None
                kind = "win_only"
            rec = {
                "snapshot_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "date": card.get("day") or date,
                "region": card.get("region") or "",
                "course": card.get("course") or "",
                "time": card.get("time") or "",
                "race": race,
                "horse": runner.get("horse") or "",
                "field_size": field,
                "handicap": handicap,
                "win_odds": round(win, 6),
                "places": places,
                "place_fraction": fraction,
                "place_odds": round(place, 6) if place is not None else None,
                "place_odds_kind": kind,
                "source": source,
            }
            key = "|".join(str(rec[k]) for k in
                           ("date", "course", "time", "horse", "win_odds"))
            rec["snapshot_id"] = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
            records.append(rec)
    return records


def existing_ids(path):
    ids = set()
    if not os.path.exists(path):
        return ids
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                ids.add(json.loads(line).get("snapshot_id"))
            except (ValueError, AttributeError):
                continue
    return ids


def append_records(records, out=DEFAULT_OUT):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    seen = existing_ids(out)
    new = [r for r in records if r.get("snapshot_id") not in seen]
    if new:
        with open(out, "a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return len(new)


def collect_file(path, date, out=DEFAULT_OUT):
    cards = load_cards(path, date)
    return append_records(collect(cards, date, os.path.basename(path)), out)


def main():
    ap = argparse.ArgumentParser(description="Append each-way price snapshots from archived cards")
    ap.add_argument("--cards", help="card JSON; defaults to data/cards/<date>.json")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = args.cards or os.path.join(ROOT, "data", "cards", args.date + ".json")
    if not os.path.exists(path):
        raise SystemExit("card file not found: " + path)
    cards = load_cards(path, args.date)
    records = collect(cards, args.date, os.path.basename(path))
    if args.dry_run:
        print("%d records from %d cards; no write" % (len(records), len(cards)))
        print(json.dumps(records[:2], indent=2, ensure_ascii=False))
        return 0
    n = append_records(records, args.out)
    print("%d cards, %d runner prices, %d new snapshots -> %s" % (
        len(cards), len(records), n, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
