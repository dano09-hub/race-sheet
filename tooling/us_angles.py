#!/usr/bin/env python3
"""Read-only US Angles: every runner, every available flag.

The daily source is The Overlay's public US angle feed, which carries the
runner's flags and the reasons behind them.  Races missing from that feed are
scanned from the archived card and only the flags the card supports are
derived.  Fair chance is a power-method de-vig of the card prices.

Nothing here feeds the model or changes a pick.

usage: python3 tooling/us_angles.py 2026-11-16
"""
import collections
import datetime as dt
import glob
import html
import json
import math
import os
import re
import sys

import fetch_overlay_us
import us_flags

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ANGLE_DIR = os.path.join(ROOT, "data", "us_angles")

# The exact flag names and categories used by the Overlay angle builder.
FLAG_DEFS = [
    ("fav", "Favourite", "race", "Shortest price in the race"),
    ("topor", "Top rated", "race", "Highest official rating in the race"),
    ("inside", "Inside post", "race", "Drawn 1-3"),
    ("wide", "Wide post", "race", "One of the two widest stalls"),
    ("c", "Track winner", "proven", "Has won at this track"),
    ("wonlto", "Won last time", "proven", "Won on the latest start"),
    ("plclto", "Placed last time", "proven", "Second or third on the latest start"),
    ("surface", "Switching surface", "changed", "Dirt/turf switch from the latest start"),
    ("quick", "Quick return", "changed", "Ran within the last 14 days"),
    ("absence", "Off 60+ days", "changed", "No run for 60 days or more"),
    ("blink1", "Blinkers first time", "changed", "First run in blinkers"),
    ("tin", "Trainer in form", "people", "20%+ win strike rate in 30 days"),
    ("jin", "Jockey in form", "people", "20%+ win strike rate in 30 days"),
]
FLAG_BY_KEY = {x[0]: x for x in FLAG_DEFS}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _norm(value):
    text = html.unescape(str(value or "")).lower()
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def key(course, time):
    return _norm(course) + "|" + str(time or "")[:5]


# Standard fractional ladder, matched to the one used by the site.
_LADDER = [
    (1, 2), (4, 7), (8, 13), (4, 6), (8, 11), (4, 5), (10, 11), (1, 1),
    (11, 10), (6, 5), (5, 4), (11, 8), (6, 4), (13, 8), (7, 4), (15, 8),
    (2, 1), (9, 4), (5, 2), (11, 4), (3, 1), (10, 3), (7, 2), (4, 1),
    (9, 2), (5, 1), (11, 2), (6, 1), (13, 2), (7, 1), (15, 2), (8, 1),
    (9, 1), (10, 1), (11, 1), (12, 1), (14, 1), (16, 1), (20, 1), (25, 1),
    (33, 1), (40, 1), (50, 1), (66, 1), (100, 1),
]


def fair_fraction(decimal):
    """Turn a fair decimal price into the nearest standard fraction."""
    try:
        decimal = float(decimal)
    except (TypeError, ValueError):
        return ""
    if decimal <= 1:
        return ""
    best = min(_LADDER, key=lambda f: abs(math.log(1 + f[0] / f[1]) - math.log(decimal)))
    if decimal > 101 * 1.15:
        return "100/1+"
    return "Evs" if best == (1, 1) else f"{best[0]}/{best[1]}"


def _fair_map(runners):
    """Fair chance for every priced runner, keyed by normalised horse name."""
    pairs = []
    for runner in runners:
        decimal = fetch_overlay_us.decimal_odds(runner.get("odds"))
        if decimal:
            pairs.append((runner, decimal))
    if len(pairs) < 2:
        return {}
    probs, _book, _k, _method = fetch_overlay_us.devig_power([d for _, d in pairs])
    out = {}
    for (runner, _), prob in zip(pairs, probs):
        out[_norm(runner.get("horse") or runner.get("h"))] = {
            "pct": round(100 * prob, 1),
            "fraction": fair_fraction(1 / prob),
        }
    return out


def _clock(card_runner):
    try:
        ts = int(float(str((card_runner or {}).get("ts") or "").strip()))
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def _card_live_runners(card):
    return [r for r in card.get("runners") or [] if not r.get("scratched")]


def _angle_runner(angle_race, horse):
    if not angle_race:
        return None
    wanted = _norm(horse)
    for runner in (angle_race.get("runners") or {}).values():
        if _norm(runner.get("h") or runner.get("horse")) == wanted:
            return runner
    return None


def _flags_from_feed(runner):
    reason = {x[0]: x[1] for x in (runner.get("c") or []) if isinstance(x, (list, tuple)) and x}
    flags = []
    for flag_key in runner.get("f") or []:
        definition = FLAG_BY_KEY.get(flag_key)
        if not definition:
            continue
        flags.append({"key": flag_key, "label": definition[1],
                      "category": definition[2], "description": definition[3],
                      "reason": reason.get(flag_key, "")})
    return flags


def _derive_flags(card, runner, inform, field_size):
    """Fallback flags for a card-only race; never guesses unavailable history."""
    flags = []

    def add(flag_key, reason=""):
        definition = FLAG_BY_KEY.get(flag_key)
        if definition and not any(x["key"] == flag_key for x in flags):
            flags.append({"key": flag_key, "label": definition[1],
                          "category": definition[2], "description": definition[3],
                          "reason": reason})

    live = _card_live_runners(card)
    prices = []
    for other in live:
        try:
            prices.append(fetch_overlay_us.decimal_odds(other.get("odds")))
        except Exception:
            prices.append(None)
    own = fetch_overlay_us.decimal_odds(runner.get("odds"))
    if own and prices and own == min(x for x in prices if x):
        add("fav", "Shortest price on the card")

    ratings = []
    for other in live:
        try:
            rating = int(float(other.get("or") or 0))
        except (TypeError, ValueError):
            rating = 0
        if rating:
            ratings.append(rating)
    try:
        own_rating = int(float(runner.get("or") or 0))
    except (TypeError, ValueError):
        own_rating = 0
    if own_rating and ratings and own_rating == max(ratings):
        add("topor", "Top official rating")

    try:
        draw = int(float(runner.get("draw") or 0))
    except (TypeError, ValueError):
        draw = 0
    if draw:
        if draw <= 3:
            add("inside", f"Drawn {draw} of {field_size}")
        if draw >= field_size - 1:
            add("wide", f"Drawn {draw} of {field_size}")

    form = str(runner.get("form") or "").strip()
    if form[:1] == "1":
        add("wonlto", "Won on the latest start")
    elif form[:1] in ("2", "3"):
        add("plclto", "Placed on the latest start")

    if str(runner.get("course") or "").upper() == "Y":
        add("c", "Won at this track")

    try:
        days = int(float(str(runner.get("days") or "").strip()))
    except (TypeError, ValueError):
        days = None
    if days is not None:
        if days <= 14:
            add("quick", f"Ran {days} days ago")
        elif days >= 60:
            add("absence", f"Off {days} days")

    _tn, tstats = us_flags.match_person(inform.get("trainers") or {},
                                        runner.get("trainer") or runner.get("tp") or "")
    _jn, jstats = us_flags.match_person(inform.get("jockeys") or {},
                                        runner.get("jockey") or runner.get("jp") or "")
    if tstats and tstats.get("r30") and tstats.get("sr30") is not None and tstats["sr30"] >= 20:
        add("tin", f"Trainer {tstats['sr30']:g}% in 30 days")
    if jstats and jstats.get("r30") and jstats.get("sr30") is not None and jstats["sr30"] >= 20:
        add("jin", f"Jockey {jstats['sr30']:g}% in 30 days")
    return flags


def _date_files(folder):
    out = []
    for path in glob.glob(os.path.join(ROOT, "data", folder, "*.json")):
        name = os.path.basename(path)[:-5]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            out.append(name)
    return sorted(out)


def health(date):
    """A small, honest freshness/coverage check for the generated page."""
    cards = _date_files("cards/by_date")
    results = _date_files("results")
    picks = _date_files("picks_ab_us")
    angles = _date_files("us_angles")
    through = [d for d in cards if d <= date]
    window = [d for d in through if d >= (dt.date.fromisoformat(date) - dt.timedelta(days=6)).isoformat()]
    return {
        "cards_days": cards,
        "results_days": results,
        "picks_days": picks,
        "angle_days": angles,
        "latest_cards": through[-1] if through else "",
        "latest_results": results[-1] if results else "",
        "latest_picks": picks[-1] if picks else "",
        "latest_angles": angles[-1] if angles else "",
        "missing_results": [d for d in window if d not in results],
        # Only flag A/B gaps after the first US snapshot exists; the US tab
        # began on 16 Nov and older days are not expected to have one.
        "missing_picks": ([d for d in window if d >= picks[0] and d not in picks]
                          if picks else []),
        "inform_updated": _load(os.path.join(ROOT, "data", "us_inform.json"), {}).get("updated", ""),
    }


def build(date, cards_path=None, angles_path=None, picks_path=None, inform_path=None):
    cards_path = cards_path or os.path.join(ROOT, "data", "cards", "by_date", f"{date}.json")
    angles_path = angles_path or os.path.join(ANGLE_DIR, f"{date}.json")
    picks_path = picks_path or os.path.join(ROOT, "data", "picks_ab_us", f"{date}.json")
    inform_path = inform_path or os.path.join(ROOT, "data", "us_inform.json")

    card_data = _load(cards_path, {})
    all_card_entries = ([c for group in card_data.values() for c in group]
                        if isinstance(card_data, dict) else (card_data or []))
    cards = card_data.get("US") if isinstance(card_data, dict) else card_data
    cards = cards or []
    raw_card_entries = len(cards)
    # The by-date archive merges runs and can carry two copies of a race.
    # Keep one copy so a race is never counted twice in the scan.
    deduped, seen_cards = [], set()
    for card in cards:
        race_key = key(card.get("course"), card.get("time"))
        if race_key in seen_cards:
            continue
        seen_cards.add(race_key)
        deduped.append(card)
    cards = deduped
    angle_data = _load(angles_path, {})
    angle_races = angle_data.get("races") or {}
    picks_data = _load(picks_path, {})
    picks = picks_data.get("rows") if isinstance(picks_data, dict) else picks_data
    pick_by_key = {}
    for row in picks or []:
        if row.get("withRating"):
            pick_by_key[key(row.get("course"), row.get("time"))] = row["withRating"]
    inform = _load(inform_path, {})

    angle_index = {}
    for race in angle_races.values() if isinstance(angle_races, dict) else angle_races:
        angle_index[key(race.get("course"), race.get("off") or race.get("time"))] = race

    races = []
    counts = collections.Counter()
    runner_total = 0
    live_runners = 0
    live_races = 0
    card_only_races = 0

    for card in cards:
        race_key = key(card.get("course"), card.get("time"))
        angle_race = angle_index.get(race_key)
        card_runners = _card_live_runners(card)
        field_size = len(card_runners)
        fair = _fair_map([
            {"horse": r.get("horse"), "odds": (_angle_runner(angle_race, r.get("horse")) or {}).get("odds")
             or r.get("odds")}
            for r in card_runners
        ])
        runners = []
        for card_runner in card_runners:
            horse = card_runner.get("horse") or ""
            angle_runner = _angle_runner(angle_race, horse)
            flags = _flags_from_feed(angle_runner) if angle_runner else []
            if not flags:
                flags = _derive_flags(card, card_runner, inform, field_size)
            clock = _clock(card_runner)
            runner = {
                "horse": horse,
                "number": card_runner.get("number") or card_runner.get("no") or card_runner.get("draw") or "",
                "draw": card_runner.get("draw") or (angle_runner or {}).get("draw") or "",
                "odds": (angle_runner or {}).get("odds") or card_runner.get("odds") or "",
                "trainer": card_runner.get("trainer") or card_runner.get("tp") or (angle_runner or {}).get("t") or "",
                "jockey": card_runner.get("jockey") or card_runner.get("jp") or (angle_runner or {}).get("j") or "",
                "flags": flags,
                "clock": clock,
                "fair": fair.get(_norm(horse)) or None,
                "live": bool(angle_runner),
                "pick": _norm(horse) == _norm(pick_by_key.get(race_key, "")),
            }
            for flag in flags:
                counts[flag["key"]] += 1
            runner_total += 1
            if angle_runner:
                live_runners += 1
            runners.append(runner)

        if angle_race:
            live_races += 1
        else:
            card_only_races += 1
        races.append({
            "course": card.get("course") or "",
            "time": card.get("time") or "",
            "name": card.get("race") or card.get("label") or "",
            "surface": (angle_race or {}).get("surf") or "",
            "field": field_size,
            "live": bool(angle_race),
            "runners": runners,
        })

    # Cross-check the settled result file against the same cards: every
    # winner must be findable by course and horse before we call the day clean.
    result_path = os.path.join(ROOT, "data", "results", f"{date}.json")
    result_rows = _load(result_path, [])
    result_rows = result_rows.get("races") if isinstance(result_rows, dict) else result_rows
    result_rows = result_rows or []
    result_matched = 0
    if result_rows:
        by_course = collections.defaultdict(set)
        for card in all_card_entries:
            course = _norm(card.get("course"))
            for runner in card.get("runners") or []:
                by_course[course].add(_norm(runner.get("horse")))
        for row in result_rows:
            if _norm(row.get("winner")) in by_course.get(_norm(row.get("course")), set()):
                result_matched += 1

    races.sort(key=lambda r: (str(r["time"]), _norm(r["course"])))
    return {
        "date": date,
        "audit": {
            "card_entries": raw_card_entries,
            "unique_cards": len(cards),
            "duplicate_card_entries": raw_card_entries - len(cards),
            "result_rows": len(result_rows),
            "result_with_picks": sum(1 for r in result_rows if r.get("pick")),
            "result_matched": result_matched,
        },
        "races": races,
        "total_races": len(races),
        "live_races": live_races,
        "card_only_races": card_only_races,
        "runners": runner_total,
        "live_runners": live_runners,
        "flags": [{"key": k, "label": label, "category": category,
                   "description": description, "count": counts.get(k, 0)}
                  for k, label, category, description in FLAG_DEFS],
        "source_generated": angle_data.get("generated") or "",
        "health": health(date),
    }


def block_html(data):
    esc = lambda s: html.escape(str(s if s is not None else ""))
    if not data or not data.get("races"):
        return '<p class="k pending">No US cards available for an angle scan yet.</p>'

    catalog = "".join(
        f'<button type="button" class="angchip" data-angle-filter="{esc(f["key"])}" '
        f'title="{esc(f["description"])}">{esc(f["label"])} '
        f'<span class="angcount">{f["count"]}</span></button>'
        for f in data["flags"])
    checks = data.get("health") or {}
    generated = data.get("source_generated") or ""
    try:
        generated = dt.datetime.fromisoformat(generated.replace("Z", "+00:00")).strftime("%-d %B %Y %H:%M")
    except (TypeError, ValueError):
        pass
    source_note = (f"Flags come from The Overlay's public US angle feed"
                   + (f" (built {esc(generated)})" if generated else "")
                   + ". Card-only races derive only the facts the card carries. "
                   "Fair chance is a power-method de-vig of the card prices; it is not a tip.")

    race_parts = []
    for race in data["races"]:
        tag = "live angles" if race["live"] else "card only"
        runner_parts = []
        for runner in race["runners"]:
            badges = "".join(
                f'<span class="angbadge angbadge-{esc(f["category"])}" '
                f'title="{esc(f["reason"] or f["description"])}">'
                f'{esc(f["label"])}</span>'
                for f in runner["flags"])
            if runner.get("clock"):
                badges += f'<span class="angbadge angbadge-clock">Clock {runner["clock"]}</span>'
            if not badges:
                badges = '<span class="angempty">No qualifying flag</span>'
            fair = runner.get("fair") or {}
            fair_text = (f'{fair.get("pct", "—")}% · {fair.get("fraction", "—")}'
                         if fair else "—")
            pick_class = " angpick" if runner.get("pick") else ""
            runner_parts.append(
                f'<div class="angrunner{pick_class}" '
                f'data-flags="{esc(" ".join(f["key"] for f in runner["flags"]))}" '
                f'data-pick="{1 if runner.get("pick") else 0}">'
                f'<div class="angno">{esc(runner.get("number") or "—")}'
                f'<small>({esc(runner.get("draw") or "—")})</small></div>'
                f'<div class="angmain"><b>{esc(runner["horse"])}</b>'
                f'<div class="angbadges">{badges}</div>'
                f'<div class="angmeta">{esc(runner.get("trainer") or "—")}'
                f' &middot; {esc(runner.get("jockey") or "—")}</div></div>'
                f'<div class="angprice"><b>{esc(runner.get("odds") or "—")}</b>'
                f'<span class="angfair">fair {esc(fair_text)}</span></div>'
                '</div>')
        open_attr = " open" if any(r.get("pick") for r in race["runners"]) else ""
        race_parts.append(
            f'<details class="angrace"{open_attr}>'
            f'<summary><span><b>{esc(race["time"])} {esc(race["course"])}</b>'
            f'<span class="angracename">{esc(race["name"])}</span></span>'
            f'<span class="angtag">{esc(tag)} &middot; {race["field"]} runners</span></summary>'
            f'<div class="angrunners">{"".join(runner_parts)}</div></details>')

    gaps = []
    if checks.get("missing_results"):
        gaps.append("results " + ", ".join(checks["missing_results"]))
    if checks.get("missing_picks"):
        gaps.append("US A/B picks " + ", ".join(checks["missing_picks"]))
    latest = (f'Data check: cards {esc(checks.get("latest_cards") or "—")} &middot; '
              f'results {esc(checks.get("latest_results") or "—")} &middot; '
              f'US picks {esc(checks.get("latest_picks") or "—")} &middot; '
              f'inform {esc((checks.get("inform_updated") or "—")[:10])}'
              + (f' &middot; <span class="angwarn">gaps: {esc("; ".join(gaps))}</span>' if gaps else ""))
    audit = data.get("audit") or {}
    if audit:
        latest += (f'<br><b>Card / result cross-check:</b> {audit.get("unique_cards", 0)} unique US races '
                   f'from {audit.get("card_entries", 0)} archive entries'
                   + (f' ({audit.get("duplicate_card_entries")} duplicates collapsed)' if audit.get("duplicate_card_entries") else "")
                   + f'; results {audit.get("result_rows", 0)} rows, '
                   f'{audit.get("result_with_picks", 0)} with picks, '
                   f'{audit.get("result_matched", 0)} winners found in the cards.')
    return (
        '<div class="angbox">'
        '<h3>US Angles — every runner, every available flag</h3>'
        f'<p class="k">{source_note}</p>'
        f'<p class="k">Scanned <b>{data["total_races"]}</b> US cards, '
        f'<b>{data["runners"]}</b> runners: <b>{data["live_races"]}</b> with live angles '
        f'and <b>{data["card_only_races"]}</b> card-only. '
        f'<b>{len([f for f in data["flags"] if f["count"]])}</b> flag types found today.</p>'
        f'<div class="angcatalog"><span class="angcataloglabel">Filter flags:</span>{catalog}'
        '<button type="button" class="angchip angclear" id="angClear">Clear</button></div>'
        f'<p class="k angsmall">{latest}</p>'
        f'<div class="anglist">{"".join(race_parts)}</div>'
        '<p class="k angsmall">Flags are context only: no score, pick or result is changed by this tab. '
        'Click a flag to filter the scan; click it again to remove it.</p>'
        '</div>'
    )


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    data = build(date)
    print(json.dumps({k: data[k] for k in
                      ("date", "total_races", "live_races", "card_only_races",
                       "runners", "live_runners")}, indent=1))
    print(json.dumps(data["flags"], indent=1))
