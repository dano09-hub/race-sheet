#!/usr/bin/env python3
"""File Hong Kong win odds before each race, never after it.

The morning card is scraped before betting opens, so the page's Market factor
(weight 18, the largest single input) is blank for almost every Hong Kong race
and the pick is decided by form, official rating, draw and weight alone. This
tool runs through the meeting and files one snapshot per race inside a fixed
window before its post time, so a future Hong Kong model can use the crowd's
price without ever reading a figure that only existed after the race.

    python3 tooling/snapshot_hk.py             # file whatever is due now
    python3 tooling/snapshot_hk.py --print     # show what would be filed
    python3 tooling/snapshot_hk.py --lead 15   # a different lead time

Three rules keep the archive honest:

* a filed snapshot is never overwritten, so the first pre-off price stands;
* a race whose post time has passed is never captured, whatever the feed says;
* a race whose runners do not match the archived card is skipped rather than
  stored against the wrong field.

Nothing scores from these files yet. They exist so a Hong Kong factor can be
measured against a price that was actually available before the off.
"""
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import scrape_hk                                          # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "hk_preoff")
LEAD = 20 * 60        # earliest capture: twenty minutes before the post time
MIN_LEFT = 3 * 60     # latest capture: three minutes before it


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _parse(iso):
    """The card's official post time, as an aware UTC datetime."""
    try:
        return dt.datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _key(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _race_no(card):
    m = re.search(r"-r(\d+)$", str(card.get("id") or ""))
    return int(m.group(1)) if m else 0


def _cards(date_iso):
    """The Hong Kong card filed for the day, dated snapshot first."""
    for path in (os.path.join(ROOT, "data", "cards", f"{date_iso}.json"),
                 os.path.join(ROOT, "data", "cards", "by_date", f"{date_iso}.json")):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        cards = data.get("HK") if isinstance(data, dict) else None
        if isinstance(cards, list) and cards:
            return cards
    return []


def due(off, now, lead=LEAD, left=MIN_LEFT):
    """True when the race is inside its capture window and still to come."""
    if off is None:
        return False
    remaining = (off - now).total_seconds()
    return left <= remaining <= lead


def odds_rows(race, card):
    """The field's win odds now, or None when this is not the card's field."""
    got, declared = [], 0
    for runner in race.get("runners") or []:
        if str(runner.get("standbyNo") or "").strip():
            continue
        if str(runner.get("status") or "").strip().lower() in scrape_hk.NON_RUNNERS:
            continue
        declared += 1
        name = str(runner.get("name_en") or "").strip()
        if not name:
            return None
        got.append({
            "horse": name,
            "odds": str(runner.get("winOdds") or "").strip(),
            "status": str(runner.get("status") or "").strip(),
        })
    if not got or declared != len(got):
        return None
    # The snapshot is only worth keeping if it is the field the card declared.
    want = {_key(r.get("horse")) for r in card.get("runners") or []}
    if want != {_key(g["horse"]) for g in got}:
        return None
    # No price is published yet: nothing to archive.
    if not any(g["odds"] for g in got):
        return None
    return got


def collect(cards, payload, now, lead=LEAD, left=MIN_LEFT):
    """One snapshot per race that is due, never for a race already off."""
    by_no = {_race_no(c): c for c in cards if _race_no(c)}
    meets = (payload.get("data") or {}).get("raceMeetings") or []
    if not meets:
        return []
    out = []
    for race in meets[0].get("races") or []:
        no = int(race.get("no") or 0)
        card = by_no.get(no)
        if not no or not card:
            continue
        if str(race.get("status") or "").strip().upper() == "RESULT":
            continue
        off = _parse(card.get("iso"))
        if not due(off, now, lead, left):
            continue
        runners = odds_rows(race, card)
        if not runners:
            continue
        out.append({
            "date": str(card.get("day") or off.date().isoformat()),
            "race_no": no,
            "course": card.get("course", ""),
            "race": card.get("race", ""),
            "off": card.get("iso"),
            "captured": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lead_seconds": int((off - now).total_seconds()),
            "source": "HKJC win odds (official GraphQL meeting)",
            "runners": runners,
        })
    return out


def save(date_iso, snapshot):
    """Write a race's snapshot once; a filed snapshot is history."""
    path = os.path.join(OUT_DIR, date_iso, "r%d.json" % int(snapshot["race_no"]))
    if os.path.exists(path):
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
    return path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    preview = "--print" in argv
    lead = LEAD
    if "--lead" in argv:
        i = argv.index("--lead")
        try:
            lead = max(1, int(argv[i + 1])) * 60
        except (IndexError, ValueError):
            print(f"  HK odds:     --lead needs a number of minutes", file=sys.stderr)
            return 2
    date_iso = _now().date().isoformat()
    cards = _cards(date_iso)
    if not cards:
        print(f"  HK odds:     no Hong Kong card filed for {date_iso}")
        return 0
    try:
        payload = scrape_hk.fetch(date_iso.replace("-", "/"))
    except Exception as e:
        print(f"  HK odds:     feed unavailable ({e}); nothing filed")
        return 0
    now = _now()
    found = collect(cards, payload, now, lead)
    if not found:
        print(f"  HK odds:     nothing due for {date_iso}")
        return 0
    if preview:
        print(json.dumps(found, ensure_ascii=False, indent=1))
        return 0
    filed = 0
    for snapshot in found:
        if save(date_iso, snapshot):
            filed += 1
            print(f"  HK odds:     R{snapshot['race_no']} filed "
                  f"{snapshot['lead_seconds']}s before the off")
    print(f"  HK odds:     {filed} new, {len(found) - filed} already on file "
          f"for {date_iso}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
