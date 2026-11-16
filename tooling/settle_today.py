#!/usr/bin/env python3
"""Settle today's results after the last UK race.

The morning refresh deliberately writes yesterday's summary. This pass runs
late, builds today's summary from the completed Sporting Life results, settles
the picks from today's archived cards, and rebuilds the Results tab. It never
changes a pick that is already stored.

    python3 tooling/settle_today.py
    python3 tooling/settle_today.py --progressive  # during racing
    python3 tooling/settle_today.py 2026-11-16   # force a date
"""
import datetime as dt
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import eachway                                           # noqa: E402
import refresh                                           # noqa: E402
import fetch_results_sl as sl                            # noqa: E402
import fetch_results_us                                # noqa: E402


def _ordinal(pos):
    if 10 <= pos % 100 <= 20:
        return f"{pos}th"
    return f"{pos}{dict({1: 'st', 2: 'nd', 3: 'rd'}).get(pos % 10, 'th')}"


def _positions(iso):
    """Full finishing order for each completed GB/IRE race on a date."""
    index = sl.get(f"{sl.BASE}/racing/results/{iso}")
    links = sorted(set(re.findall(
        rf'href="(/racing/results/{iso}/[^"]+)"', index)))
    out = {}
    for href in links:
        parts = href.strip("/").split("/")
        course = parts[3].replace("-", " ").title() if len(parts) > 3 else ""
        page = sl.get(sl.BASE + href)
        if not page:
            continue
        cc = sl.country_of(page)
        if cc and cc not in sl.GB_IRE:
            continue
        name, runners = sl.parse_race(page)
        if not runners:
            continue
        key = (refresh._result_name(course),
               refresh._result_name(runners[0][1]))
        out[key] = (course, name, runners)
    return out


def _cards(iso):
    path = os.path.join(ROOT, "data", "cards", "by_date", f"{iso}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [c for cs in data.values() for c in cs]


def _card_for(cards, row):
    course = refresh._result_name(row.get("course"))
    winner = refresh._result_name(row.get("winner"))
    for card in cards:
        if refresh._result_name(card.get("course")) != course:
            continue
        if any(refresh._result_name(r.get("horse")) == winner
               for r in card.get("runners", [])):
            return card
    return None


def _annotate(iso, rows):
    """Set exact finish and place terms from the archived card and result page."""
    positions = _positions(iso)
    cards = _cards(iso)
    changed = 0
    for row in rows:
        card = _card_for(cards, row)
        if card:
            row["time"] = card.get("time", row.get("time", ""))
            if not row.get("race"):
                row["race"] = f"{row['time']} {card.get('course', '')}".strip()

        key = (refresh._result_name(row.get("course")),
               refresh._result_name(row.get("winner")))
        found = positions.get(key)
        if not found:
            continue
        _, race_name, runners = found
        row["places"] = [html.unescape(h) for _, h in runners[:3]]

        pick = row.get("pick")
        if not pick:
            continue
        runner = next((r for r in runners
                       if refresh._result_name(r[1]) == refresh._result_name(pick)), None)
        if not runner:
            continue
        pos, _ = runner
        row["pick_finish"] = _ordinal(pos)

        race_text = str((card or {}).get("race") or race_name)
        handicap = bool(re.search(r"\b(handicap|hcp)\b", race_text, re.I))
        card_runners = len((card or {}).get("runners", [])) or len(runners)
        places, fraction = eachway.place_terms(card_runners, handicap)
        row["place_places"] = places
        row["place_fraction"] = fraction
        row["placed"] = bool(places and pos <= places)
        changed += 1
    return changed


def _clean(rows):
    for row in rows:
        for key in ("course", "race", "winner"):
            row[key] = html.unescape(str(row.get(key) or ""))
        row["places"] = [html.unescape(str(h)) for h in row.get("places", [])]
    return rows


def _time(row):
    t = str(row.get("time") or "").strip()
    if not t:
        m = re.search(r"(\d{1,2}:\d{2})", str(row.get("race") or ""))
        t = m.group(1) if m else ""
    return t[:5]


def _merge_irish(iso, rows):
    try:
        import fetch_results_ir
        extra = fetch_results_ir.fetch_day(iso)
    except Exception as e:
        print(f"  results ir:  skipped ({e})")
        return 0
    have = {(refresh._result_name(r.get("course")), _time(r)) for r in rows}
    added = 0
    for r in extra:
        key = (refresh._result_name(r.get("course")), _time(r))
        if key in have:
            continue
        rows.append({"course": r["course"], "time": r.get("time", ""),
                     "race": r.get("race", ""), "winner": r["winner"],
                     "places": r.get("places", [])})
        have.add(key)
        added += 1
    return added


def _merge_us(rows, us_rows, cards):
    """Add completed US races that have an archived card to score."""
    have = {(refresh._result_name(r.get("course")), _time(r)) for r in rows}
    added = 0
    for r in us_rows:
        if not _card_for(cards, r):
            continue
        key = (refresh._result_name(r.get("course")), _time(r))
        if key in have:
            continue
        rows.append(r)
        have.add(key)
        added += 1
    return added


def _annotate_us(iso, rows):
    """Finish status for US picks: win/place/show, i.e. top three."""
    cards = _cards(iso)
    changed = 0
    for row in rows:
        if not isinstance(row.get("finish"), dict):
            continue
        card = _card_for(cards, row)
        if not card:
            continue
        if not row.get("race"):
            row["race"] = f"{row.get('time', '')} {card.get('course', '')}".strip()
        pick = row.get("pick")
        if not pick:
            continue
        pos = next((n for h, n in row["finish"].items()
                    if refresh._result_name(h) == refresh._result_name(pick)), None)
        row["pick_finish"] = _ordinal(pos) if pos else "unplaced"
        row["place_places"] = 3
        row["place_fraction"] = 0.2
        row["placed"] = bool(pos and pos <= 3)
        changed += 1
    return changed


def _replace(html, name, body):
    return re.sub(rf"<!-- {name}_START -->.*?<!-- {name}_END -->",
                  body, html, count=1, flags=re.S)


def _merge_old(rows, old_rows):
    """Keep earlier races when a partial results feed omits them."""
    seen = {(refresh._result_name(r.get("course")), _time(r)) for r in rows}
    added = 0
    for old in old_rows:
        key = (refresh._result_name(old.get("course")), _time(old))
        if key in seen:
            continue
        rows.append(old)
        seen.add(key)
        added += 1
    return added


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    progressive = "--progressive" in argv
    argv = [a for a in argv if a != "--progressive"]
    forced = bool(argv)
    iso = argv[0] if forced else dt.date.today().isoformat()
    if not forced and not progressive and dt.datetime.now(dt.timezone.utc).hour < 21:
        print(f"  today:       {iso} not settled yet (before 21:00 UTC)")
        return 0

    rows = _clean(sl.fetch_day(iso))
    if rows:
        _merge_irish(iso, rows)
    try:
        us_rows = _clean(fetch_results_us.fetch_day(iso))
    except Exception as e:
        us_rows = []
        print(f"  results us:  skipped ({e})")
    if not rows and not us_rows:
        print(f"  today:       no completed races for {iso}")
        return 0
    added_us = _merge_us(rows, us_rows, _cards(iso))
    _clean(rows)
    if added_us:
        print(f"  results us:  +{added_us} completed US races")

    path = os.path.join(ROOT, "data", "results", f"{iso}.json")
    old_rows = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old_rows = json.load(f)
            if isinstance(old_rows, dict):
                old_rows = old_rows.get("races", [])
        except (OSError, ValueError):
            old_rows = []
    kept = refresh.carry_picks(old_rows, rows)
    added_old = _merge_old(rows, old_rows)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f"  results:     {len(rows)} races for {iso} -> data/results/{iso}.json")
    if kept:
        print(f"  picks kept:  {kept} settled races kept their original pick")
    if added_old:
        print(f"  results:     kept {added_old} earlier races missing from this feed")

    refresh.settle_day(iso)
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    checked = _annotate(iso, rows)
    checked_us = _annotate_us(iso, rows)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f"  placed:      {checked + checked_us} finishes checked for {iso}")

    import ab_tally
    snap = os.path.join(ROOT, "data", "picks_ab", f"{iso}.json")
    if not os.path.exists(snap):
        try:
            import ab_day
            ab_day.main(iso)
        except (SystemExit, Exception) as e:
            print(f"  ab:          snapshot skipped ({e})")
    ab_result = ab_tally.collect()

    us_snap = os.path.join(ROOT, "data", "picks_ab_us", f"{iso}.json")
    if not os.path.exists(us_snap):
        try:
            import ab_day
            ab_day.main(iso, metric="ts", out_dir="picks_ab_us", label="speed figure", regions=("US",))
        except (SystemExit, Exception) as e:
            print(f"  us ab:       snapshot skipped ({e})")
    overlay_path = os.path.join(ROOT, "data", "overlay_us", f"{iso}.json")
    if not os.path.exists(overlay_path):
        try:
            import fetch_overlay_us
            fetch_overlay_us.archive(iso)
        except Exception as e:
            print(f"  overlay:     snapshot skipped ({e})")
    us_ab_result = ab_tally.collect(snap_dir="picks_ab_us", regions=("US",),
                                    subject="speed figure", overlay_dir="overlay_us")
    html = open(refresh.INDEX, encoding="utf-8").read()
    html = _replace(html, "RESULTS_SL",
                    refresh.results_block_all() or refresh.results_block(iso, rows))
    html = _replace(html, "AB_SL",
                    refresh.ab_block_html(ab_result, iso))
    html = _replace(html, "US_AB_SL",
                    refresh.us_ab_block_html(us_ab_result, iso))
    open(refresh.INDEX, "w", encoding="utf-8").write(html)
    os.makedirs(os.path.join(ROOT, "dist", "public"), exist_ok=True)
    open(os.path.join(ROOT, "dist", "public", "index.html"),
         "w", encoding="utf-8").write(html)

    hits = sum(1 for r in rows if r.get("hit"))
    judged = sum(1 for r in rows if r.get("pick"))
    placed = sum(1 for r in rows if r.get("placed") is True)
    place_judged = sum(1 for r in rows if r.get("pick") and r.get("placed") is not None)
    print(f"  summary:     {judged} picks, {hits} winners, "
          f"{placed}/{place_judged} placed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
