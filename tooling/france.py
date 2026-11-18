#!/usr/bin/env python3
"""PMU race cards and definitive arrivals for France and Chile.

PMU is the French pari-mutuel operator. Its public programme endpoint is the one
foreign card feed that answers plain HTTP with a complete field: horse, rider,
trainer, the French form string (``musique``), handicap value, weight, draw and,
for French meetings, the live forecast dividend. The same endpoint carries the
definitive finishing order once a race has run, so cards and results come from
one place.

France and Chile are wired. The programme also lists Happy Valley, Kempton, San
Isidro and Solvalla; those are either covered elsewhere or have no declared
field, and ``tooling/audit_coverage.py`` records what each really offers.

Four rules, the same ones the other regions live by:

* a card is only filed **before** its off, so a stored pick cannot be a
  re-scored post-race card;
* only the declared field is a field: if PMU reports more declared runners than
  it lists, the race is skipped rather than scored short;
* a result is only written when the arrival is definitive and **every** number
  in it maps to a named runner, so a partial feed never invents a winner;
* a pick already on file is never rewritten.

Chile carries no prices, so its pick is scored on the factors the card does
carry and the page says so rather than estimating a price.
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
CARDS_BY_DAY = os.path.join(ROOT, "data", "cards", "by_date")

BASE = "https://online.turfinfo.api.pmu.fr/rest/client/61/programme"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}
NO_CONTENT = "__pmu_no_content__"

REGIONS = {
    "FR": {
        "country": "FRANCE",
        "page": "France",
        "label": "French",
        "tag": "FRA",
        "out": "results_fr",
        "cards": "CARDS_FR",
        "results_sl": "FR_RESULTS_SL",
        "priced": True,
    },
    "CL": {
        "country": "CHILI",
        "page": "Chile",
        "label": "Chilean",
        "tag": "CHI",
        "out": "results_cl",
        "cards": "CARDS_CL",
        "results_sl": "CL_RESULTS_SL",
        "priced": False,
    },
}

OUT_DIRS = {code: os.path.join(ROOT, "data", cfg["out"])
            for code, cfg in REGIONS.items()}

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


def config(region):
    return REGIONS[str(region).upper()]


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
    """The day's PMU programme (all meetings, not just the wired ones)."""
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
    s = re.sub(r"\s+(?:CHILI|ARG|GB|HK|USA|SUEDE|SUÈDE)$", "", s)
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


def _card(meeting, race, iso, region):
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
        "id": f"{region.lower()}-{course.lower().replace(' ', '-')}-r{num_r}c{num_c}",
        "label": (name[:44] + "…") if len(name) > 45 else name,
        "course": course,
        "time": start.strftime("%H:%M"),
        "iso": start.strftime("%Y-%m-%dT%H:%M:00Z"),
        "day": iso,
        "region": region,
        "race": name,
        "going": "",
        "klass": klass,
        "note": f"{distance}m" if distance else "",
        "race_no": f"R{num_r}C{num_c}",
        "runners": [],
    }


def fetch_cards(iso, region="FR", now=None):
    """Today's races for one region that have **not** gone off yet.

    Filtering at fetch time is what stops the afternoon and evening refreshes
    replacing a pre-race card with a post-race one: archive() merges on
    (course, time, race), so a race that is gone from this payload keeps the
    version filed before its off.
    """
    cfg = config(region)
    region = str(region).upper()
    now = now or dt.datetime.now(dt.timezone.utc)
    prog = programme(iso)
    cards = []
    for m in meetings(prog):
        if country(m) != cfg["country"]:
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
            card = _card(m, race, iso, region)
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
                print(f"  {region} card:    {card['course']} {card['race_no']}: "
                      f"{len(listed)} of {declared} declared runners listed, "
                      f"{len(runners)} to run; skipped", file=sys.stderr)
                continue
            card["runners"] = runners
            cards.append(card)
    return cards


def archived_cards(iso, region="FR"):
    path = os.path.join(CARDS_BY_DAY, f"{iso}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [c for c in (data.get(str(region).upper()) or [])
            if isinstance(c, dict) and c.get("runners")]


def _definitive(race):
    return (race.get("arriveeDefinitive") is True
            or str(race.get("statut") or "").upper().startswith("ARRIVEE_DEFINITIVE"))


def results_for(iso, region="FR"):
    """The day's definitive arrivals for one region, named through its field."""
    cfg = config(region)
    region = str(region).upper()
    prog = programme(iso)
    rows = []
    for m in meetings(prog):
        if country(m) != cfg["country"]:
            continue
        course = course_name((m.get("hippodrome") or {}).get("libelleLong")
                             or (m.get("hippodrome") or {}).get("libelleCourt"))
        for race in m.get("courses") or []:
            if not _definitive(race):
                continue
            order = [x[0] for x in (race.get("ordreArrivee") or []) if x]
            if len(order) < 3:
                continue
            card = _card(m, race, iso, region)
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
                print(f"  {region} result:  {course} {card['race_no']}: arrival has "
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
                "region": region,
            })
    return rows


def _read_doc(iso, region="FR"):
    path = os.path.join(OUT_DIRS[str(region).upper()], f"{iso}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):
        return {"races": data}
    return data if isinstance(data, dict) else {}


def _fingerprint(rows):
    """The parts of a ledger that make it a different ledger."""
    return {(r.get("course"), r.get("time"), r.get("race_no")): (
        r.get("winner"), tuple(r.get("order") or []), r.get("pick"),
        r.get("pick_finish"), bool(r.get("hit")), r.get("chance"))
        for r in rows if isinstance(r, dict)}


def _utcnow():
    return (dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def _existing(iso, region="FR"):
    data = _read_doc(iso, region)
    rows = data.get("races", [])
    return {(r.get("course"), r.get("time"), r.get("race_no")): r
            for r in rows if isinstance(r, dict)}


def settle(iso, region="FR"):
    """Pair the official arrival with the pick from the pre-race card."""
    import settle as settle_mod

    cfg = config(region)
    region = str(region).upper()
    rows = results_for(iso, region)
    if not rows:
        print(f"  {region} results: no definitive {cfg['label']} arrival for {iso}")
        return []

    cards = archived_cards(iso, region)
    by_race = {}
    for card in cards:
        by_race.setdefault((card.get("course"), card.get("time")), card)

    prev_doc = _read_doc(iso, region)
    prev_rows = prev_doc.get("races") or []
    prev = _existing(iso, region)
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

    changed = _fingerprint(settled) != _fingerprint(prev_rows)
    updated = _utcnow() if changed else prev_doc.get("updated")
    doc = {"date": iso, "region": region}
    if updated:
        doc["updated"] = updated
    doc["races"] = settled
    out = OUT_DIRS[region]
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{iso}.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    wins = sum(1 for r in settled if r.get("hit"))
    judged = sum(1 for r in settled if r.get("pick"))
    print(f"  {region} results: {len(settled)} races settled, "
          f"{wins} of {judged} picks won")
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
    """Put the stored official outcome on each card for the page."""
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


def _day_results(iso, rows, region="FR"):
    cfg = config(region)
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
    price_note = ('' if cfg["priced"] else
                  ' PMU does not price this meeting, so the pick is scored '
                  'without the market factor.')
    return (
        '<h3>Settled &mdash; %s</h3>\n'
        '<p class="method">%d %s %s settled from PMU&rsquo;s definitive '
        'arrival. %s%s Winner and the first three '
        'home, with the off time in UTC.</p>\n'
        '<div class="scroll"><table class="ledger"><thead><tr><th>Off (UTC)</th>'
        '<th>Course</th><th>Race</th><th>Winner</th><th>First three</th>'
        '<th>Our pick</th></tr></thead><tbody>%s</tbody></table></div>\n'
        % (label, len(rows), cfg["label"], noun, summary, price_note, "".join(trs)))


def _stamp(region="FR"):
    """When the ledger last changed, or the latest day if it predates this."""
    region = str(region).upper()
    out = OUT_DIRS[region]
    latest_updated, latest_date = "", ""
    if os.path.isdir(out):
        for name in os.listdir(out):
            if not name.endswith(".json"):
                continue
            doc = _read_doc(name[:-5], region)
            if doc.get("updated", "") > latest_updated:
                latest_updated = doc["updated"]
            if doc.get("date", "") > latest_date:
                latest_date = doc["date"]
    ident = "%sLedger" % region.lower()
    if latest_updated:
        try:
            when = dt.datetime.fromisoformat(latest_updated.replace("Z", "+00:00"))
            label = when.strftime("%-d %B %Y, %H:%M UTC")
        except ValueError:
            return ('<p class="method" id="%s">Ledger last changed <b>%s</b>.</p>\n'
                    % (ident, html.escape(latest_updated)))
        return ('<p class="method" id="%s">Ledger last changed '
                '<time datetime="%s">%s</time>.</p>\n'
                % (ident, html.escape(latest_updated), label))
    if latest_date:
        try:
            label = dt.datetime.strptime(latest_date, "%Y-%m-%d").strftime("%-d %B %Y")
        except ValueError:
            return ""
        return ('<p class="method" id="%s">Latest result '
                '<time datetime="%s">%s</time>; the settlement time was not '
                'recorded for rows filed before this change.</p>\n'
                % (ident, html.escape(latest_date), label))
    return ""


def results_block(region="FR"):
    cfg = config(region)
    region = str(region).upper()
    out = OUT_DIRS[region]
    parts = []
    if os.path.isdir(out):
        for name in sorted(os.listdir(out), reverse=True):
            if not name.endswith(".json"):
                continue
            iso = name[:-5]
            try:
                with open(os.path.join(out, name), encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            rows = data.get("races", []) if isinstance(data, dict) else data
            if isinstance(rows, list) and rows:
                parts.append(_day_results(iso, rows, region))
    stamp = _stamp(region)
    body = ((stamp + "\n".join(parts)) if parts else
            (stamp + f'<p class="method">No {cfg["label"]} results yet. A race '
             'appears here once PMU publishes its definitive arrival and the '
             'archived card can name every finisher.</p>'))
    marker = cfg["results_sl"]
    return (f"<!-- {marker}_START -->\n" + body
            + f"\n<!-- {marker}_END -->")


def replace_results(page, region="FR"):
    marker = config(region)["results_sl"]
    return re.sub(rf"<!-- {marker}_START -->.*?<!-- {marker}_END -->",
                  lambda _m: results_block(region), page, count=1, flags=re.S)


def _stored_results(iso, region="FR"):
    path = os.path.join(OUT_DIRS[str(region).upper()], f"{iso}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data.get("races", []) if isinstance(data, dict) else data


def cards_for_page(iso, region="FR"):
    """Every pre-race card on file for the day and the next one."""
    region = str(region).upper()
    day = dt.date.fromisoformat(iso)
    cards, seen = [], set()
    for d in (day, day + dt.timedelta(days=1)):
        for card in archived_cards(d.isoformat(), region):
            key = (card.get("iso") or d.isoformat(), card.get("course"),
                   card.get("time"), card.get("race"))
            if key in seen:
                continue
            seen.add(key)
            cards.append(card)
        attach(cards, _stored_results(d.isoformat(), region))
    cards.sort(key=lambda c: (c.get("iso") or "", c.get("course") or ""))
    return cards


def replace_cards(page, iso, region="FR"):
    import refresh
    cfg = config(region)
    cards = cards_for_page(iso, region)
    head, body = refresh.serialise(cards, f"{cfg['page']} cards, {iso}")
    return refresh.replace_block(page, cfg["cards"], head + body)


def refresh_page(iso, region="FR"):
    """Rebuild the region's card block and ledger, then write both copies."""
    if not os.path.exists(PAGE):
        return 0
    page = open(PAGE, encoding="utf-8").read()
    page = replace_cards(page, iso, region)
    page = replace_results(page, region)
    open(PAGE, "w", encoding="utf-8").write(page)
    if os.path.exists(os.path.dirname(DIST)):
        open(DIST, "w", encoding="utf-8").write(page)
    return len(cards_for_page(iso, region))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    region = "FR"
    for a in list(argv):
        if a.startswith("--region="):
            region = a.split("=", 1)[1].upper()
            argv.remove(a)
    iso = argv[0] if argv else dt.date.today().isoformat()
    if region not in REGIONS:
        print(f"unknown PMU region {region}")
        return 2
    try:
        rows = settle(iso, region)
    except feed.FeedError as e:
        print(f"{region}: FAILED ({e})")
        print(f"::error::PMU results feed unavailable for {region} {iso}: {e}")
        return 1
    try:
        n = refresh_page(iso, region)
    except feed.FeedError as e:
        print(f"{region}: FAILED ({e})")
        print(f"::error::PMU card feed unavailable for {region} {iso}: {e}")
        return 1
    print(f"  {region} page:    {n} cards (today + tomorrow)")
    if not rows:
        print(f"{region}: no definitive arrival for {iso}; page left as built")
    return 0


if __name__ == "__main__":
    sys.exit(main())
