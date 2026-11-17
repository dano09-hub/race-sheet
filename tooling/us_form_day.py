#!/usr/bin/env python3
"""Snapshot the shadow US pick that has the live form string.

The published US card carries no form, so the model ranks the US on price,
speed and draw. This scores the same cards a second way - with the archived
form_live string in the form slot - and records both picks. It never touches the
page, the weights or the stored pick; tooling/ab_tally.py judges it over time
like the speed-figure A/B.

Only races where every runner has a live form string are compared: a partly
filled race would give some runners a factor the others do not have.

usage: python3 tooling/us_form_day.py 2026-11-17
"""
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import settle  # noqa: E402

OUT_DIR = "picks_ab_form"
SUBJECT = "live form"


def _norm(value):
    text = re.sub(r"\(.*?\)", "", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def complete(card):
    """Every runner needs the signal, or the arm scores a different field."""
    runners = [r for r in card.get("runners") or []
               if str(r.get("horse") or "").strip()]
    return bool(runners) and all(str(r.get("form_live") or "").strip() for r in runners)


def main(date):
    # A snapshot is a pre-race record: once taken it is never rewritten, even if
    # a later refresh runs after the races have settled.
    path = os.path.join(ROOT, "data", OUT_DIR, f"{date}.json")
    if os.path.exists(path):
        print(f"{date}: shadow snapshot already taken, left as-is")
        return 0

    cards = settle._enrich(settle._cards_for(date), date)
    us = [c for c in cards if c.get("region") == "US"]
    compared = [c for c in us if complete(c)]
    if not compared:
        print(f"{date}: no US race carries live form for every runner; shadow skipped")
        return 1

    base = settle.score_cards(json.loads(json.dumps(compared)))
    enriched = []
    for card in compared:
        c2 = json.loads(json.dumps(card))
        for runner in c2.get("runners", []):
            runner["form"] = runner.get("form_live") or ""
        enriched.append(c2)
    shadow = settle.score_cards(enriched)

    rows, changed, seen = [], 0, set()
    for a, b in zip(shadow, base):
        if not a.get("pick") or not b.get("pick"):
            continue
        key = (_norm(a.get("course")), str(a.get("time") or "")[:5])
        if key in seen:
            continue          # one pick per race, however the archive duplicates it
        seen.add(key)
        differs = a["pick"] != b["pick"]
        changed += differs
        rows.append({"course": a["course"], "time": a["time"],
                     "withRating": a["pick"], "withoutRating": b["pick"],
                     "changed": differs})

    out = {
        "date": date,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": ("US shadow: the same cards with the archived live form string "
                 "in the form slot"),
        "metric": "form",
        "label": SUBJECT,
        "cards": len(rows),
        "same": len(rows) - changed,
        "changed": changed,
        "ratedRunners": sum(1 for c in compared for r in c.get("runners", [])
                            if str(r.get("form_live") or "").strip()),
        "runners": sum(len(c.get("runners", [])) for c in compared),
        "rows": rows,
    }
    os.makedirs(os.path.join(ROOT, "data", OUT_DIR), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"{date}: {len(rows)} US races shadowed, {changed} picks differ -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()))
