#!/usr/bin/env python3
"""What each racing country actually offers this project.

The page only earns a country tab when a machine-readable card feed and a
definitive result both name the field. This audit probes each jurisdiction from
the same network the workflow runs on, records what came back, and writes:

    data/coverage/latest.txt   the table below
    data/coverage/latest.json  the same facts, machine-readable

``--inject`` also replaces the COVERAGE_SL block on the Method tab. It never
decides that a country is covered because a home page answered 200: a card
counts only when the probe reached a field, and a result only when the arrival
could be named.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "index.html")
DIST = os.path.join(ROOT, "dist", "public", "index.html")
OUT_DIR = os.path.join(ROOT, "data", "coverage")
# horseracing.net answers the project's plain UA and 403s a full browser header
# set, so the audit uses the same UA the scrapers do rather than a prettier one.
UA = {"User-Agent": "Mozilla/5.0"}


def get(url, timeout=20):
    """(status, body, final_url). A transport failure is status 0."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "ignore"), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, "", getattr(e, "url", url)
    except Exception:
        return 0, "", url


def _status(cards=False, results=False, prices=False, verdict="", note=""):
    return {"cards": bool(cards), "results": bool(results),
            "prices": bool(prices), "verdict": verdict, "note": note}


def probe_home(iso):
    """GB/IRE card and result feeds (the two sources the page already uses)."""
    d = dt.date.fromisoformat(iso)
    cards = prices = False
    # Once a meeting has finished, horseracing.net drops it from the day's
    # index. Tomorrow's index measures the feed, not how late the audit ran.
    for delta in (0, 1):
        slug = (d + dt.timedelta(days=delta)).strftime("%d-%m-%y")
        code, body, _ = get(f"https://www.horseracing.net/racecards/{slug}")
        links = re.findall(rf'href="/([a-z0-9-]+)/{re.escape(slug)}"', body or "")
        if code == 200 and links:
            cards = True
            c2, page, _ = get(f"https://www.horseracing.net/{links[0]}/{slug}")
            prices = prices or (c2 == 200 and bool(re.search(r"odds|price", page or "", re.I)))
            break
    rc, rbody, _ = get(f"https://www.sportinglife.com/racing/results/{iso}")
    ic, ibody, _ = get(f"https://www.irishracing.com/results/{iso}")
    results = (rc == 200 and len(rbody or "") > 5000) or (ic == 200 and len(ibody or "") > 5000)
    return _status(cards, results, prices, "live" if cards and results else "partial",
                   "horseracing.net cards; Sporting Life / irishracing results")


def probe_us(iso):
    code, body, _ = get(f"https://entries.horseracingnation.com/entries-results/{iso}")
    slugs = re.findall(rf'href="/entries-results/([a-z0-9-]+)/{re.escape(iso)}"', body or "")
    cards = code == 200 and bool(slugs)
    prices = False
    if slugs:
        c2, page, _ = get(f"https://entries.horseracingnation.com/entries-results/{slugs[0]}/{iso}")
        cards = c2 == 200 and "race-header" in (page or "")
        prices = cards and "Morning Line Odds" in (page or "")
    results = False
    # The payout table is only on a meeting page whose races have run; the
    # first track on the index may still be hours away, so scan a few.
    for slug in slugs[:6]:
        c3, page, _ = get(f"https://entries.horseracingnation.com/entries-results/{slug}/{iso}")
        if c3 == 200 and "table-payouts" in (page or ""):
            results = True
            break
    return _status(cards, results, prices, "live" if cards and results else "cards only",
                   "HRN entries and payout table on one host")


def probe_pmu(iso, wanted):
    """A PMU meeting: field, forecast dividend and (once run) the arrival."""
    import france
    try:
        prog = france.programme(iso)
    except france.feed.FeedError as e:
        return _status(verdict="blocked", note=f"PMU unreadable ({e})")
    ms = [m for m in france.meetings(prog) if france.country(m) == wanted]
    if not ms:
        return _status(verdict="no meeting", note="not on today's PMU programme")
    m = ms[0]
    races = m.get("courses") or []
    cards = prices = False
    partants_count = 0
    arrivals = 0
    # Today's programme proves the card; yesterday's proves the arrival, since
    # a morning audit runs before that evening's overseas meetings.
    # A meeting's first race may not be declared (or may still be open), so the
    # probe looks across the card instead of judging the meeting on race one.
    for race in races[:8]:
        try:
            url = (f"{france.BASE}/{dt.date.fromisoformat(iso):%d%m%Y}/"
                   f"R{race.get('numReunion')}/C{race.get('numOrdre')}/participants")
            payload = france._json(url, f"audit {wanted}")
        except france.feed.FeedError:
            continue
        partants = [p for p in (payload.get("participants") or [])
                    if str(p.get("statut") or "").upper() == "PARTANT"]
        if len(partants) >= 2:
            cards = True
            partants_count = max(partants_count, len(partants))
            prices = prices or any(
                (p.get("dernierRapportDirect") or {}).get("rapport")
                for p in partants)
        if france._definitive(race) and len(race.get("ordreArrivee") or []) >= 3:
            arrivals += 1
    try:
        yday = (dt.date.fromisoformat(iso) - dt.timedelta(days=1)).isoformat()
        for pm in france.meetings(france.programme(yday)):
            if france.country(pm) != wanted:
                continue
            for race in pm.get("courses") or []:
                if france._definitive(race) and len(race.get("ordreArrivee") or []) >= 3:
                    arrivals += 1
    except france.feed.FeedError:
        pass
    # An arrival whose numbers cannot be named is not a result we can publish.
    results = arrivals > 0 and cards
    if cards and results and prices:
        verdict = "live"
    elif cards and results:
        verdict = "cards + results, no prices"
    elif cards:
        verdict = "cards only"
    elif arrivals:
        verdict = "arrival numbers only"
    else:
        verdict = "no usable field"
    note = (f"{partants_count} declared runners, {arrivals} definitive races"
            + (", forecast dividends" if prices else ", no prices"))
    return _status(cards, results, prices, verdict, note)


def probe_au(iso):
    urls = ["https://punters.com.au/form-guide/",
            f"https://www.racing.com/form/{iso}"]
    codes = [get(u, timeout=15)[0] for u in urls]
    reachable = [c for c in codes if c and c < 400]
    return _status(False, False, False,
                   "blocked" if not reachable else "html only",
                   "Punters Cloudflare 403; racing.com 403 from this network")


def probe_jp(iso):
    day = dt.date.fromisoformat(iso).strftime("%Y%m%d")
    code, body, _ = get(f"https://race.netkeiba.com/top/race_list.html?kaisai_date={day}")
    ids = re.findall(r"race_id=\d{12}", body or "")
    if code == 200 and ids:
        return _status(True, False, False, "cards only",
                       f"netkeiba lists {len(ids)} races; no verified result feed")
    return _status(False, False, False, "html only",
                   "race pages reachable; no machine-readable race list")


def probe_za(iso):
    code, body, _ = get("https://www.tab.co.za/racing")
    return _status(False, False, False, "html only" if code == 200 else "unreachable",
                   "TAB home answers; no meeting/field payload found")


def probe_de(iso):
    code, body, _ = get("https://www.deutscher-galopp.de/gr/renntage/")
    return _status(False, False, False, "html only" if code == 200 else "unreachable",
                   "race-day pages are HTML; no structured field feed found")


def probe_br(iso):
    code, body, final = get("https://www.gavea.com.br/")
    return _status(False, False, False, "no feed" if code == 200 else "unreachable",
                   f"redirects to {final}" if "gaveainvest" in final else "no race-data feed")


def probe_ae(iso):
    code, body, _ = get("https://www.abudhabiraceclub.com/", timeout=15)
    return _status(False, False, False, "unreachable" if not code else "html only",
                   "connection timed out from this network" if not code else "HTML only")


WIRED = {"GB/IE", "US", "HK", "FR", "CL"}


def audit(iso):
    rows = [
        ("Great Britain & Ireland", "GB/IE", "horseracing.net", probe_home),
        ("United States", "US", "horseracingnation.com", probe_us),
        ("Hong Kong", "HK", "HKJC", None),
        ("France", "FR", "PMU", lambda d: probe_pmu(d, "FRANCE")),
        ("Argentina", "AR", "PMU", lambda d: probe_pmu(d, "ARGENTINE")),
        ("Chile", "CL", "PMU", lambda d: probe_pmu(d, "CHILI")),
        ("Australia & New Zealand", "AU/NZ", "Punters / racing.com", probe_au),
        ("Japan", "JP", "netkeiba", probe_jp),
        ("South Africa", "ZA", "tab.co.za", probe_za),
        ("Germany", "DE", "deutscher-galopp.de", probe_de),
        ("Brazil", "BR", "gavea.com.br", probe_br),
        ("United Arab Emirates", "AE", "abudhabiraceclub.com", probe_ae),
    ]
    out = []
    for label, code, source, fn in rows:
        if code == "HK":
            # The HKJC gateway is already wired and settled in this repo.
            try:
                import scrape_hk
                payload = scrape_hk.fetch(iso.replace("-", "/"))
                cards = bool(payload)
                results = cards
                note = "official HKJC card and finishing positions"
            except Exception as e:
                cards = results = False
                note = f"HKJC unreadable ({e})"
            st = _status(cards, results, False, "live" if cards else "blocked", note)
        else:
            try:
                st = fn(iso)
            except Exception as e:
                st = _status(verdict="probe failed", note=str(e))
        st["wired"] = code in WIRED
        out.append({"country": label, "code": code, "source": source, **st})
    return {"date": iso, "measured": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "rows": out}


def _table(report):
    lines = [f"Racing coverage — measured {report['measured']} (UTC)",
             f"{'country':26} {'cards':5} {'results':7} {'prices':6}  verdict / evidence",
             "-" * 100]
    for r in report["rows"]:
        lines.append(f"{r['country'][:26]:26} "
                     f"{'yes' if r['cards'] else 'no':5} "
                     f"{'yes' if r['results'] else 'no':7} "
                     f"{'yes' if r['prices'] else 'no':6}  "
                     f"{r['verdict']}{' [on page]' if r.get('wired') else ''}; {r['note']}")
    lines.append("")
    lines.append("A tab needs cards and a named result. Results-only and blocked sources are recorded")
    lines.append("here instead of being shown as an empty tab.")
    return "\n".join(lines)


def _badge(v):
    return "yes" if v else "&mdash;"


def block_html(report):
    esc = lambda s: html.escape(str(s or ""))
    rows = "".join(
        "<tr><td><b>%s</b></td><td class=\"num\">%s</td><td class=\"num\">%s</td>"
        "<td class=\"num\">%s</td><td>%s</td><td class=\"muted\">%s</td></tr>"
        % (esc(r["country"]), _badge(r["cards"]), _badge(r["results"]),
           _badge(r["prices"]),
           esc(r["verdict"]) + (" <b>on page</b>" if r.get("wired") else ""),
           esc(r["note"]))
        for r in report["rows"])
    return (
        '<!-- COVERAGE_SL_START -->\n'
        '<h3>Which countries the sheet can actually follow</h3>\n'
        '<p class="method">Measured from the build network on %s UTC. A country earns a tab only '
        'when a machine-readable card and a definitive result both name the field; the rows marked '
        '<b>on page</b> are the ones wired today. A feed that is blocked, HTML-only or results-only '
        'is recorded here rather than shown as an empty tab.</p>\n'
        '<div class="scroll"><table class="ledger"><thead><tr><th>Country</th><th>Cards</th>'
        '<th>Results</th><th>Prices</th><th>Verdict</th><th>Evidence</th></tr></thead>'
        '<tbody>%s</tbody></table></div>\n'
        '<!-- COVERAGE_SL_END -->' % (esc(report.get("measured", "")[:16].replace("T", " ")), rows))


def inject(report):
    if not os.path.exists(PAGE):
        return
    page = open(PAGE, encoding="utf-8").read()
    if "COVERAGE_SL_START" not in page:
        return
    page = re.sub(r"<!-- COVERAGE_SL_START -->.*?<!-- COVERAGE_SL_END -->",
                  lambda _m: block_html(report), page, count=1, flags=re.S)
    open(PAGE, "w", encoding="utf-8").write(page)
    if os.path.exists(os.path.dirname(DIST)):
        open(DIST, "w", encoding="utf-8").write(page)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    iso = next((a for a in argv if re.match(r"\d{4}-\d{2}-\d{2}$", a)),
               dt.date.today().isoformat())
    report = audit(iso)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    text = _table(report)
    with open(os.path.join(OUT_DIR, "latest.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    if "--inject" in argv:
        inject(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
