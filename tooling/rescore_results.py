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

A price the archived card lost is restored from a stored copy of the same day --
the append-only each-way snapshots, which record when they were taken, or the
day's live card pulls -- but only where that copy was taken before the off on
the course's own clock, and never over a price the card already carries. The source of every restored price
is written beside the race as `price_source`, and a race with no stored price
for even one runner stays not rated.

usage: python3 tooling/rescore_results.py [YYYY-MM-DD ...]
"""
import datetime as dt
import glob
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNNER = os.path.join(HERE, "score_rated.js")
OUT_DIR = os.path.join(ROOT, "data", "results_rated")
EACHWAY = os.path.join(ROOT, "data", "eachway_prices.jsonl")
CARDS = os.path.join(ROOT, "data", "cards")

REGIONS = ("GB", "IRE", "US", "AU", "HK", "FR", "CL", "AE")
POS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th",
       7: "7th", 8: "8th", 9: "9th", 10: "10th"}


def norm(value):
    """A name reduced for matching, with HTML entities decoded first.

    Result rows are scraped from HTML, so a winner can arrive as
    "Who&#x27;s Going" while the card carries "Who's Going". Decoding keeps
    the two spellings equal, whether or not the row has a time to match on.
    """
    s = html.unescape(str(value or "")).lower()
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


def price_text(value):
    """A stored price in the form the page's own decimalOdds reads, or None.

    The rules are the page's: evens, a decimal above 1 below 100000, or a
    fractional price. Anything else is no price rather than a guess.
    """
    s = str(value if value is not None else "").strip().lower().replace(" ", "")
    if not s:
        return None
    if s in ("evs", "evens"):
        return "evs"
    if re.match(r"^\d+(\.\d+)?$", s):
        n = float(s)
        return f"{n:g}" if 1.0 < n < 100000 else None
    if re.match(r"^\d+[/-]\d+$", s):
        return s
    return None


def off_time(date, time_text):
    """The scheduled off as a naive datetime, or None when no time is on file."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})", str(time_text or ""))
    if not m:
        return None
    try:
        day = dt.datetime.fromisoformat(date)
    except ValueError:
        return None
    return day.replace(hour=int(m.group(1)), minute=int(m.group(2)))


def snapshot_time(text):
    """A stored snapshot as naive UTC, or None when it carries no time."""
    try:
        got = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if got.tzinfo is not None:
        got = got.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return got


# A card's off is local track time and a snapshot is UTC. A country that keeps
# one clock can be placed exactly, so a British summer card no longer accepts
# the hour of prices filed after its off. Countries with more than one clock
# (the US, Australia) are left out rather than guessed: the off cannot be
# proven from the region alone, so only an earlier day's snapshot is taken as
# pre-race for them.
ZONES = {
    "GB": "Europe/London",
    "IRE": "Europe/Dublin",
    "FR": "Europe/Paris",
    "HK": "Asia/Hong_Kong",
    "CL": "America/Santiago",
}


def off_utc(date, time_text, region):
    """The scheduled off in UTC, or None when the venue's clock is unknown."""
    off = off_time(date, time_text)
    zone = ZONES.get(str(region or "").strip().upper())
    if off is None or zone is None:
        return None
    try:
        local = off.replace(tzinfo=ZoneInfo(zone))
    except KeyError:              # no time-zone database on this host
        return None
    return local.astimezone(dt.timezone.utc).replace(tzinfo=None)


def card_list(path):
    """Every card-like record in a stored card file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        cards = data
    else:
        cards = [c for v in data.values() if isinstance(v, list) for c in v]
    return [c for c in cards if isinstance(c, dict) and "runners" in c]


def price_index(date):
    """Prices stored for the day, keyed by course and horse, with provenance.

    The archive can lose the price column while another stored copy of the same
    day keeps it. The append-only each-way snapshots say when they were taken,
    so a snapshot at or after the off is refused: a post-race price cannot
    stand in for the one the card would have shown. The snapshot is UTC and the
    card's off is local track time, so the two are compared in the course's
    zone; during British Summer Time a straight comparison would pass prices
    filed an hour after the off. Where several pre-off snapshots exist the
    latest wins. The day's own card pulls carry no time of
    their own and are used only to fill horses the snapshots do not cover.
    """
    stamped = {}
    if os.path.exists(EACHWAY):
        with open(EACHWAY, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("date") != date:
                    continue
                price = price_text(rec.get("win_odds"))
                snap = snapshot_time(rec.get("snapshot_utc"))
                if price is None or snap is None:
                    continue
                off = off_utc(date, rec.get("time"), rec.get("region"))
                if off is not None:
                    if snap >= off:
                        continue
                elif snap.date() >= dt.date.fromisoformat(date):
                    continue          # cannot show this price was pre-race
                key = (norm(rec.get("course")), norm(rec.get("horse")))
                if key not in stamped or snap > stamped[key][0]:
                    stamped[key] = (snap, price, "eachway_prices.jsonl")
    out = {}
    for key, (snap, price, source) in stamped.items():
        out[key] = (price, f"{source} {snap.isoformat()}")
    for path in sorted(glob.glob(os.path.join(CARDS, date + "*.json"))):
        for card in card_list(path):
            for r in card.get("runners") or []:
                price = price_text(r.get("odds"))
                if price is None:
                    continue
                key = (norm(card.get("course")), norm(r.get("horse")))
                if key not in out:
                    out[key] = (price, os.path.basename(path))
    return out


def priced(card, prices):
    """A copy of the card with empty prices filled from stored ones.

    Only an empty slot is filled and a scratched runner is left alone, so a
    price the card already carries can never be replaced. A runner with no
    stored price keeps its empty field, and the card stays not rated.
    """
    out = dict(card)
    used = set()
    runners = []
    for r in card.get("runners") or []:
        r = dict(r)
        if not r.get("scratched") and price_text(r.get("odds")) is None:
            hit = prices.get((norm(card.get("course")), norm(r.get("horse"))))
            if hit:
                r["odds"] = hit[0]
                used.add(hit[1])
        runners.append(r)
    out["runners"] = runners
    return out, used


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
    """The card a settled row belongs to: both the off and the winner first,
    then the card holding the winner, then the off alone. A scraped time that
    hits another race must not beat the card with the winner; the off alone
    keeps a row scoreable when the card spells the name differently.
    """
    course, time, winner = norm(row.get("course")), str(row.get("time") or "")[:5], norm(row.get("winner"))
    pool = [c for c in cards if norm(c.get("course")) == course]

    def at_off(card):
        return str(card.get("time") or "")[:5] == time

    def has_winner(card):
        return any(norm(r.get("horse")) == winner for r in card.get("runners", []))

    for card in pool:
        if at_off(card) and has_winner(card):
            return card
    for card in pool:
        if has_winner(card):
            return card
    for card in pool:
        if at_off(card):
            return card
    return None


def rate_day(date):
    """Every archived card for the day, scored exactly as the re-score does.

    Returns (cards, scored, restored): cards in the order the day stores them,
    scored keyed by id(card), and the stored copies used to restore a price.
    A card whose only gap is a price the archive still holds is scored again
    with that price filled in. The race-by-race audit reads this same result, so
    the rank it prints comes from the very card that produced the pick.
    """
    cards = day_cards(date)
    scored = {id(c): s for c, s in zip(cards, score(cards))}
    # Only the races price alone holds out are candidates: restoring the price
    # for a race still missing form or a rating would change no pick, and the
    # gap report should keep naming the field that actually blocks it.
    prices = price_index(date)
    refill, restored = [], {}
    for card in cards:
        got = scored.get(id(card)) or {}
        gaps = [g.get("what") for g in got.get("missing") or []]
        if gaps != ["price"]:
            continue
        copy, used = priced(card, prices)
        if used:
            refill.append((card, copy, sorted(used)))
    if refill:
        rescored = score([copy for _, copy, _ in refill])
        for (card, _, used), got in zip(refill, rescored):
            scored[id(card)] = got
            restored[id(card)] = used
    return cards, scored, restored


def race_entry(cards, scored, restored, region, row, card=None):
    """The re-score's row for one settled race, from the card it matched."""
    if card is None:
        card = pick_card(cards, row)
    got = scored.get(id(card)) if card else None
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
    if card and restored.get(id(card)):
        entry["price_source"] = restored[id(card)]
    entry["blocker"], entry["blocker_only"] = blocker_of(entry)
    return entry


def day(date, rated=None):
    """The day's re-score, reusing a rate_day() result when one is passed in."""
    cards, scored, restored = rated if rated is not None else rate_day(date)
    return {
        "date": date,
        "model": "six-input",
        "races": [race_entry(cards, scored, restored, region, row)
                  for region, row in result_rows(date)],
    }


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
    recovered = recovered_rated = 0
    tally, no_card = {}, {"races": 0, "regions": set()}
    for date in sorted(dates):
        got = day(date)
        total += len(got["races"])
        rated += sum(1 for r in got["races"] if r["state"] == "rated")
        recovered += sum(1 for r in got["races"] if r.get("price_source"))
        recovered_rated += sum(1 for r in got["races"]
                               if r.get("price_source") and r["state"] == "rated")
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
    if recovered:
        print(f"prices: {recovered} races had a stored pre-off price for every "
              f"runner, {recovered_rated} of them now rated")
    print_breakdown(tally, no_card, total - rated)


if __name__ == "__main__":
    main(sys.argv[1:])
