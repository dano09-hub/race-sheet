#!/usr/bin/env python3
"""France: PMU race cards, forecast dividends and the official arrival.

PMU is the French pari-mutuel operator, and its public programme endpoint is
the one foreign card feed that answers plain HTTP with a complete field: horse,
rider, trainer, the French form string (``musique``), handicap value, weight,
draw and the live forecast dividend. The same endpoint also carries the
definitive finishing order once a race has run, so cards and results come from
one place.

Four rules, the same ones the other regions live by:

* a card is only filed **before** its off, so a stored pick cannot be a
  re-scored post-race card;
* only the declared field is a field: if PMU reports more declared runners than
  it lists, the race is skipped rather than scored short;
* a result is only written when the arrival is definitive and **every** number
  in it maps to a named runner, so a partial feed never invents a winner;
* a pick already on file is never rewritten.

The programme also lists non-French meetings (Happy Valley, Kempton, San
Isidro, Valparaiso, Solvalla). They are not French cards and are not written
here; ``tooling/audit_coverage.py`` records what each of them actually offers.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

import feed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "index.html")
DIST = os.path.join(ROOT, "dist", "public", "index.html")
OUT_DIR = os.path.join(ROOT, "data", "results_fr")
CARDS_BY_DAY = os.path.join(ROOT, "data", "cards", "by_date")

BASE = "https://online.turfinfo.api.pmu.fr/rest/client/61/programme"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}
NO_CONTENT = "__pmu_no_content__"

DISCIPLINES = {
    "ATTELE": "Trot attelé",
    "MONTE": "Trot monté",
    "PLAT": "Plat",
    "HAIES": "Haies",
    "STEEPLE": "Steeple",
    "CROSS": "Cross",
}

# ``musique`` writes one token per run: the finishing position (or a letter for
# a failed run) followed by the discipline. The page reads form through its own
# alphabet, where a bare ``P`` means pulled up -- so a French ``p`` (plat) must
# never reach it. Only the finishing position is kept.
_FR_TOKEN = re.compile(r"(\d{1,2}|[DATRU])([pamhsco])", re.I)
_FR_FAILED = {"D": "D", "A": "P", "T": "F", "R": "R", "U": "U"}


def _get(url):
    """One page, or ``NO_CONTENT`` for a legitimate 204 (no declarations yet)."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return NO_CONTENT
        return ""
    except Exception:
        return ""


def _json(url, label):
    body = feed.get_page(_get, url, label=label)
    if body == NO_CONTENT:
        return {}
    try:
        return json.loads(body)
    except ValueError as e:
        raise feed.FeedError(f"{label}: invalid JSON ({e})") from e


def programme(iso):
    """The day's PMU programme (all meetings, not just French ones)."""
    d = dt.date.fromisoformat(iso)
    return _json(f"{BASE}/{d:%d%m%Y}", f"PMU programme {iso}") or {}


def meetings(prog):
    return ((prog or {}).get("programme") or {}).get("reunions") or []


def country(meeting):
    return str(((meeting.get("pays") or {}).get("libelle")) or "").upper()


def course_name(value):
    """'HIPPODROME DE SAINT-CLOUD' -> 'Saint-Cloud'."""
    s = str(value or "").upper().strip()
    s = re.sub(r"^HIPPODROME\s+(?:DE\s+|D')", "", s)
    s = re.sub(r"\s{2,}[A-Z]{2,3}$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    small = {"de", "du", "des", "d'", "la", "le", "les", "sur"}
    words = []
    for w in s.split(" "):
        low = w.lower()
        if low in small and words:
            words.append(low)
        else:
            words.append("-".join(p.capitalize() for p in low.split("-")))
    return " ".join(words)


def fr_form(value):
    """French ``musique`` -> the page's form alphabet, most recent last."""
    out = []
    for pos, _disc in _FR_TOKEN.findall(str(value or "")):
        if pos.isdigit():
            n = int(pos)
            out.append(str(n) if 1 <= n <= 9 else "0")
        else:
            out.append(_FR_FAILED.get(pos.upper(), "0"))
    return "".join(out[-6:])


def _runners(participants):
    runners = []
    for p in participants or []:
        if str(p.get("statut") or "").upper() != "PARTANT":
            continue
        name = str(p.get("nom") or "").strip()
        if not name:
            continue
        odds = (p.get("dernierRapportDirect") or {}).get("rapport")
        value = p.get("handicapValeur")
        poids = p.get("handicapPoids")
        rider = p.get("jockey") or p.get("driver") or ""
        runners.append({
            "horse": name,
            "no": str(p.get("numPmu") or ""),
            "odds": f"{float(odds):.2f}" if odds else "",
            "form": fr_form(p.get("musique")),
            "or": (f"{float(value):g}" if isinstance(value, (int, float)) else ""),
            "rpr": "", "ts": "",
            "weight": (f"{float(poids) / 10:g}"
                       if isinstance(poids, (int, float)) and poids > 0 else ""),
            "days": "",
            "course": "N", "distance": "N", "going": "?",
            "draw": str(p.get("placeCorde") or ""),
            "jp": str(rider), "tp": str(p.get("entraineur") or ""),
            "tips": "", "naps": "",
            "trainer": str(p.get("entraineur") or ""), "jockey": str(rider),
            "scratched": False,
        })
    return runners


def _card(meeting, race, iso):
    course = course_name((meeting.get("hippodrome") or {}).get("libelleLong")
                         or (meeting.get("hippodrome") or {}).get("libelleCourt"))
    num_r = str(race.get("numReunion") or "")
    num_c = str(race.get("numOrdre") or "")
    start = dt.datetime.fromtimestamp(float(race.get("heureDepart")) / 1000,
                                      dt.timezone.utc)
    disc = DISCIPLINES.get(str(race.get("discipline") or "").upper(),
                           str(race.get("discipline") or "").title())
    distance = race.get("distance")
    klass = disc + (f" · {distance}m" if distance else "")
    name = str(race.get("libelle") or f"Race {num_c}").strip()
    return {
        "id": f"fr-{course.lower().replace(' ', '-')}-r{num_r}c{num_c}",
        "label": (name[:44] + "…") if len(name) > 45 else name,
        "course": course,
        "time": start.strftime("%H:%M"),
        "iso": start.strftime("%Y-%m-%dT%H:%M:00Z"),
        "day": iso,
        "region": "FR",
        "race": name,
        "going": "",
        "klass": klass,
        "note": f"{distance}m" if distance else "",
        "race_no": f"R{num_r}C{num_c}",
        "runners": [],
    }


def fetch_cards(iso, now=None):
    """Today's French races that have **not** gone off yet.

    Filtering at fetch time is what stops the afternoon and evening refreshes
    replacing a pre-race card with a post-race one: archive() merges on
    (course, time, race), so a race that is gone from this payload keeps the
    version filed before its off.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    prog = programme(iso)
    cards = []
    for m in meetings(prog):
        if country(m) != "FRANCE":
            continue
        for race in m.get("courses") or []:
            try:
                start = dt.datetime.fromtimestamp(
                    float(race.get("heureDepart")) / 1000, dt.timezone.utc)
            except (TypeError, ValueError):
                continue
            if start <= now:
                continue
            declared = race.get("nombreDeclaresPartants")
            try:
                declared = int(declared)
            except (TypeError, ValueError):
                declared = 0
            if not declared:
                continue
            card = _card(m, race, iso)
            url = f"{BASE}/{dt.date.fromisoformat(iso):%d%m%Y}/" \
                  f"R{race.get('numReunion')}/C{race.get('numOrdre')}/participants"
            payload = _json(url, f"PMU {card['course']} {card['race_no']}")
            listed = payload.get("participants") or []
            runners = _runners(listed)
            # ``nombreDeclaresPartants`` counts declared runners including any
            # that have come out, so completeness is the size of the listed
            # field, not the size of the starting field.
            if len(listed) != declared or len(runners) < 2:
                # A short field is not a field. Skipping keeps the race off the
                # page rather than scoring the handful of rows that parsed.
                print(f"  FR card:    {card['course']} {card['race_no']}: "
                      f"{len(listed)} of {declared} declared runners listed, "
                      f"{len(runners)} to run; skipped", file=sys.stderr)
                continue
            card["runners"] = runners
            cards.append(card)
    return cards


def archived_cards(iso):
    path = os.path.join(CARDS_BY_DAY, f"{iso}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [c for c in (data.get("FR") or []) if isinstance(c, dict)
            and c.get("runners")]


def _definitive(race):
    return (race.get("arriveeDefinitive") is True
            or str(race.get("statut") or "").upper().startswith("ARRIVEE_DEFINITIVE"))


def results_for(iso):
    """The day's French arrivals, named through PMU's declared field."""
    prog = programme(iso)
    rows = []
    for m in meetings(prog):
        if country(m) != "FRANCE":
            continue
        course = course_name((m.get("hippodrome") or {}).get("libelleLong")
                             or (m.get("hippodrome") or {}).get("libelleCourt"))
        for race in m.get("courses") or []:
            if not _definitive(race):
                continue
            order = [x[0] for x in (race.get("ordreArrivee") or []) if x]
            if len(order) < 3:
                continue
            card = _card(m, race, iso)
            url = f"{BASE}/{dt.date.fromisoformat(iso):%d%m%Y}/" \
                  f"R{race.get('numReunion')}/C{race.get('numOrdre')}/participants"
            payload = _json(url, f"PMU result {course} {card['race_no']}")
            by_no = {str(p.get("numPmu")): str(p.get("nom") or "").strip()
                     for p in payload.get("participants") or []
                     if str(p.get("statut") or "").upper() == "PARTANT"}
            names = [by_no.get(str(n), "") for n in order]
            if not names or not all(names):
                # The arrival cannot be named, so it is not settled. A number
                # without a horse is not a result.
                print(f"  FR result:  {course} {card['race_no']}: arrival has "
                      f"an unnamed runner; skipped", file=sys.stderr)
                continue
            rows.append({
                "course": course,
                "time": card["time"],
                "race": card["race"],
                "race_no": card["race_no"],
                "winner": names[0],
                "places": names[:3],
                "order": names,
                "region": "FR",
            })
    return rows


def _existing(iso):
    path = os.path.join(OUT_DIR, f"{iso}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    rows = data.get("races", []) if isinstance(data, dict) else data
    return {(r.get("course"), r.get("time"), r.get("race_no")): r
            for r in rows if isinstance(r, dict)}


def settle(iso):
    """Pair the official arrival with the pick from the pre-race card."""
    import settle as settle_mod

    rows = results_for(iso)
    if not rows:
        print(f"  FR results: no definitive French arrival for {iso}")
        return []

    cards = archived_cards(iso)
    by_race = {}
    for card in cards:
        by_race.setdefault((card.get("course"), card.get("time")), card)

    prev = _existing(iso)
    settled = []
    for row in rows:
        old = prev.get((row.get("course"), row.get("time"), row.get("race_no")))
        if old:
            # The official arrival is immutable, and a pick already on file is
            # never rewritten. Keep both rather than re-deriving them.
            row = {**row, "winner": old.get("winner") or row["winner"],
                   "places": old.get("places") or row["places"],
                   "order": old.get("order") or row["order"],
                   "pick": old.get("pick", ""),
                   "pick_finish": old.get("pick_finish", ""),
                   "hit": bool(old.get("hit")),
                   "chance": old.get("chance")}
        if not row.get("pick"):
            card = by_race.get((row.get("course"), row.get("time")))
            if card:
                scored = settle_mod.scored(card)
                if scored and scored.get("pick"):
                    row["pick"] = scored["pick"]
                    row["chance"] = scored.get("chance")
        if row.get("pick"):
            order = row.get("order") or []
            if row["pick"] in order:
                pos = order.index(row["pick"]) + 1
                row["pick_finish"] = _ordinal(pos)
                row["hit"] = pos == 1
            else:
                # A late non-runner (or a card that never held the pick) is not
                # a loss we can prove; leave the finish blank rather than
                # claiming one.
                row["pick_finish"] = ""
                row["hit"] = False
        settled.append(row)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"{iso}.json"), "w", encoding="utf-8") as f:
        json.dump({"date": iso, "region": "FR", "races": settled}, f,
                  ensure_ascii=False, indent=1)
    wins = sum(1 for r in settled if r.get("hit"))
    judged = sum(1 for r in settled if r.get("pick"))
    print(f"  FR results: {len(settled)} races settled, {wins} of {judged} picks won")
    return settled


def _ordinal(n):
    if n == 1:
        return "1st"
    if n == 2:
        return "2nd"
    if n == 3:
        return "3rd"
    return f"{n}th"


def attach(cards, rows):
    """Put the stored official outcome on each French card for the page."""
    by_race = {(r.get("course"), r.get("time")): r for r in rows or []}
    got = 0
    for card in cards:
        row = by_race.get((card.get("course"), card.get("time")))
        if not row or not row.get("pick"):
            continue
        finish = {name: i + 1 for i, name in enumerate(row.get("order") or [])}
        card["result"] = {
            "winner": row.get("winner", ""),
            "places": row.get("places", []),
            "finish": finish,
            "pick": row.get("pick", ""),
            "pick_finish": row.get("pick_finish", ""),
            "hit": bool(row.get("hit")),
        }
        got += 1
    return got


def _day_results(iso, rows):
    rows = sorted(rows, key=lambda r: (r.get("time", ""), r.get("race_no") or ""))
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
    summary = (f'<b>{wins} of {len(judged)}</b> model picks won.'
               if judged else
               'No pre-race card was on file for this day, so no pick is shown.')
    return (
        '<h3>Settled &mdash; %s</h3>\n'
        '<p class="method">%d French %s settled from PMU&rsquo;s definitive '
        'arrival. %s Winner and the first three '
        'home, with the off time in UTC.</p>\n'
        '<div class="scroll"><table class="ledger"><thead><tr><th>Off (UTC)</th>'
        '<th>Course</th><th>Race</th><th>Winner</th><th>First three</th>'
        '<th>Our pick</th></tr></thead><tbody>%s</tbody></table></div>\n'
        % (label, len(rows), noun, summary, "".join(trs)))


def results_block():
    parts = []
    if os.path.isdir(OUT_DIR):
        for name in sorted(os.listdir(OUT_DIR), reverse=True):
            if not name.endswith(".json"):
                continue
            iso = name[:-5]
            try:
                with open(os.path.join(OUT_DIR, name), encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            rows = data.get("races", []) if isinstance(data, dict) else data
            if isinstance(rows, list) and rows:
                parts.append(_day_results(iso, rows))
    body = ("\n".join(parts) if parts else
            '<p class="method">No French results yet. A race appears here once '
            'PMU publishes its definitive arrival and the archived card can name '
            'every finisher.</p>')
    return ("<!-- FR_RESULTS_SL_START -->\n" + body
            + "\n<!-- FR_RESULTS_SL_END -->")


def replace_results(page):
    return re.sub(r"<!-- FR_RESULTS_SL_START -->.*?<!-- FR_RESULTS_SL_END -->",
                  lambda _m: results_block(), page, count=1, flags=re.S)


def _stored_results(iso):
    try:
        with open(os.path.join(OUT_DIR, f"{iso}.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data.get("races", []) if isinstance(data, dict) else data


def cards_for_page(iso):
    """Every pre-race French card on file for the day and the next one."""
    day = dt.date.fromisoformat(iso)
    cards, seen = [], set()
    for d in (day, day + dt.timedelta(days=1)):
        for card in archived_cards(d.isoformat()):
            key = (card.get("iso") or d.isoformat(), card.get("course"),
                   card.get("time"), card.get("race"))
            if key in seen:
                continue
            seen.add(key)
            cards.append(card)
        attach(cards, _stored_results(d.isoformat()))
    cards.sort(key=lambda c: (c.get("iso") or "", c.get("course") or ""))
    return cards


def replace_cards(page, iso):
    import refresh
    cards = cards_for_page(iso)
    head, body = refresh.serialise(cards, f"France cards, {iso}")
    return refresh.replace_block(page, "CARDS_FR", head + body)


def refresh_page(iso):
    """Rebuild CARDS_FR (today + tomorrow) and the French ledger, then write."""
    if not os.path.exists(PAGE):
        return 0
    page = open(PAGE, encoding="utf-8").read()
    page = replace_cards(page, iso)
    page = replace_results(page)
    open(PAGE, "w", encoding="utf-8").write(page)
    if os.path.exists(os.path.dirname(DIST)):
        open(DIST, "w", encoding="utf-8").write(page)
    return len(cards_for_page(iso))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    iso = argv[0] if argv else dt.date.today().isoformat()
    try:
        rows = settle(iso)
    except feed.FeedError as e:
        print(f"France: FAILED ({e})")
        print(f"::error::French results feed unavailable for {iso}: {e}")
        return 1
    try:
        n = refresh_page(iso)
    except feed.FeedError as e:
        print(f"France: FAILED ({e})")
        print(f"::error::French card feed unavailable for {iso}: {e}")
        return 1
    print(f"  FR page:    {n} cards (today + tomorrow)")
    if not rows:
        print(f"France: no definitive arrival for {iso}; page left as built")
    return 0


if __name__ == "__main__":
    sys.exit(main())
