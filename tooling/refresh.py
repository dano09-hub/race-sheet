def _archive_key(card, region):
    """Stable identity for a card when merging an archive day.

    US cards can arrive with cosmetic case changes in the distance label
    (``6 f`` vs ``6F``), which are the same race.  Normalise only US here so
    the separate GB source variants keep their richer card metadata.
    """
    course = str(card.get("course") or "").strip()
    time = str(card.get("time") or "").strip()
    race = str(card.get("race") or "").strip()
    if str(region).upper() == "US":
        # Drop case and spacing differences (``6 f`` / ``6F``) from the key.
        race = re.sub(r"\s+", "", race).casefold()
        course = course.casefold()
    return (course, time, race)


def archive(iso, sets):
    """Keep every pre-race card the run fetched, filed under the day it RUNS.

    Two copies are written: a dated snapshot of this run (data/cards/<iso>.json)
    and merged by-date files. Merging means a later run that only covers some
    meetings cannot drop cards an earlier run captured, while a race already on
    file is refreshed with the newer card, so a day can always be scored from
    the card the page actually showed.
    """
    d = os.path.join(ROOT, "data", "cards")
    os.makedirs(d, exist_ok=True)
    payload = {k: v or [] for k, v in sets.items()}
    with open(os.path.join(d, f"{iso}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)
    total = sum(len(v) for v in payload.values())

    by_day = {}
    for reg, cards in payload.items():
        for c in cards:
            day = c.get("day") or iso
            key = "GB" if str(reg).startswith("GB") else reg
            by_day.setdefault(day, {}).setdefault(key, []).append(c)

    bdir = os.path.join(d, "by_date")
    os.makedirs(bdir, exist_ok=True)
    filed = 0
    for day, regions in by_day.items():
        path = os.path.join(bdir, f"{day}.json")
        cur = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cur = json.load(f) or {}
            except (OSError, ValueError):
                cur = {}
        if not isinstance(cur, dict):
            cur = {"GB": cur}  # very old list-shaped archives
        for reg, cards in regions.items():
            bucket = cur.setdefault(reg, [])
            if not isinstance(bucket, list):
                bucket = cur[reg] = []
            if str(reg).upper() == "US":
                # Remove historical case-only duplicates as well as preventing
                # new ones; keep the first card seen for each race.
                seen = set()
                clean = []
                for x in bucket:
                    key3 = _archive_key(x, reg)
                    if key3 in seen:
                        continue
                    seen.add(key3)
                    clean.append(x)
                bucket[:] = clean
            by_key = {}
            for i, x in enumerate(bucket):
                by_key.setdefault(_archive_key(x, reg), i)
            for c in cards:
                key3 = _archive_key(c, reg)
                if key3 in by_key:
                    bucket[by_key[key3]] = c
                else:
                    by_key[key3] = len(bucket)
                    bucket.append(c)
                    filed += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
    print(f"  archived:    {total} cards -> data/cards/{iso}.json; {filed} new by date")


def settle_day(iso):
    """Score a settled day from its archived cards and write the pick into results."""
    path = os.path.join(ROOT, "data", "cards", "by_date", f"{iso}.json")
    rpath = os.path.join(ROOT, "data", "results", f"{iso}.json")
    if not (os.path.exists(path) and os.path.exists(rpath)):
        return 0
    try:
        import settle
    except Exception:
        return 0
    cards = []
    with open(path, encoding="utf-8") as f:
        for reg, cs in json.load(f).items():
            cards += cs
    with open(rpath, encoding="utf-8") as f:
        rows = json.load(f)
    filled = 0
    chances = 0
    for row in rows:
        if row.get("pick") and row.get("chance") is not None:
            continue
        card = next((c for c in cards
                     if c.get("course") == row.get("course")
                     and any(r.get("horse") == row.get("winner") for r in c.get("runners", []))), None)
        if not card:
            continue
        scored = settle.scored(card)
        if not scored or not scored.get("pick"):
            continue
        pick, chance = scored["pick"], scored.get("chance")
        if row.get("pick"):
            # A stored pick is never rewritten. The chance can be attached only
            # when the archived card gives back exactly the pick on record, and
            # no card snapshot is stored: this card is a re-scrape, not the one
            # the pick was made from.
            if pick == row["pick"] and chance is not None:
                row["chance"] = chance
                chances += 1
            continue
        row["pick"], row["score"] = pick, None
        row["chance"] = chance
        # Freeze the card the pick came from, so a later backfill never has to
        # trust whatever the archive holds tomorrow.
        row["card"] = settle.snapshot_of(scored)
        row["hit"] = (pick == row.get("winner"))
        row["pick_finish"] = ""
        filled += 1
    annotated = annotate_result_rows(iso, rows)
    if filled or chances or annotated:
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
    print(f"  settled:     {filled} picks written for {iso}")
    if chances:
        print(f"  chances:     {chances} stored picks given the chance they were shown at")
    if annotated:
        print(f"  placed:      {annotated} finishes checked for {iso}")
    return filled


def _utcnow():
    return (dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def carry_picks(old_rows, new_rows):
    """Keep the original pick when a settled day is scraped again.

    A results refresh must never rewrite history: the pick was made before the
    race, so if the same race appears in the new scrape its stored pick, finish
    and fate are carried over. Only genuinely new races are left to settle.
    The same match carries the settlement time; a race never seen before is
    stamped now, while a row filed before timestamps existed stays unstamped
    rather than being given a made-up time.
    """
    by_time, by_winner = {}, {}
    all_time, all_winner = {}, {}
    for r in old_rows or []:
        t = (r.get("time") or "").strip()
        if t:
            all_time[(r.get("course"), t)] = r
        all_winner[(r.get("course"), r.get("winner"))] = r
        if not r.get("pick"):
            continue
        if t:
            by_time[(r.get("course"), t)] = r
        by_winner[(r.get("course"), r.get("winner"))] = r
    now = _utcnow()
    kept = 0
    for r in new_rows:
        t = (r.get("time") or "").strip()
        old = all_time.get((r.get("course"), t)) if t else None
        old = old or all_winner.get((r.get("course"), r.get("winner")))
        if old is not None:
            if old.get("settled_at"):
                r["settled_at"] = old["settled_at"]
        elif not r.get("settled_at"):
            r["settled_at"] = now
        prev = by_time.get((r.get("course"), t)) if t else None
        prev = prev or by_winner.get((r.get("course"), r.get("winner")))
        if not prev:
            continue
        for k in ("pick", "pick_finish", "hit"):
            if k in prev:
                r[k] = prev[k]
        kept += 1
    return kept


def merge_foreign_results(rows, old_rows):
    """Keep US rows when a UK/IRE results scrape rewrites the whole day.

    The UK and Irish feeds do not know the US card, so a day with both would
    otherwise lose its US half on every refresh. Rows are matched on course
    and winner; the fresh row always wins for a race both feeds know.
    """
    have = {(_result_name(r.get("course")), _result_name(r.get("winner")))
            for r in rows}
    added = 0
    for old in old_rows or []:
        if str(old.get("region") or "").upper() not in ("US", "USA"):
            continue
        key = (_result_name(old.get("course")), _result_name(old.get("winner")))
        if key in have:
            continue
        rows.append(old)
        have.add(key)
        added += 1
    return added


def _result_name(value):
    """Normalise a horse/course name for matching result rows."""
    import html as _html
    s = _html.unescape(str(value or "")).lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _result_pos(value):
    """Return the numeric finish from values such as ``3rd`` or ``PU``."""
    m = re.match(r"\s*(\d+)", str(value or ""))
    return int(m.group(1)) if m else None


def _is_handicap(race):
    return bool(re.search(r"\b(handicap|hcp)\b", str(race.get("title", "")), re.I))


def annotate_result_rows(iso, rows):
    """Add finish and standard each-way place status to settled-result rows.

    The full result file has every finishing position; the summary file only
    stores the first three. This looks up the original pick in the full result
    and marks whether it finished inside the paid places for its field size and
    handicap status. It never changes ``pick`` or ``hit``.
    """
    full_path = os.path.join(ROOT, "data", f"{iso}.json")
    if not os.path.exists(full_path):
        return 0
    try:
        with open(full_path, encoding="utf-8") as f:
            full = json.load(f)
    except (OSError, ValueError):
        return 0

    by_course_winner = {}
    for meeting in full.get("meetings", []):
        course = meeting.get("course", "")
        for race in meeting.get("races", []):
            for runner in race.get("runners", []):
                key = (_result_name(course), _result_name(runner.get("horse")))
                by_course_winner.setdefault(key, []).append(race)

    changed = 0
    for row in rows or []:
        pick = row.get("pick")
        if not pick:
            continue
        key = (_result_name(row.get("course")), _result_name(row.get("winner")))
        candidates = by_course_winner.get(key) or []
        if not candidates:
            continue
        row_time = str(row.get("time") or "").strip()
        if not row_time:
            m = re.search(r"(\d{1,2}:\d{2})", str(row.get("race") or ""))
            row_time = m.group(1) if m else ""
        race = next((r for r in candidates
                     if str(r.get("time", "")).strip() == row_time), None)
        race = race or candidates[0]
        runners = race.get("runners", [])
        runner = next((r for r in runners
                       if _result_name(r.get("horse")) == _result_name(pick)), None)
        if not runner:
            continue

        finish = str(runner.get("pos") or "").strip()
        if finish and not row.get("pick_finish"):
            row["pick_finish"] = finish
        if not row.get("places"):
            row["places"] = [str(r.get("horse", "")) for r in runners[:3]]
        field = len([r for r in runners
                     if str(r.get("pos", "")).strip().upper() not in ("NR", "WD")])
        places, fraction = eachway.place_terms(field, _is_handicap(race))
        pos = _result_pos(finish)
        row["place_places"] = places
        row["place_fraction"] = fraction
        row["placed"] = bool(places and pos is not None and pos <= places)
        changed += 1
    return changed


#!/usr/bin/env python3
"""Refresh the page in one command: scrape, rebuild, stage for deploy.

    python3 tooling/refresh.py            # today + tomorrow UK, today US, today results
    python3 tooling/refresh.py --days 3   # widen the UK window
    python3 tooling/refresh.py --data-only  # re-render Results from data/, no scraping

Steps
  1. UK racecards  -> RACE_CARDS (today), CARDS_FRI (tomorrow)
  2. US entries    -> CARDS_US (today, US tracks only)
  3. HKJC card     -> CARDS_HK (today, official rating/form/draw/post times)
  4. settled results -> data/YYYY-MM-DD.json (feeds the back-test dataset)
  5. rewrite the three JS blocks inside index.html
  6. copy to dist/public/index.html

A block that scrapes empty is left untouched, so a blocked or quiet day can
never wipe a card that already works.
"""

import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from parse_cards import get, parse_course_page          # noqa: E402
from scrape_us import get as us_get, parse_track        # noqa: E402
import eachway                                          # noqa: E402
import scrape_results                                   # noqa: E402
try:
    import scrape_hk
except Exception:                                       # pragma: no cover
    scrape_hk = None

try:
    import fetch_results_sl                             # noqa: E402
except Exception:                                       # pragma: no cover
    fetch_results_sl = None

ROOT = os.path.join(HERE, "..")
INDEX = os.path.join(ROOT, "index.html")
CHECKS = os.path.join(HERE, "test_checks.py")


def run_checks():
    """Run the fixture suite before touching the page. Returns (ok, output).

    A separate process, so a failing check cannot take the refresh down with it
    and nothing it imports can leak into the build.
    """
    import subprocess
    if not os.path.exists(CHECKS):
        return True, "no checks file at %s" % CHECKS
    try:
        p = subprocess.run([sys.executable, CHECKS], capture_output=True, text=True,
                           timeout=180)
    except subprocess.TimeoutExpired:
        return False, "checks timed out after 180s"
    return p.returncode == 0, ((p.stdout or "") + (p.stderr or ""))


def preflight(skip_checks=False):
    """Gate the build on the fixture suite. Returns an exit code to stop, else None."""
    if skip_checks:
        msg = "CHECKS SKIPPED (--skip-checks) - this build is unverified"
        print("=" * len(msg))
        print(msg)
        print("=" * len(msg))
        try:
            path = os.path.join(ROOT, "data", "checks_skipped.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                        + "  checks skipped via --skip-checks\n")
            print(f"  recorded in {os.path.relpath(path, ROOT)}")
        except Exception as e:
            print(f"  could not write the skip record: {e}")
        return None
    ok, check_out = run_checks()
    if not ok:
        print("checks FAILED - build stopped; index.html and dist are untouched")
        for line in check_out.strip().splitlines()[-25:]:
            print("  " + line)
        return 3
    last = check_out.strip().splitlines()[-1] if check_out.strip() else "ok"
    print(f"  checks:      {last}")
    return None

RUNNER_KEYS = ("horse", "odds", "form", "or", "rpr", "ts", "weight", "days",
               "course", "distance", "going", "jp", "tp", "tips", "naps")


def js(s):
    return json.dumps(str(s), ensure_ascii=False)


def serialise(cards, comment):
    out = [f"  // {comment}"]
    name = "BLOCK"
    body = []
    for c in cards:
        # HK cards carry the official programme number and barrier draw, so the
        # card view can show the draw the model scores. Other regions keep the
        # block they already had: their draw was never part of the page's card.
        extra = (("draw", "no")
                 if str(c.get("region", "")).upper() in ("HK", "FR", "CL") else ())
        def runner(r):
            keys = [f"{k}:{js(r.get(k, ''))}" for k in RUNNER_KEYS]
            keys += [f"{k}:{js(r.get(k, ''))}" for k in extra]
            return "        {" + ", ".join(keys) + "}"
        runners = ",\n".join(runner(r) for r in c["runners"])
        head = (f'      id: {js(c["id"])}, label: {js(c["label"])}, course: {js(c["course"])}, '
                f'time: {js(c["time"])},\n      day: {js(c["day"])}, region: {js(c["region"])},')
        if c.get("iso"):
            head += f'\n      iso: {js(c["iso"])},'
        result_line = ""
        if c.get("result"):
            # HK cards carry the official outcome so the tab can show what the
            # settled pick did, without a runtime fetch.
            result_line = ("\n      result: "
                           + json.dumps(c["result"], ensure_ascii=False,
                                        separators=(",", ":")) + ",")
        body.append("    {\n" + head +
                    f'\n      race: {js(c["race"])}, going: {js(c["going"])}, '
                    f'klass: {js(c["klass"])}, note: {js(c.get("note", ""))},'
                    f'{result_line}\n'
                    "      runners: [\n" + runners + "\n      ]\n    }")
    return out, body


def archived_cards(date_iso):
    """GB cards already on file for a day, so a later run cannot lose a meeting.

    horseracing.net stops listing a meeting on its card index once the last race
    has run, so an afternoon or evening refresh used to replace the day's page
    with only the meetings still to come. The by-date archive is filed under the
    day a card runs, so those are the same day's cards, not stale data.
    """
    path = os.path.join(ROOT, "data", "cards", "by_date", f"{date_iso}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for reg, cards in data.items():
        if not str(reg).upper().startswith("GB") or not isinstance(cards, list):
            continue
        for c in cards:
            if isinstance(c, dict) and c.get("runners"):
                out.append(c)
    return out


def _card_key(card):
    return (card.get("course"), card.get("time"), card.get("race"))


def uk_day(date_iso):
    d = dt.date.fromisoformat(date_iso)
    slug_date = d.strftime("%d-%m-%y")
    index = get(f"https://www.horseracing.net/racecards/{slug_date}")
    slugs = set(re.findall(rf'href="/([a-z0-9-]+)/{re.escape(slug_date)}"', index))
    cards = archived_cards(date_iso)
    by_key = {_card_key(c): i for i, c in enumerate(cards)}
    # Fetch a course page for every meeting the index still lists and for
    # every meeting already archived today, so a finished meeting is refreshed
    # rather than quietly dropped.
    for c in cards:
        course = re.sub(r"[^a-z0-9]+", "-", str(c.get("course") or "").lower()).strip("-")
        if course:
            slugs.add(course)
    for slug in sorted(slugs):
        page = get(f"https://www.horseracing.net/{slug}/{slug_date}")
        if not page:
            continue
        course = slug.replace("-", " ").title()
        for c in parse_course_page(page, course, slug_date):
            title = c["race"]
            c["label"] = (title[:44] + "…") if len(title) > 45 else title
            c["day"] = date_iso
            c["region"] = "GB"
            key = _card_key(c)
            if key in by_key:
                cards[by_key[key]] = c
            else:
                by_key[key] = len(cards)
                cards.append(c)
    return cards


def us_day(date_iso):
    day = us_get(f"https://entries.horseracingnation.com/entries-results/{date_iso}")
    slugs = sorted(set(re.findall(rf'href="/entries-results/([a-z0-9-]+)/{date_iso}"', day)))
    cards = []
    for slug in slugs:
        page = us_get(f"https://entries.horseracingnation.com/entries-results/{slug}/{date_iso}")
        course = slug.replace("-", " ").title()
        for c in parse_track(page, course, date_iso):
            c["day"] = date_iso
            c["region"] = "US"
            cards.append(c)
    return cards


def hk_day(date_iso):
    """Sha Tin / Happy Valley card from the official HKJC sources (plain HTTP).

    The GraphQL gateway supplies the declared field, official rating, last-six
    form, barrier draw, weight and price. The race-card page supplies the
    meeting, race name and post time; a race without that header is skipped
    rather than given a guessed time.
    """
    if scrape_hk is None:
        return []
    try:
        payload = scrape_hk.fetch(date_iso.replace("-", "/"))
        meta = scrape_hk.fetch_meta(date_iso, scrape_hk.race_numbers(payload))
        return scrape_hk.cards(payload, date_iso, meta)
    except Exception as e:
        print(f"  HK: {e}")
        return []


def au_day():
    """Australian cards are harvested through a browser (Cloudflare); refresh.py
    just picks up the file that harvest leaves behind."""
    path = os.path.join(ROOT, "data", "au_cards.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _rated_day(iso):
    """The six-input re-score for one settled day, or {} when it has none.

    data/results_rated is a derived view: it never replaces the pre-race pick
    stored in data/results, it only says what the strict model would have picked
    from the archived card and whether that race could be rated at all.
    """
    path = os.path.join(ROOT, "data", "results_rated", iso + ".json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data.get("races") or []


def _rated_for(rated, row):
    """The re-score row that belongs to a settled result, or None."""
    course = _result_name(row.get("course"))
    winner = _result_name(row.get("winner"))
    time = str(row.get("time") or "")[:5]
    for x in rated:
        if _result_name(x.get("course")) != course:
            continue
        if time and str(x.get("time") or "")[:5] != time:
            continue
        if _result_name(x.get("winner")) == winner:
            return x
    return None


def _missing_text(missing):
    """Name the gap the way the Today tab does: some runners, or the whole field."""
    parts = []
    for m in missing or []:
        what = m.get("what") or "an input"
        n = m.get("n")
        total = m.get("of")
        if not n:
            parts.append(what)
        elif total and n >= total:
            parts.append("no %s for all %d runners" % (what, total))
        elif total:
            parts.append("%d of %d runners %s no %s"
                         % (n, total, "has" if n == 1 else "have", what))
        else:
            parts.append("%s (%d runners)" % (what, n))
    return "; ".join(parts)


def _results_inner(iso, rows, region):
    """Build one day's ledger table for a single region group.

    When data/results_rated has the day, the pick shown is the six-input
    model's re-score of the archived card, and a race with a gap is marked
    Not rated instead of falling back to the old twelve-factor pick. The stored
    pre-race pick is left untouched in data/results.
    """
    import html as _h
    rated = _rated_day(iso)
    rows = sorted(rows, key=lambda r: (r["course"], r.get("time", ""), r.get("race", "")))
    hits_box, judged_box = [0], [0]
    placed_box, place_judged = [0], [0]
    rated_box = [0]
    nocards = 0
    trs = []
    for r in rows:
        places = " &middot; ".join(_h.escape(p) for p in r.get("places", []))
        t = r.get("race", "")
        m = re.search(r"(\d{1,2}:\d{2})", t)
        time_attr = r.get("time") or (m.group(1) if m else "")
        course_html = (('<span class="rtag">USA</span> ' if r.get("region") == "US" else '')
                       + _h.escape(r["course"]))
        time_html = ""
        if time_attr:
            time_html = ' <span class="rtime">%s</span>' % _h.escape(str(time_attr)[:5])

        got = _rated_for(rated, r)
        if got is not None and got.get("state") == "rated":
            rated_box[0] += 1
            pk = got.get("pick") or ""
            fin = got.get("finish") or ""
            chance = got.get("chance")
            chance_html = (" <span class=\"pickchance\" title=\"Six-input win chance "
                           "on the re-score\">%.1f%%</span>" % float(chance)
                           if chance is not None else "")
            hit = bool(got.get("hit"))
            cell = '<td class="pick %s">%s%s%s%s</td>' % (
                "hit" if hit else "miss", "\u2713 " if hit else "\u2717 ",
                _h.escape(pk), (" (%s)" % _h.escape(fin)) if fin else "", chance_html)
            if hit:
                hits_box[0] += 1
            judged_box[0] += 1
            terms = got.get("place_places")
            if terms == 0 or terms is None:
                place_cell = '<td class="placed winonly">Win only</td>'
            elif got.get("placed") is True:
                place_cell = '<td class="placed yes">Yes</td>'
                placed_box[0] += 1
                place_judged[0] += 1
            elif got.get("placed") is False:
                place_cell = '<td class="placed no">No</td>'
                place_judged[0] += 1
            else:
                place_cell = '<td class="placed unknown">&mdash;</td>'
        elif got is not None and got.get("state") == "no-card":
            nocards += 1
            cell = '<td class="pick none">card not kept</td>'
            place_cell = '<td class="placed unknown">&mdash;</td>'
        elif got is not None:
            rated_box[0] += 1
            why = _missing_text(got.get("missing"))
            cell = ('<td class="pick none">not rated'
                    + (' <span class="pickchance" title="%s">%s</span>'
                       % (_h.escape(why), _h.escape(why.split(";")[0])) if why else "")
                    + '</td>')
            place_cell = '<td class="placed unknown">&mdash;</td>'
        elif rated:
            cell = '<td class="pick none">not re-scored</td>'
            place_cell = '<td class="placed unknown">&mdash;</td>'
        else:
            pk = r.get("pick", "")
            if pk:
                fin = r.get("pick_finish", "")
                chance = r.get("chance")
                chance_html = ""
                if chance is not None and r.get("region") != "US":
                    chance_html = (' <span class="pickchance" title="Model win chance '
                                   'before the off">%.1f%%</span>' % float(chance))
                cell = '<td class="pick %s">%s%s%s%s</td>' % (
                    "hit" if r.get("hit") else "miss",
                    "\u2713 " if r.get("hit") else "\u2717 ", _h.escape(pk),
                    (" (%s)" % _h.escape(fin)) if fin else "", chance_html)
                if r.get("hit"):
                    hits_box[0] += 1
                judged_box[0] += 1
                place_places = r.get("place_places")
                if place_places == 0:
                    place_cell = '<td class="placed winonly">Win only</td>'
                elif r.get("placed") is True:
                    place_cell = '<td class="placed yes">Yes</td>'
                    placed_box[0] += 1
                    place_judged[0] += 1
                elif r.get("placed") is False:
                    place_cell = '<td class="placed no">No</td>'
                    place_judged[0] += 1
                else:
                    place_cell = '<td class="placed unknown">&mdash;</td>'
            else:
                cell = '<td class="pick none">card not kept</td>'
                place_cell = '<td class="placed unknown">&mdash;</td>'
        trs.append('<tr data-course="%s" data-time="%s" data-winner="%s">'
                   '<td><b>%s</b>%s</td><td>%s</td><td>%s</td><td class="muted">%s</td>%s%s</tr>'
                   % (_h.escape(r["course"]), _h.escape(str(time_attr)), _h.escape(r["winner"]),
                      course_html, time_html, _h.escape(t[:44]) or "&mdash;",
                      _h.escape(r["winner"]), places, cell, place_cell))
    label = dt.datetime.strptime(iso, "%Y-%m-%d").strftime("%-d %B %Y")
    pick_noun = "rated pick" if judged_box[0] == 1 else "rated picks"
    place_noun = "rated pick" if place_judged[0] == 1 else "rated picks"
    top_bit = ("<b>%d of %d</b> %s won. " % (hits_box[0], judged_box[0], pick_noun)
               if judged_box[0] else "")
    place_bit = ("<b>%d of %d</b> %s placed. "
                 % (placed_box[0], place_judged[0], place_noun)
                 if place_judged[0] else "")
    scored_bit = ("<b>%d of %d</b> races with an archived card %s all six inputs; "
                  "the rest are marked Not rated. "
                  % (judged_box[0], rated_box[0],
                     "carries" if judged_box[0] == 1 else "carry")
                  if rated and rated_box[0] else "")
    if nocards:
        scored_bit += ("<b>%d</b> %s no archived card and %s marked Card not kept. "
                       % (nocards, "race has" if nocards == 1 else "races have",
                          "is" if nocards == 1 else "are"))
    if rated and not rated_box[0]:
        note = (" No archived card survives for this day, so no pick is shown; "
                "the pre-race pick on record stays in data/results.")
    else:
        note = (" Picks are re-scored from the archived card with the six-input model; "
                "the pre-race pick on record stays in data/results." if rated else "")
    noun = "race" if len(rows) == 1 else "races"
    return (
        '<h3>Settled &mdash; %s</h3>\n'
        '<p class="method">%d %s %s settled. %s%s%sWinner first, then the '
        'first three home in finishing order. Off time is shown beside each course.%s</p>\n'
        '<div class="scroll"><table class="ledger"><thead><tr><th>Course / off</th><th>Race</th>'
        '<th>Winner</th><th>Places</th><th>%s</th><th>Placed?</th></tr></thead>'
        '<tbody>%s</tbody></table></div>\n'
        % (label, len(rows), region, noun, top_bit, place_bit, scored_bit, note,
           "Rated pick" if rated else "Our pick", "".join(trs))
    )


def _result_region(row):
    """Which results tab a row belongs on, decided by its course.

    US rows are stamped when they are merged. British and Irish courses come
    from one shared list, so a day mixing Hexham and Dundalk splits cleanly. A
    course on neither list counts as British, which is where it sat before.
    """
    import courses
    if str(row.get("region") or "").upper() == "US":
        return "US"
    return courses.region_of(row.get("course")) or "GB"


def results_block(iso, rows):
    """The UK ledger. Irish and US rows have their own tabs."""
    rows = [r for r in rows if _result_region(r) == "GB"]
    if not rows:
        return ('<!-- RESULTS_SL_START -->\n'
                '<p class="method">No UK results for this day.</p>\n'
                '<!-- RESULTS_SL_END -->')
    stamp = _ledger_line(max((r.get("settled_at") or "" for r in rows), default=""),
                         iso, "ukLedger")
    return ("<!-- RESULTS_SL_START -->\n" + stamp
            + _results_inner(iso, rows, "UK")
            + "\n<!-- RESULTS_SL_END -->")


def results_block_ire(iso, rows):
    """The Irish ledger, same table and the same pre-race picks."""
    rows = [r for r in rows if _result_region(r) == "IRE"]
    if not rows:
        return ('<!-- IRE_RESULTS_SL_START -->\n'
                '<p class="method">No Irish results for this day.</p>\n'
                '<!-- IRE_RESULTS_SL_END -->')
    stamp = _ledger_line(max((r.get("settled_at") or "" for r in rows), default=""),
                         iso, "ireLedger")
    return ("<!-- IRE_RESULTS_SL_START -->\n" + stamp
            + _results_inner(iso, rows, "Irish")
            + "\n<!-- IRE_RESULTS_SL_END -->")


def _ledger_line(updated, date_iso, ident):
    """When a results ledger last changed, or the latest day if it predates this."""
    import html as _h
    if updated:
        try:
            when = dt.datetime.fromisoformat(updated.replace("Z", "+00:00"))
            label = when.strftime("%-d %B %Y, %H:%M UTC")
        except ValueError:
            return ('<p class="method" id="%s">Ledger last changed <b>%s</b>.</p>\n'
                    % (ident, _h.escape(updated)))
        return ('<p class="method" id="%s">Ledger last changed '
                '<time datetime="%s">%s</time>.</p>\n'
                % (ident, _h.escape(updated), label))
    if date_iso:
        try:
            label = dt.datetime.strptime(date_iso, "%Y-%m-%d").strftime("%-d %B %Y")
        except ValueError:
            return ""
        return ('<p class="method" id="%s">Latest result '
                '<time datetime="%s">%s</time>; the settlement time was not '
                'recorded for rows filed before this change.</p>\n'
                % (ident, _h.escape(date_iso), label))
    return ""


def _results_meta(kind):
    """Latest settlement stamp and latest day for one region's rows."""
    import glob
    latest_updated, latest_date = "", ""
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json"))):
        iso = os.path.basename(path)[:-5]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        rows = data.get("races", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            continue
        keep = [r for r in rows
                if isinstance(r, dict) and _result_region(r) == kind]
        if not keep:
            continue
        latest_date = max(latest_date, iso)
        for r in keep:
            latest_updated = max(latest_updated, r.get("settled_at") or "")
    return latest_updated, latest_date


def results_block_us(iso, rows):
    """The US-only ledger, with the off time on every row."""
    rows = [r for r in rows if r.get("region") == "US"]
    if not rows:
        return ('<!-- US_RESULTS_SL_START -->\n'
                '<p class="method">No US results for this day.</p>\n'
                '<!-- US_RESULTS_SL_END -->')
    stamp = _ledger_line(max((r.get("settled_at") or "" for r in rows), default=""),
                         iso, "usLedger")
    return ("<!-- US_RESULTS_SL_START -->\n" + stamp
            + _results_inner(iso, rows, "US")
            + "\n<!-- US_RESULTS_SL_END -->")


def _results_all(kind):
    import glob
    files = sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json")), reverse=True)
    parts = []
    for path in files:
        iso = os.path.basename(path)[:-5]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("races", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            continue
        if kind == "US":
            keep, label = [r for r in rows if _result_region(r) == "US"], "US"
        elif kind == "IRE":
            keep, label = [r for r in rows if _result_region(r) == "IRE"], "Irish"
        else:
            keep, label = [r for r in rows if _result_region(r) == "GB"], "UK"
        if keep:
            parts.append(_results_inner(iso, keep, label))
    return parts


def results_block_all():
    """Every archived day's UK ledger, newest first."""
    parts = _results_all("GB")
    if not parts:
        return None
    updated, date_iso = _results_meta("GB")
    return ("<!-- RESULTS_SL_START -->\n"
            + _ledger_line(updated, date_iso, "ukLedger")
            + "\n".join(parts) + "\n<!-- RESULTS_SL_END -->")


def results_block_all_ire():
    """Every archived day's Irish ledger, newest first."""
    parts = _results_all("IRE")
    if not parts:
        return None
    updated, date_iso = _results_meta("IRE")
    return ("<!-- IRE_RESULTS_SL_START -->\n"
            + _ledger_line(updated, date_iso, "ireLedger")
            + "\n".join(parts) + "\n<!-- IRE_RESULTS_SL_END -->")


def results_block_all_us():
    """Every archived day's US ledger, newest first."""
    parts = _results_all("US")
    if not parts:
        return None
    updated, date_iso = _results_meta("US")
    return ("<!-- US_RESULTS_SL_START -->\n"
            + _ledger_line(updated, date_iso, "usLedger")
            + "\n".join(parts) + "\n<!-- US_RESULTS_SL_END -->")


def _audit_source():
    """Latest results day that has at least one UK/IRE row."""
    import glob
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json")), reverse=True):
        iso = os.path.basename(path)[:-5]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        rows = data.get("races", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            continue
        uk = [r for r in rows if r.get("region") != "US"]
        if uk:
            return iso, uk
    return None, []


def audit_rows():
    """The latest settled UK/IRE day, scored with the page's strict model.

    Returns a list of JSON object strings ready for replace_block("RESULTS").
    Every column comes from the card tooling/rescore_results.py scored for the
    day: the pick is that card's pick, and the winner's rank, market rank and
    price are read from that same card's scored rows. The audit therefore
    always prints the rank of the pick beside it, even when two archived copies
    of a race disagree. Without a re-score on file the stored pre-race pick is
    carried and no rank is printed.
    """
    iso, rows = _audit_source()
    if not iso:
        return []
    import rescore_results as rr
    rated = _rated_day(iso)
    cards, scored, restored = [], {}, {}
    if rated:
        try:
            cards, scored, restored = rr.rate_day(iso)
        except Exception as e:
            print(f"  audit:       skipped ({e})")
            return []

    out = []
    for row in rows:
        course = row.get("course", "")
        time = str(row.get("time") or "")[:5]
        winner = row.get("winner", "")
        card = rr.pick_card(cards, row) if rated else None
        got = (rr.race_entry(cards, scored, restored, row.get("region") or "",
                             row, card=card) if rated else None)
        # The same card the re-score matched for this row, so its rows rank the
        # pick shown above rather than some other archived copy of the race.
        ranked = scored.get(id(card)) if card else None
        if got is None:
            pick = row.get("pick", "")
            finish = row.get("pick_finish", "")
        elif got.get("state") == "rated":
            pick = got.get("pick", "")
            finish = got.get("finish", "")
        else:
            pick = "card not kept" if got.get("state") == "no-card" else "not rated"
            finish = ""

        rank, field, market, price = None, 0, None, ""
        if ranked and ranked.get("rated"):
            runners = ranked.get("runners") or []
            field = len(runners)
            for idx, x in enumerate(runners):
                if _result_name(x.get("horse")) == _result_name(winner):
                    rank = idx + 1
                    price = x.get("odds") or ""
                    share = x.get("share")
                    if share is not None:
                        market = 1 + sum(1 for y in runners
                                         if (y.get("share") or 0) > share + 1e-9)
                    break
        if str(price) == "9999999":
            price = ""
        if not price:
            price = row.get("sp") or ""
        if got is not None:
            if got.get("state") != "rated":
                verdict = "none"
            elif got.get("hit"):
                verdict = "hit"
            elif got.get("placed") is True:
                verdict = "near"
            else:
                verdict = "miss"
        elif row.get("hit"):
            verdict = "hit"
        elif row.get("placed") is True:
            verdict = "near"
        elif row.get("placed") is False:
            verdict = "miss"
        elif pick:
            verdict = "miss"
        else:
            verdict = "none"
        out.append({
            "c": course, "t": time, "w": winner, "sp": price,
            "r": rank, "n": field,
            "m": market if market not in ("", 0) else None,
            "p": pick, "pf": finish, "v": verdict,
        })
    return [json.dumps(x, ensure_ascii=False) for x in out]


def rebuild_page(html=None):
    """Re-render the Results blocks from stored data, without scraping.

    A new re-score, or a price restored from a stored copy, changes
    data/results_rated, and the page has to show it. A full refresh would get
    there by scraping, which can put a post-off card in place of the one the
    pick was made from. This rewrites only the UK, Irish and US ledgers, the
    race-by-race audit and the Model chance audit from what is already in
    data/, then writes index.html and dist/public/index.html.
    """
    import chance_audit
    if html is None:
        with open(INDEX, encoding="utf-8") as f:
            html = f.read()
    blocks = (
        ("RESULTS_SL", results_block_all()),
        ("IRE_RESULTS_SL", results_block_all_ire()),
        ("US_RESULTS_SL", results_block_all_us()),
        ("CAL_SL", chance_block_html(chance_audit.collect_rated())),
    )
    for name, body in blocks:
        if not body:
            continue
        pattern = re.compile(rf"<!-- {name}_START -->.*?<!-- {name}_END -->", re.S)
        if not pattern.search(html):
            raise SystemExit(f"could not find {name} in index.html")
        html = pattern.sub(lambda m: body, html, count=1)
    audit = audit_rows()
    if audit:
        html = replace_block(html, "RESULTS", audit)
    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(html)
    os.makedirs(os.path.join(ROOT, "dist", "public"), exist_ok=True)
    with open(os.path.join(ROOT, "dist", "public", "index.html"),
              "w", encoding="utf-8") as f:
        f.write(html)
    return len(html.encode())


def replace_block(html, name, body):
    pattern = re.compile(rf"  var {name} = \[.*?\n  \];", re.S)
    if not pattern.search(html):
        raise SystemExit(f"could not find {name} in index.html")
    return pattern.sub(lambda m: f"  var {name} = [\n" + ",\n".join(body) + "\n  ];", html, count=1)


def hk_audit_block_html(stats, marker="HK_AUDIT_SL", css=""):
    """What the Hong Kong price archive holds, and what the audit still needs.

    The Hong Kong card carries no prices when the pick is made, so the market
    factor drops and the page says so. This block tells the reader how far the
    evidence for changing that has got: odds are now filed before each off, and
    the audit will not call a verdict on the thinnest of samples.
    """
    import audit_hk as _ahk
    s = stats or {}
    races = int(s.get("races") or 0)
    if not races:
        return (f'<!-- {marker}_START -->\n<p{css}><strong>Hong Kong market:</strong> '
                'HKJC cards are filed before betting opens, so a Hong Kong pick is '
                'scored on the factors the card carries and not on the price. Win '
                'odds are now archived from twenty minutes before each off; none '
                'has been filed yet, so no Hong Kong market factor can be tested.'
                f'</p>\n<!-- {marker}_END -->')
    held = int(s.get("holdout_races") or 0)
    met = int(s.get("holdout_meetings") or 0)
    reached = (held >= _ahk.MIN_HOLDOUT_RACES and met >= _ahk.MIN_HOLDOUT_MEETINGS)
    tail = ("that floor is now behind it, so the audit can call a verdict; the "
            "weights still only change through a pre-registered shadow arm."
            if reached else
            "until that is reached the market factor stays out of the model.")
    return (f'<!-- {marker}_START -->\n<p{css}><strong>Hong Kong market:</strong> '
            'HKJC cards are filed before betting opens, so a Hong Kong pick is '
            'scored on the factors the card carries and not on the price. Win odds '
            f'are now archived before each off: {races} races at '
            f'{int(s.get("meetings") or 0)} meetings, the latest on '
            f'{s.get("last") or "-"}. {held} of those races are held back for the '
            'factor audit, which will not call a verdict below '
            f'{_ahk.MIN_HOLDOUT_RACES} held-back races at '
            f'{_ahk.MIN_HOLDOUT_MEETINGS} meetings; ' + tail + '</p>\n'
            f'<!-- {marker}_END -->')


def market_block_html(res, iso):
    """The market re-check panel that sits at the top of the Results tab."""
    import html as _html
    esc = lambda s: _html.escape(str(s or ""))
    label = dt.date.fromisoformat(iso).strftime("%d %B").lstrip("0")
    if res is None:
        inner = ('<h3>Market re-check</h3><p class="k pending">'
                 'No re-check queued for this build.</p>')
    elif not res.get("ok"):
        inner = ('<h3>Market re-check</h3><p class="k pending">'
                 + esc(res.get("reason", "waiting for prices")) + '</p>')
    else:
        n = len(res["rows"])
        inner = (f'<h3>Market re-check &mdash; {esc(label)}</h3>'
                 f'<p class="k">{n} races re-scored with prices '
                 f'({res["priced"]}/{res["runners"]} runners priced) &middot; '
                 f'<b>{res["kept"]} of {n}</b> picks survived the market, '
                 f'{res["changed"]} changed.</p>')
        moved = [r for r in res["rows"] if r["survives"] is False]
        if moved:
            inner += '<ul>' + ''.join(
                f'<li><b>{esc(r["course"])} {esc(r["time"])}</b>: '
                f'{esc(r["before"])} &rarr; {esc(r["after"])}</li>' for r in moved) + '</ul>'
        else:
            inner += '<p class="k">Every pick held.</p>'
    return ('<!-- MARKET_SL_START -->\n<div class="mktcheck">' + inner
            + '</div>\n<!-- MARKET_SL_END -->')


def chance_block_html(a, iso=None):
    """The Model chance audit: the printed percentage against the record."""
    import html as _html
    esc = lambda s: _html.escape(str(s or ""))
    if not a or not a.get("races"):
        empty = ('No settled race has a card complete on all six inputs yet.'
                 if a and a.get("rated") else
                 'No settled race has a stored chance yet. From now on every '
                 'settled pick records the chance it was shown at.')
        inner = ('<h3>Model chance audit</h3><p class="k pending">'
                 + empty + '</p>')
    else:
        n = a["races"]
        if a["inside"]:
            fit = "The interval contains the stated figure."
        elif a["stated"] < a["lo"]:
            fit = ("The stated figure sits below the interval: the Model "
                   "percentage is reading low.")
        else:
            fit = ("The stated figure sits above the interval: the Model "
                   "percentage is reading high.")
        model = ("six-input re-score" if a.get("rated") else "twelve-factor record")
        sample = ("settled races with a complete card" if a.get("rated")
                  else "settled GB/IRE races")
        lead = ("the re-scored picks carried" if a.get("rated")
                else "the pick was shown at")
        avg = " on average" if a.get("rated") else ""
        inner = (f'<h3>Model chance audit &mdash; the {model} against the record</h3>'
                 f'<p class="k">Across <b>{n}</b> {sample} '
                 f'({esc(a["first"])} to {esc(a["last"])}) {lead} '
                 f'<b>{a["stated"]:.1f}%</b>{avg} and won <b>{a["wins"]}</b> '
                 f'({a["rate"]*100:.1f}%, 95% CI {a["lo"]:.1f}-{a["hi"]:.1f}%, '
                 f'a half-width of {a["half"]:.1f} points). {fit}</p>')
        field = a.get("field")
        if field:
            band = max(field["bands"], key=lambda b: abs(b["stated"] - b["won"]))
            inner += (f'<p class="k">Whole field, from the frozen cards: '
                      f'<b>{field["runners"]}</b> runners in <b>{field["races"]}</b> '
                      f'races, printed share vs actual win rate within '
                      f'<b>{field["ece"]:.1f}</b> points (ECE); Brier '
                      f'{field["brier"]:.4f}. The widest band is '
                      f'{band["label"]}: stated {band["stated"]:.1f}%, won '
                      f'{band["won"]:.1f}% (95% CI {band["lo"]:.1f}-{band["hi"]:.1f}%).</p>')
            band_rows = ''.join(
                f'<tr><td>{esc(b["label"])}</td>'
                f'<td class="num">{b["runners"]}</td>'
                f'<td class="num">{b["won"]:.1f}%</td>'
                f'<td class="num">{b["lo"]:.1f}-{b["hi"]:.1f}%</td>'
                f'<td class="num">{b["stated"]:.1f}%</td></tr>'
                for b in field.get("bands", []))
            if band_rows:
                inner += ('<table class="abt"><thead><tr><th>Printed</th>'
                          '<th>Runners</th><th>Won</th><th>95% CI</th>'
                          '<th>Stated</th></tr></thead><tbody>'
                          + band_rows + '</tbody></table>')
        if a.get("more_early"):
            inner += (f'<p class="k">Not yet informative: needs '
                      f'<b>{a["more_early"]}</b> more settled races. An interval '
                      f'this wide can catch a gross error, but it cannot choose '
                      f'between the 0.04 and 0.0498 scales, whose pick chances '
                      f'differ by about four points.</p>')
        elif a.get("more_decisive"):
            inner += (f'<p class="k">Informative but not decisive: needs '
                      f'<b>{a["more_decisive"]}</b> more settled races before the '
                      f'interval is about two points wide and the 0.04 and 0.0498 '
                      f'scales can be told apart.</p>')
        else:
            inner += ('<p class="k">At this sample the interval is narrow '
                      'enough to test the scale it was fitted on.</p>')
    return ('<!-- CAL_SL_START -->\n<div class="mktcheck calc">' + inner
            + '</div>\n<!-- CAL_SL_END -->')


def ab_block_html(t, iso, title="Rating A/B", subject="rating",
                  start="AB_SL_START", end="AB_SL_END"):
    """A with/without-signal panel for a fixed model comparison."""
    import html as _html
    esc = lambda s: _html.escape(str(s or ""))
    if not t or not t.get("races"):
        inner = (f'<h3>{esc(title)}</h3><p class="k pending">'
                 'No settled A/B days yet.</p>')
        if t and t.get("pending"):
            inner += (f'<p class="k">Waiting for results: '
                      f'{esc(", ".join(t["pending"]))}</p>')
    else:
        n = t["races"]
        inner = (f'<h3>{esc(title)} &mdash; with and without a {esc(subject)}</h3>'
                 f'<p class="k">Across <b>{n}</b> settled races: '
                 f'<b>{t["with"]}</b> winners with the {esc(subject)} '
                 f'({100*t["with"]/n:.1f}%) and <b>{t.get("with_placed", 0)}</b> '
                 f'top-three ({100*t.get("with_placed", 0)/n:.1f}%); '
                 f'<b>{t["without"]}</b> winners without '
                 f'({100*t["without"]/n:.1f}%) and <b>{t.get("without_placed", 0)}</b> '
                 f'top-three ({100*t.get("without_placed", 0)/n:.1f}%). The '
                 f'{esc(subject)} changed the pick in {t["changed"]} races '
                 f'(won {t["changed_won"]}, lost {t["changed_lost"]}).</p>')
        if t.get("bench_races"):
            b = t["bench_races"]
            inner += (f'<p class="k">Benchmark on the same races: the market '
                      f'favourite won <b>{t["fav_wins"]} of {b}</b> '
                      f'({100*t["fav_wins"]/b:.1f}%), our pick won '
                      f'<b>{t["pick_hits"]} of {b}</b> '
                      f'({100*t["pick_hits"]/b:.1f}%); we backed the favourite '
                      f'in {t["pick_fav"]}.</p>')
        else:
            inner += ('<p class="k">Benchmark: no pre-race prices were archived '
                      'for these races.</p>')
        if t.get("fair_races"):
            f = t["fair_races"]
            b = t.get("fair_backfilled", 0)
            coverage = f"<b>{f}</b> matched races"
            if b:
                coverage += (f" ({f-b} live Overlay snapshots + "
                             f"{b} archived-card backfills)")
            inner += (f'<p class="k"><a href="https://theoverlay.co.uk/us-racing.html"'
                      f' target="_blank" rel="noopener">The Overlay</a> fair-chance '
                      f'benchmark on {coverage}: its favourite won '
                      f'<b>{t["fair_fav_wins"]} of {f}</b> '
                      f'({100*t["fair_fav_wins"]/f:.1f}%), our pick won '
                      f'<b>{t["fair_pick_hits"]} of {f}</b> '
                      f'({100*t["fair_pick_hits"]/f:.1f}%); we backed the fair '
                      f'favourite in {t["fair_pick_fav"]}.</p>')
            if t.get("fair_unmatched"):
                inner += (f'<p class="k">No fair-chance match for '
                          f'{t["fair_unmatched"]} settled races.</p>')
        rows = ''.join(
            f'<tr><td>{esc(d["date"])}</td><td class="num">{d["races"]}</td>'
            f'<td class="num">{d["with"]}</td><td class="num">{d.get("with_placed", 0)}</td>'
            f'<td class="num">{d["without"]}</td><td class="num">{d.get("without_placed", 0)}</td>'
            f'<td class="num">{d["changed"]}</td></tr>' for d in t["days"])
        inner += ('<table class="abt"><thead><tr><th>Day</th><th>Races</th>'
                  '<th>With win</th><th>With place</th><th>Without win</th>'
                  '<th>Without place</th><th>Changed</th></tr></thead>'
                  f'<tbody>{rows}</tbody></table>')
        inner += ab_place_chart(t, subject)
        inner += f'<p class="k abverdict">{esc(t["verdict"])}</p>'
        if t.get("pending"):
            inner += (f'<p class="k">Waiting for results: '
                      f'{esc(", ".join(t["pending"]))}</p>')
    return (f'<!-- {start} -->\n<div class="mktcheck abcheck">' + inner
            + f'</div>\n<!-- {end} -->')



def ab_place_chart(t, subject):
    """SVG trend of daily win and top-three place rates for both A/B arms."""
    import html as _html
    days = [d for d in (t.get("days") or []) if d.get("races")]
    if not days:
        return ""
    esc = lambda s: _html.escape(str(s or ""))

    W, H = 640, 225
    left, right, top, bottom = 40, 14, 60, 28
    pw, ph = W - left - right, H - top - bottom
    n = len(days)
    xs = [left + (pw * (i / (n - 1) if n > 1 else 0.5)) for i in range(n)]

    def y(pct):
        return top + (100 - pct) / 100 * ph

    def label(value):
        try:
            d = dt.date.fromisoformat(value)
            return f"{d.day} {d.strftime('%b')}"
        except (TypeError, ValueError):
            return str(value or "")

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Daily win/place rate and 7-day place trend">',
             '<title>Daily win/place rate and 7-day place trend</title>']
    for pct in (0, 25, 50, 75, 100):
        yy = y(pct)
        parts.append(f'<line class="grid" x1="{left}" y1="{yy:.1f}" x2="{W-right}" y2="{yy:.1f}"/>')
        parts.append(f'<text x="{left-6}" y="{yy+3:.1f}" text-anchor="end">{pct}%</text>')

    # Colour identifies the arm; solid/dashed identifies place/win.
    for i, (name, colour) in enumerate((("With", "#14453d"), ("Without", "#a9761c"))):
        lx = left + i * 180
        parts.append(f'<line x1="{lx}" y1="18" x2="{lx+16}" y2="18" stroke="{colour}" stroke-width="2.5"/>')
        parts.append(f'<text x="{lx+22}" y="21">{esc(name)} {esc(subject)}</text>')
    parts.append(f'<line x1="{left}" y1="38" x2="{left+16}" y2="38" stroke="#63756e" stroke-width="2.5"/>')
    parts.append(f'<text x="{left+22}" y="41">Place</text>')
    parts.append(f'<line x1="{left+90}" y1="38" x2="{left+106}" y2="38" stroke="#63756e" stroke-width="2.5" stroke-dasharray="6 4"/>')
    parts.append(f'<text x="{left+112}" y="41">Win</text>')
    parts.append(f'<line x1="{left+170}" y1="38" x2="{left+186}" y2="38" stroke="#63756e" stroke-width="6" opacity=".28"/>')
    parts.append(f'<text x="{left+192}" y="41">7-day place</text>')

    def day_value(value):
        try:
            return dt.date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    for arm, field in (("with", "with_placed"), ("without", "without_placed")):
        pts = []
        for i, d in enumerate(days):
            end = day_value(d["date"])
            if end is None:
                window = days[:i + 1]
            else:
                start = end - dt.timedelta(days=6)
                window = [x for x in days[:i + 1]
                          if (day_value(x["date"]) or end) >= start]
            races = sum(x["races"] for x in window)
            placed = sum(x.get(field, 0) for x in window)
            rate = 100 * placed / races if races else 0
            pts.append((xs[i], y(rate), rate, races, placed))
        if n > 1:
            path = " ".join(f"{x:.1f},{yy:.1f}" for x, yy, _, _, _ in pts)
            parts.append(f'<polyline class="rolling {arm}" points="{path}"/>')

    for arm, colour in (("with", "#14453d"), ("without", "#a9761c")):
        for kind, field in (("place", f"{arm}_placed"), ("win", arm)):
            pts = []
            for x, d in zip(xs, days):
                rate = 100 * d.get(field, 0) / d["races"]
                pts.append((x, y(rate), rate, d))
            if n > 1:
                path = " ".join(f"{x:.1f},{yy:.1f}" for x, yy, _, _ in pts)
                parts.append(f'<polyline class="{arm} {kind}" points="{path}"/>')
            for x, yy, rate, d in pts:
                parts.append(
                    f'<circle class="{arm} {kind}" cx="{x:.1f}" cy="{yy:.1f}" r="4">'
                    f'<title>{arm.capitalize()} {esc(subject)} {kind}: {rate:.1f}% '
                    f'({d.get(field, 0)}/{d["races"]}) on {esc(d["date"])}</title></circle>')

    for x, d in zip(xs, days):
        parts.append(f'<text x="{x:.1f}" y="{H-8}" text-anchor="middle">{esc(label(d["date"]))}</text>')
    parts.append('</svg>')
    note = "One settled day; the 7-day trend starts with the next day." if n == 1 else ""
    return ('<div class="abchart"><p class="k abchartnote">Daily win/place rate and 7-day place trend</p>'
            + "".join(parts) + (f'<p class="k abchartnote">{esc(note)}</p>' if note else '')
            + '</div>')



def us_ab_block_html(t, iso):
    """The US comparison: model with and without the HRN speed figure."""
    return ab_block_html(t, iso, title="US A/B", subject="speed figure",
                         start="US_AB_SL_START", end="US_AB_SL_END")


def us_flags_block_html(t, iso):
    """The read-only US runner-context tab."""
    import us_flags
    return ("<!-- US_FLAGS_SL_START -->\n" + us_flags.block_html(t)
            + "\n<!-- US_FLAGS_SL_END -->")


def us_angles_block_html(t, iso):
    """The read-only all-runner angle scan."""
    import us_angles
    return ("<!-- US_ANGLES_SL_START -->\n" + us_angles.block_html(t)
            + "\n<!-- US_ANGLES_SL_END -->")

def main():
    if "--data-only" in sys.argv[1:]:
        print(f"rebuilt results blocks from stored data; wrote index.html + "
              f"dist ({rebuild_page()} bytes)")
        return 0
    days = 2
    skip_checks = False
    for a in sys.argv[1:]:
        if a.startswith("--days"):
            days = int(a.split("=")[1])
        elif a == "--skip-checks":
            skip_checks = True

    rc = preflight(skip_checks)
    if rc is not None:
        return rc

    today = dt.date.today()
    iso = today.isoformat()

    print(f"refreshing {iso}")
    uk_today = uk_day(iso)

    # if a market-free pass was saved for today, re-score it now that the day's
    # prices are up, and print which picks survived contact with the market
    market_check = None
    ab_result = None
    us_ab_result = None
    us_flags_result = None
    us_angles_result = None
    market_arm_result = None
    test_picks = os.path.join(ROOT, "data", "picks_ab", f"test_{iso}.json")
    test_cards = os.path.join(ROOT, "data", "cards", f"{iso}_two_meetings.json")
    if os.path.exists(test_picks) and os.path.exists(test_cards):
        try:
            import recheck_market
            market_check = recheck_market.run(iso, test_cards, sl_cards=uk_today)
            if not market_check["ok"]:
                print(f"  market check: {market_check['reason']}")
        except Exception as e:
            print(f"  market check: skipped ({e})")
    print(f"  UK today:    {len(uk_today)} races")
    uk_tomorrow = uk_day((today + dt.timedelta(days=1)).isoformat())
    print(f"  UK tomorrow: {len(uk_tomorrow)} races")
    us = us_day(iso)
    print(f"  US today:    {len(us)} races")
    # Live US form, official rating and layoff, archived under *_live keys.
    # The page's serialiser does not emit those keys, so the pick is unchanged;
    # tooling/us_form_day.py scores the shadow arm that uses the form string.
    try:
        import fetch_overlay_us
        fetch_overlay_us.ensure_raw(iso)
        fetch_overlay_us.fetch_angles(iso)
        import us_form
        filled = us_form.enrich_cards(us, iso)
        runners = sum(len(c.get("runners") or []) for c in us)
        print(f"  us form:     {filled} of {runners} runners archived with live form/rating")
    except Exception as e:
        print(f"  us form:     skipped ({e})")
    hk = hk_day(iso)
    try:
        import settle_hk
        attached = settle_hk.attach(hk, iso)
        if attached:
            print(f"  HK results:  {attached} settled races attached")
    except Exception as e:
        print(f"  HK results:  skipped ({e})")
    print(f"  HK today:    {len(hk)} races")

    # France: PMU's programme. Only pre-off races are filed, so a later refresh
    # cannot replace a card the pick was made from with a post-race re-scrape.
    fr, cl = [], []
    try:
        import france
        tomorrow = (today + dt.timedelta(days=1)).isoformat()
        fr = france.fetch_cards(iso) + france.fetch_cards(tomorrow)
        print(f"  FR cards:    {len(fr)} pre-race races (today + tomorrow)")
        cl = (france.fetch_cards(iso, "CL")
              + france.fetch_cards(tomorrow, "CL"))
        print(f"  CL cards:    {len(cl)} pre-race races (today + tomorrow)")
    except Exception as e:
        print(f"  PMU cards:   skipped ({e})")

    try:
        slug_date = today.strftime("%d-%m-%y")
        _, meetings = scrape_results.fetch_day(slug_date)
        if meetings:
            path, races, runners = scrape_results.to_disk(slug_date, meetings)
            print(f"  results:     {races} races / {runners} runners -> {os.path.basename(path)}")
    except Exception as e:
        print(f"  results:     skipped ({e})")

    # yesterday's results: fetch, save, and rebuild the Results panel
    try:
        yday = (today - dt.timedelta(days=1)).isoformat()
        rows = fetch_results_sl.fetch_day(yday) if fetch_results_sl else []
        if rows:
            old_path = os.path.join(ROOT, "data", "results", f"{yday}.json")
            old_rows = []
            if os.path.exists(old_path):
                try:
                    with open(old_path, encoding="utf-8") as f:
                        old_rows = json.load(f)
                    if isinstance(old_rows, dict):
                        old_rows = old_rows.get("races", [])
                except Exception:
                    old_rows = []
            # second results source: every meeting, merged on course + winner
            try:
                import fetch_results_ir
                ir = fetch_results_ir.fetch_day(yday)
                have = {(r.get("course"), r.get("winner")) for r in rows}
                added = 0
                for r in ir:
                    if (r["course"], r["winner"]) in have:
                        continue
                    rows.append({"course": r["course"], "time": r.get("time", ""),
                                 "race": r.get("race", ""), "winner": r["winner"],
                                 "sp": r.get("sp", ""), "places": r.get("places", [])})
                    have.add((r["course"], r["winner"])); added += 1
                print(f"  results ir:  +{added} races from irishracing")
            except Exception as e:
                print(f"  results ir:  skipped ({e})")

            # Racing TV adds Timeform ratings, days and course/trip badges that
            # Sporting Life does not publish; without them Irish days settle blind.
            try:
                import fetch_cards_rtv
                rtv_cards = fetch_cards_rtv.build(yday)
                if rtv_cards:
                    with open(os.path.join(ROOT, "data", "cards", f"{yday}_rtv.json"),
                              "w", encoding="utf-8") as f:
                        json.dump(rtv_cards, f, ensure_ascii=False)
                    print(f"  rtv cards:   {len(rtv_cards)} races for {yday}")
            except Exception as e:
                print(f"  rtv cards:   skipped ({e})")

            kept = carry_picks(old_rows, rows)
            foreign = merge_foreign_results(rows, old_rows)
            os.makedirs(os.path.join(ROOT, "data", "results"), exist_ok=True)
            with open(os.path.join(ROOT, "data", "results", f"{yday}.json"), "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
            print(f"  results:     {len(rows)} races for {yday} -> data/results/{yday}.json")
            if kept:
                print(f"  picks kept:  {kept} settled races kept their original pick")
            if foreign:
                print(f"  results US:  {foreign} US rows kept from the earlier ledger")
            settle_day(yday)
            # Re-score the settled day with the strict model, so the ledger's
            # Rated pick column is current whenever results are refreshed.
            try:
                import rescore_results
                rescore_results.main([yday])
            except Exception as e:
                print(f"  re-score:    skipped ({e})")

            # keep the with/without-rating A/B running: snapshot the day, then
            # fold it (and every earlier day) into the running tally
            try:
                import ab_day, ab_tally
                snap = os.path.join(ROOT, "data", "picks_ab", f"{yday}.json")
                if not os.path.exists(snap):
                    ab_day.main(yday)
                ab_tally.main()
                ab_result = ab_tally.collect()
            except SystemExit:
                pass
            except Exception as e:
                print(f"  ab:          skipped ({e})")

            block = results_block_all() or results_block(yday, rows)
            us_block = results_block_all_us() or results_block_us(yday, rows)
            html_now = open(INDEX, encoding="utf-8").read()
            new = re.sub(r"<!-- RESULTS_SL_START -->.*?<!-- RESULTS_SL_END -->", block,
                         html_now, count=1, flags=re.S)
            new = re.sub(r"<!-- US_RESULTS_SL_START -->.*?<!-- US_RESULTS_SL_END -->", us_block,
                         new, count=1, flags=re.S)
            if new != html_now:
                open(INDEX, "w", encoding="utf-8").write(new)
                print("  results tab: rebuilt")
        else:
            print(f"  results:     no settled races for {yday} (left as-is)")
    except Exception as e:
        print(f"  results:     skipped ({e})")

    au = au_day()
    print(f"  AU card:     {len(au)} races")
    archive(iso, {"GB": uk_today, "GB_tomorrow": uk_tomorrow, "US": us, "HK": hk,
                  "AU": au, "FR": fr, "CL": cl})
    # Take the with/without-rating snapshot before the day settles. Later
    # results refreshes only recalculate the tally; they never rewrite it.
    try:
        import ab_day, ab_tally
        snap = os.path.join(ROOT, "data", "picks_ab", f"{iso}.json")
        if not os.path.exists(snap):
            ab_day.main(iso)
        ab_result = ab_tally.collect()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ab:          snapshot skipped ({e})")

    # Shadow arm: the page's own factor scores re-weighted with the vector fitted
    # on four years of results. It never changes the published pick.
    try:
        import market_arm_day
        import ab_tally as _ab_tally
        market_arm_day.main(iso)
        market_arm_result = _ab_tally.collect(snap_dir=market_arm_day.OUT_DIR,
                                              subject=market_arm_day.SUBJECT)
        if market_arm_result["races"]:
            print(f"  market arm:  {market_arm_result['races']} races, "
                  f"{market_arm_result['with']} market-heavy vs "
                  f"{market_arm_result['without']} page, "
                  f"{market_arm_result['changed']} picks changed")
    except SystemExit:
        pass
    except Exception as e:
        print(f"  market arm:  snapshot skipped ({e})")

    try:
        import fetch_overlay_us
        overlay_path = os.path.join(ROOT, "data", "overlay_us", f"{iso}.json")
        if not os.path.exists(overlay_path):
            n = fetch_overlay_us.archive(iso)
            if n:
                print(f"  overlay:     {n} US fair-chance snapshots")
        fetch_overlay_us.ensure_raw(iso)
        fetch_overlay_us.fetch_inform()
        fetch_overlay_us.fetch_angles(iso)
        b = fetch_overlay_us.backfill_from_cards(iso)
        if b:
            print(f"  overlay:     +{b} archived-card backfills")
    except Exception as e:
        print(f"  overlay:     snapshot skipped ({e})")

    try:
        import ab_day, ab_tally
        us_snap = os.path.join(ROOT, "data", "picks_ab_us", f"{iso}.json")
        if not os.path.exists(us_snap):
            ab_day.main(iso, metric="ts", out_dir="picks_ab_us", label="speed figure", regions=("US",))
        us_ab_result = ab_tally.collect(snap_dir="picks_ab_us", regions=("US",),
                                        subject="speed figure", overlay_dir="overlay_us")
    except SystemExit:
        pass
    except Exception as e:
        print(f"  us ab:       snapshot skipped ({e})")
    # Shadow arm for the live form string: same cards, same weights, one extra
    # factor. It never changes the published pick; ab_tally judges it.
    try:
        import us_form_day
        us_form_day.main(iso)
        import ab_tally as _ab
        shadow = _ab.collect(snap_dir="picks_ab_form", regions=("US",),
                             subject=us_form_day.SUBJECT)
        if shadow["races"]:
            print(f"  us form ab:  {shadow['races']} races, {shadow['with']} with vs "
                  f"{shadow['without']} without, {shadow['changed']} picks changed")
        print(f"  us form ab:  {shadow['verdict']}")
    except SystemExit:
        pass
    except Exception as e:
        print(f"  us form ab:  snapshot skipped ({e})")
    try:
        import us_flags
        us_flags_result = us_flags.build(iso)
        print(f"  us flags:    {us_flags_result['total']} picks, {us_flags_result['live_detail']} with live detail")
    except Exception as e:
        print(f"  us flags:    build skipped ({e})")
    try:
        import us_angles
        us_angles_result = us_angles.build(iso)
        print(f"  us angles:   {us_angles_result['total_races']} races, {us_angles_result['runners']} runners")
    except Exception as e:
        print(f"  us angles:   build skipped ({e})")
    try:
        import collect_eachway_prices
        n = collect_eachway_prices.collect_file(
            os.path.join(ROOT, "data", "cards", f"{iso}.json"), iso)
        print(f"  each-way:    {n} new price snapshots")
    except Exception as e:
        print(f"  each-way:    snapshot skipped ({e})")

    # FormFav is a read-only comparison feed. It does not touch cards, scores or
    # picks; without a free key the refresh continues exactly as before.
    try:
        import fetch_formfav
        if fetch_formfav.api_key():
            cards_path = os.path.join(ROOT, "data", "cards", f"{iso}.json")
            for day in (iso, (today + dt.timedelta(days=1)).isoformat()):
                result = fetch_formfav.collect_file(cards_path, day, delay=1.0)
                if result.get("ok"):
                    print(f"  FormFav:     {result['races']} races / "
                          f"{result['runners']} runners compared for {day}")
                else:
                    print(f"  FormFav:     skipped ({result.get('reason')})")
        else:
            print("  FormFav:     skipped (set FORMFAV_API_KEY or .formfav_key)")
    except Exception as e:
        print(f"  FormFav:     skipped ({e})")

    html = open(INDEX, encoding="utf-8").read()
    for name, cards, comment in (
        ("RACE_CARDS", uk_today, f"UK cards, {iso}"),
        ("CARDS_FRI", uk_tomorrow, f"UK cards, {(today + dt.timedelta(days=1)).isoformat()}"),
        ("CARDS_US", us, f"US cards, {iso}"),
        ("CARDS_HK", hk, f"Hong Kong cards, {iso}"),
    ):
        if not cards:
            print(f"  {name}: nothing scraped, left as-is")
            continue
        head, body = serialise(cards, comment)
        html = replace_block(html, name, head + body)

    html = re.sub(r"<!-- MARKET_SL_START -->.*?<!-- MARKET_SL_END -->",
                  market_block_html(market_check, iso), html, count=1, flags=re.S)
    html = re.sub(r"<!-- AB_SL_START -->.*?<!-- AB_SL_END -->",
                  ab_block_html(ab_result, iso), html, count=1, flags=re.S)
    html = re.sub(r"<!-- US_AB_SL_START -->.*?<!-- US_AB_SL_END -->",
                  us_ab_block_html(us_ab_result, iso), html, count=1, flags=re.S)
    html = re.sub(r"<!-- MKT_ARM_SL_START -->.*?<!-- MKT_ARM_SL_END -->",
                  ab_block_html(market_arm_result, iso, title="Market-heavy shadow",
                                subject="market-heavy weighting",
                                start="MKT_ARM_SL_START", end="MKT_ARM_SL_END"),
                  html, count=1, flags=re.S)
    try:
        import chance_audit
        chance_result = chance_audit.collect_rated()
    except Exception as e:
        print(f"  chance audit: skipped ({e})")
        chance_result = None
    html = re.sub(r"<!-- CAL_SL_START -->.*?<!-- CAL_SL_END -->",
                  chance_block_html(chance_result), html, count=1, flags=re.S)
    try:
        import audit_hk
        hk_audit_stats = audit_hk.readiness()
    except Exception as e:
        print(f"  HK audit:    block skipped ({e})")
        hk_audit_stats = None
    html = re.sub(r"<!-- HK_AUDIT_SL_START -->.*?<!-- HK_AUDIT_SL_END -->",
                  hk_audit_block_html(hk_audit_stats), html, count=1, flags=re.S)
    # The same line sits on the Hong Kong tab, where the pick is shown.
    html = re.sub(r"<!-- HK_AUDIT_TAB_START -->.*?<!-- HK_AUDIT_TAB_END -->",
                  hk_audit_block_html(hk_audit_stats, marker="HK_AUDIT_TAB",
                                      css=' class="method"'),
                  html, count=1, flags=re.S)
    us_results = (results_block_all_us()
                  or "<!-- US_RESULTS_SL_START -->\n<p class=\"method\">No US results yet.</p>\n<!-- US_RESULTS_SL_END -->")
    html = re.sub(r"<!-- US_RESULTS_SL_START -->.*?<!-- US_RESULTS_SL_END -->",
                  us_results, html, count=1, flags=re.S)
    ire_results = (results_block_all_ire()
                   or "<!-- IRE_RESULTS_SL_START -->\n"
                      "<p class=\"method\">No Irish results yet.</p>\n"
                      "<!-- IRE_RESULTS_SL_END -->")
    html = re.sub(r"<!-- IRE_RESULTS_SL_START -->.*?<!-- IRE_RESULTS_SL_END -->",
                  ire_results, html, count=1, flags=re.S)
    html = re.sub(r"<!-- US_FLAGS_SL_START -->.*?<!-- US_FLAGS_SL_END -->",
                  us_flags_block_html(us_flags_result, iso), html, count=1, flags=re.S)
    html = re.sub(r"<!-- US_ANGLES_SL_START -->.*?<!-- US_ANGLES_SL_END -->",
                  us_angles_block_html(us_angles_result, iso), html, count=1, flags=re.S)

    audit = audit_rows()
    if audit:
        html = replace_block(html, "RESULTS", audit)

    # The Hong Kong results tab is rebuilt from data/results_hk, so a refresh
    # that only re-scrapes cards keeps the settled ledger current.
    try:
        import settle_hk
        html = settle_hk.replace_results(html)
    except Exception as e:
        print(f"  HK results:  block skipped ({e})")

    # France builds from the pre-race archive (today + tomorrow) and its stored
    # ledger, not from the one fetch this run happened to make.
    try:
        import france
        html = france.replace_cards(html, iso, "FR")
        html = france.replace_results(html, "FR")
        html = france.replace_cards(html, iso, "CL")
        html = france.replace_results(html, "CL")
    except Exception as e:
        print(f"  PMU blocks:  skipped ({e})")

    open(INDEX, "w", encoding="utf-8").write(html)
    os.makedirs(os.path.join(ROOT, "dist", "public"), exist_ok=True)
    open(os.path.join(ROOT, "dist", "public", "index.html"), "w", encoding="utf-8").write(html)
    print(f"  wrote index.html + dist ({len(html.encode())} bytes)")
    print("done — deploy dist/ to publish")

    # last thing on screen: the market survival result, so it cannot be missed
    if market_check:
        import recheck_market as _rm
        if market_check.get("ok"):
            block = _rm.banner(market_check)
        else:
            block = ("=" * 62 + f"\n MARKET RE-CHECK SKIPPED - {iso}\n "
                     f"{market_check.get('reason', 'no reason given')}\n" + "=" * 62)
        print()
        print(block)
        try:
            with open(os.path.join(ROOT, "data", "picks_ab",
                                   f"market_check_{iso}.txt"), "w", encoding="utf-8") as f:
                f.write(block + "\n")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
