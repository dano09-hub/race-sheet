#!/usr/bin/env python3
"""Settle Hong Kong picks from the official HKJC result feed.

The same GraphQL meeting the card comes from fills ``finalPosition`` once a
race is official, so the result is the track's own, not a report scraped from
somewhere else. A race is settled only when every declared runner (reserves and
scratched horses are not in the field) carries a finishing position; a partial
feed leaves the race open rather than guessing.

The pick is the page's own model pick, scored from the archived card with
tooling/score_cards.js, exactly as the UK/IRE ledger is settled. A pick already
on file is never rewritten: re-running this tool only adds races that have
since been resulted.

    python3 tooling/settle_hk.py 2026-11-18
    python3 tooling/settle_hk.py 2026-11-18 --data-only
"""
import datetime as dt
import glob
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import feed                                               # noqa: E402
import refresh                                            # noqa: E402
import scrape_hk                                          # noqa: E402
import settle                                             # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "results_hk")
PAGE = os.path.join(ROOT, "index.html")
DIST = os.path.join(ROOT, "dist", "public", "index.html")


def _race_no(card):
    m = re.search(r"-r(\d+)$", str(card.get("id") or ""))
    return int(m.group(1)) if m else 0


def _name(value):
    return refresh._result_name(value)


def _ordinal(pos):
    if 10 <= pos % 100 <= 20:
        return f"{pos}th"
    return f"{pos}{dict({1: 'st', 2: 'nd', 3: 'rd'}).get(pos % 10, 'th')}"


def _cards(date_iso):
    """The Hong Kong cards filed for the day, dated snapshot first."""
    paths = [os.path.join(ROOT, "data", "cards", f"{date_iso}.json"),
             os.path.join(ROOT, "data", "cards", "by_date", f"{date_iso}.json")]
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        cards = data.get("HK") if isinstance(data, dict) else []
        if isinstance(cards, list) and cards:
            return cards
    return []


def parse(payload, cards):
    """Official finish order per race, keyed by race number.

    Only races whose result is complete are returned: a meeting's later races
    may still be ``DECLARED`` or, just after the off, carry positions for only
    part of the field. Those are left for a later run.
    """
    by_no = {_race_no(c): c for c in cards if _race_no(c)}
    meets = (payload.get("data") or {}).get("raceMeetings") or []
    if not meets:
        return {}
    out = {}
    for race in meets[0].get("races") or []:
        no = int(race.get("no") or 0)
        card = by_no.get(no)
        if not no or not card:
            continue
        if str(race.get("status") or "").strip().upper() != "RESULT":
            continue
        finishers, incomplete = [], False
        for runner in race.get("runners") or []:
            if str(runner.get("standbyNo") or "").strip():
                continue
            status = str(runner.get("status") or "").strip().lower()
            if status in scrape_hk.NON_RUNNERS:
                continue
            name = (runner.get("name_en") or "").strip()
            pos = int(runner.get("finalPosition") or 0)
            if not name or pos <= 0:
                incomplete = True
                break
            finishers.append((pos, name))
        if incomplete or not finishers:
            print(f"# HK R{no}: official result incomplete; race left open",
                  file=sys.stderr)
            continue
        # Keep the card's own capitalisation where the horse is on it, so the
        # page can look the pick up in this map without normalising names.
        card_names = {_name(r.get("horse")): r.get("horse")
                      for r in card.get("runners", [])}
        ordered = sorted(finishers, key=lambda x: (x[0], x[1]))
        finish, places = {}, []
        for pos, official in ordered:
            horse = card_names.get(_name(official), official.title())
            finish[horse] = pos
            if pos <= 3:
                places.append(horse)
        winners = [h for h, p in finish.items() if p == 1]
        out[no] = {
            "race_no": no,
            "winner": " / ".join(winners),
            "places": places,
            "finish": finish,
            "field": len(finish),
            "dead_heat": len(winners) > 1,
        }
    return out


def _previous(date_iso):
    path = os.path.join(OUT_DIR, f"{date_iso}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    rows = data.get("races", []) if isinstance(data, dict) else data
    return {int(r.get("race_no") or 0): r for r in rows
            if isinstance(r, dict) and r.get("race_no")}


def _picks(cards):
    """The page's pick for each card, keyed by race number."""
    got = settle.score_cards(cards) or []
    return {_race_no(c): p.get("pick", "") for c, p in zip(cards, got)}


def build(date_iso, cards, official, keep=None):
    """Rows for the day's settled races, with stored picks kept as they are."""
    prev = _previous(date_iso) if keep is None else keep
    scored = _picks(cards)
    rows = dict(prev)
    added = 0
    for card in cards:
        no = _race_no(card)
        res = official.get(no)
        if not res:
            continue
        if no in rows and rows[no].get("pick"):
            # A settled pick is history: never re-score or rewrite it.
            continue
        pick = scored.get(no) or ""
        pos = res["finish"].get(pick) if pick else None
        if not pick:
            continue
        # Declared on the card but not in the official field: a non-runner.
        pick_finish = _ordinal(pos) if pos else "NR"
        rows[no] = {
            "course": card.get("course", ""),
            "time": card.get("time", ""),
            "race": card.get("race", ""),
            "race_no": no,
            "region": "HK",
            "winner": res["winner"],
            "places": res["places"],
            "finish": res["finish"],
            "field": res["field"],
            "pick": pick,
            "pick_finish": pick_finish,
            "hit": bool(pos == 1),
        }
        added += 1
    ordered = sorted(rows.values(),
                     key=lambda r: (r.get("time", ""), r.get("race_no") or 0))
    return ordered, added


def save(date_iso, rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{date_iso}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": date_iso, "source": "HKJC official race result",
                   "races": rows}, f, ensure_ascii=False, indent=1)
    return path


def attach(cards, date_iso):
    """Put the stored official outcome on each Hong Kong card for the page."""
    prev = _previous(date_iso)
    got = 0
    for card in cards:
        row = prev.get(_race_no(card))
        if not row or not row.get("pick"):
            continue
        if not row.get("finish"):
            continue
        card["result"] = {
            "winner": row.get("winner", ""),
            "places": row.get("places", []),
            "finish": row.get("finish", {}),
            "field": row.get("field"),
            "pick": row.get("pick", ""),
            "pick_finish": row.get("pick_finish", ""),
            "hit": bool(row.get("hit")),
        }
        got += 1
    return got


def _day_results(iso, rows):
    """One day's Hong Kong ledger, in the same table shape as the UK one."""
    rows = sorted(rows, key=lambda r: (r.get("time", ""), r.get("race_no") or 0))
    judged = [r for r in rows if r.get("pick")]
    wins = sum(1 for r in judged if r.get("hit"))
    trs = []
    for r in rows:
        places = " &middot; ".join(html.escape(str(p)) for p in r.get("places") or [])
        pick = r.get("pick") or ""
        finish = r.get("pick_finish") or ""
        if pick:
            cell = ('<td class="pick %s">%s %s%s</td>'
                    % ("hit" if r.get("hit") else "miss",
                       "\u2713" if r.get("hit") else "\u2717",
                       html.escape(pick),
                       (" (%s)" % html.escape(finish)) if finish else ""))
        else:
            cell = '<td class="pick none">card not kept</td>'
        trs.append(
            '<tr><td><span class="rtime">%s</span></td><td><b>%s</b></td>'
            '<td>%s</td><td>%s</td><td class="muted">%s</td>%s</tr>'
            % (html.escape(str(r.get("time") or "")[:5]),
               html.escape(str(r.get("course") or "")),
               html.escape(str(r.get("race") or "")),
               html.escape(str(r.get("winner") or "")), places, cell))
    label = dt.datetime.strptime(iso, "%Y-%m-%d").strftime("%-d %B %Y")
    noun = "race" if len(rows) == 1 else "races"
    return (
        '<h3>Settled &mdash; %s</h3>\n'
        '<p class="method">%d Hong Kong %s settled. <b>%d of %d</b> model picks '
        'won. Winner and the first three home, with the off time in UTC.</p>\n'
        '<div class="scroll"><table class="ledger"><thead><tr><th>Off (UTC)</th>'
        '<th>Course</th><th>Race</th><th>Winner</th><th>First three</th>'
        '<th>Our pick</th></tr></thead><tbody>%s</tbody></table></div>\n'
        % (label, len(rows), noun, wins, len(judged), "".join(trs)))


def results_block():
    """The Hong Kong results tab: every settled HK day on file."""
    parts = []
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "*.json")), reverse=True):
        iso = os.path.basename(path)[:-5]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        rows = data.get("races", []) if isinstance(data, dict) else data
        if isinstance(rows, list) and rows:
            parts.append(_day_results(iso, rows))
    body = ("\n".join(parts) if parts else
            '<p class="method">No Hong Kong results yet. A race appears here '
            'once the official HKJC result carries a finishing position for '
            'every declared runner.</p>')
    return "<!-- HK_RESULTS_SL_START -->\n" + body + "\n<!-- HK_RESULTS_SL_END -->"


def replace_results(page):
    return re.sub(r"<!-- HK_RESULTS_SL_START -->.*?<!-- HK_RESULTS_SL_END -->",
                  results_block(), page, count=1, flags=re.S)


def inject(date_iso, cards):
    """Rebuild the CARDS_HK block and the results tab, then write dist."""
    if not os.path.exists(PAGE):
        return 0
    page = open(PAGE, encoding="utf-8").read()
    if cards:
        head, body = refresh.serialise(cards, f"Hong Kong cards, {date_iso}")
        page = refresh.replace_block(page, "CARDS_HK", head + body)
    page = replace_results(page)
    open(PAGE, "w", encoding="utf-8").write(page)
    if os.path.exists(os.path.dirname(DIST)):
        open(DIST, "w", encoding="utf-8").write(page)
    return len([c for c in cards if c.get("result")])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    data_only = "--data-only" in argv
    argv = [a for a in argv if a != "--data-only"]
    date_iso = argv[0] if argv else dt.date.today().isoformat()
    # The graphql gateway only ever serves the current meeting; a past date is
    # returned as today's runners. Settling anything else would tag a card with
    # another meeting's result, so only the current UTC day is accepted.
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if date_iso != today:
        print(f"  HK results:  {date_iso} is not the current HKJC meeting; left unchanged")
        return 0
    cards = _cards(date_iso)
    if not cards:
        print(f"  HK results:  no Hong Kong card filed for {date_iso}")
        return 0
    try:
        payload = scrape_hk.fetch(date_iso.replace("-", "/"))
    except feed.FeedError as e:
        # An outage is not "no result yet". This run must go red so nobody
        # publishes a page whose Hong Kong ledger was silently skipped.
        print(f"  HK results:  FAILED ({e})")
        raise
    except Exception as e:
        print(f"  HK results:  FAILED ({e})")
        raise feed.FeedError(f"HKJC meeting for {date_iso}: {e}") from e
    official = parse(payload, cards)
    rows, added = build(date_iso, cards, official)
    if added:
        save(date_iso, rows)
    settled = [r for r in rows if r.get("pick")]
    wins = sum(1 for r in settled if r.get("hit"))
    print(f"  HK results:  {added} new, {len(settled)} settled, {wins} winners "
          f"for {date_iso}")
    if not data_only:
        attached = attach(cards, date_iso)
        inject(date_iso, cards)
        print(f"  HK page:     {attached} races carry their official outcome")
    return 0


if __name__ == "__main__":
    sys.exit(main())
