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
            by_key = {(x.get("course"), x.get("time"), x.get("race")): i
                      for i, x in enumerate(bucket)}
            for c in cards:
                key3 = (c.get("course"), c.get("time"), c.get("race"))
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
    for row in rows:
        if row.get("pick"):
            continue
        card = next((c for c in cards
                     if c.get("course") == row.get("course")
                     and any(r.get("horse") == row.get("winner") for r in c.get("runners", []))), None)
        if not card:
            continue
        pick, score = settle.pick_for(card)
        if not pick:
            continue
        row["pick"], row["score"] = pick, score
        row["hit"] = (pick == row.get("winner"))
        row["pick_finish"] = ""
        filled += 1
    if filled:
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
    print(f"  settled:     {filled} picks written for {iso}")
    return filled


def carry_picks(old_rows, new_rows):
    """Keep the original pick when a settled day is scraped again.

    A results refresh must never rewrite history: the pick was made before the
    race, so if the same race appears in the new scrape its stored pick, finish
    and fate are carried over. Only genuinely new races are left to settle.
    """
    by_time, by_winner = {}, {}
    for r in old_rows or []:
        if not r.get("pick"):
            continue
        t = (r.get("time") or "").strip()
        if t:
            by_time[(r.get("course"), t)] = r
        by_winner[(r.get("course"), r.get("winner"))] = r
    kept = 0
    for r in new_rows:
        t = (r.get("time") or "").strip()
        prev = by_time.get((r.get("course"), t)) if t else None
        prev = prev or by_winner.get((r.get("course"), r.get("winner")))
        if not prev:
            continue
        for k in ("pick", "pick_finish", "hit"):
            if k in prev:
                r[k] = prev[k]
        kept += 1
    return kept


#!/usr/bin/env python3
"""Refresh the page in one command: scrape, rebuild, stage for deploy.

    python3 tooling/refresh.py            # today + tomorrow UK, today US, today results
    python3 tooling/refresh.py --days 3   # widen the UK window

Steps
  1. UK racecards  -> RACE_CARDS (today), CARDS_FRI (tomorrow)
  2. US entries    -> CARDS_US (today)
  3. settled results -> data/YYYY-MM-DD.json (feeds the back-test dataset)
  4. rewrite the three JS blocks inside index.html
  5. copy to dist/public/index.html

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
        runners = ",\n".join(
            "        {" + ", ".join(f"{k}:{js(r.get(k, ''))}" for k in RUNNER_KEYS) + "}"
            for r in c["runners"])
        head = (f'      id: {js(c["id"])}, label: {js(c["label"])}, course: {js(c["course"])}, '
                f'time: {js(c["time"])},\n      day: {js(c["day"])}, region: {js(c["region"])},')
        if c.get("iso"):
            head += f'\n      iso: {js(c["iso"])},'
        body.append("    {\n" + head +
                    f'\n      race: {js(c["race"])}, going: {js(c["going"])}, '
                    f'klass: {js(c["klass"])}, note: {js(c.get("note", ""))},\n'
                    "      runners: [\n" + runners + "\n      ]\n    }")
    return out, body


def uk_day(date_iso):
    d = dt.date.fromisoformat(date_iso)
    slug_date = d.strftime("%d-%m-%y")
    index = get(f"https://www.horseracing.net/racecards/{slug_date}")
    slugs = sorted(set(re.findall(rf'href="/([a-z0-9-]+)/{re.escape(slug_date)}"', index)))
    cards = []
    for slug in slugs:
        page = get(f"https://www.horseracing.net/{slug}/{slug_date}")
        if not page:
            continue
        course = slug.replace("-", " ").title()
        for c in parse_course_page(page, course, slug_date):
            title = c["race"]
            c["label"] = (title[:44] + "…") if len(title) > 45 else title
            c["day"] = date_iso
            c["region"] = "GB"
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
    """Sha Tin / Happy Valley card from the HKJC gateway (plain HTTP)."""
    if scrape_hk is None:
        return []
    try:
        payload = scrape_hk.fetch(date_iso.replace("-", "/"))
        return scrape_hk.cards(payload, date_iso)
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


def results_block(iso, rows):
    """Build the Results panel HTML from a day's settled races."""
    import html as _h
    rows = sorted(rows, key=lambda r: (r["course"], r.get("race", "")))
    hits_box, judged_box = [0], [0]
    trs = []
    for r in rows:
        places = " &middot; ".join(_h.escape(p) for p in r.get("places", []))
        t = r.get("race", "")
        m = re.search(r"(\d{1,2}:\d{2})", t)
        pk = r.get("pick", "")
        if pk:
            fin = r.get("pick_finish", "")
            cell = '<td class="pick %s">%s%s%s</td>' % (
                "hit" if r.get("hit") else "miss",
                "\u2713 " if r.get("hit") else "\u2717 ", _h.escape(pk),
                (" (%s)" % _h.escape(fin)) if fin else "")
            if r.get("hit"):
                hits_box[0] += 1
            judged_box[0] += 1
        else:
            cell = '<td class="pick none">card not kept</td>'
        trs.append('<tr data-course="%s" data-time="%s" data-winner="%s">'
                   '<td><b>%s</b></td><td>%s</td><td>%s</td><td class="muted">%s</td>%s</tr>'
                   % (_h.escape(r["course"]), m.group(1) if m else "", _h.escape(r["winner"]),
                      _h.escape(r["course"]), _h.escape(t[:44]), _h.escape(r["winner"]), places, cell))
    label = dt.datetime.strptime(iso, "%Y-%m-%d").strftime("%-d %B %Y")
    return (
        "<!-- RESULTS_SL_START -->\n"
        '<h3>Settled &mdash; %s</h3>\n'
        '<p class="method">%d races settled. %sWinner first, then the '
        'first three home in finishing order.</p>\n'
        '<div class="scroll"><table><thead><tr><th>Course</th><th>Race</th><th>Winner</th>'
        '<th>Places</th><th>Our pick</th></tr></thead><tbody>%s</tbody></table></div>\n'
        "<!-- RESULTS_SL_END -->" % (label, len(rows),
              ("<b>%d of %d</b> winners were our top pick. " % (hits_box[0], judged_box[0])) if judged_box[0] else "",
              "".join(trs))
    )



def results_block_all():
    """Render every archived day in data/results, newest first."""
    import glob
    files = sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json")), reverse=True)
    if not files:
        return None
    parts = []
    for path in files:
        iso = os.path.basename(path)[:-5]
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if not rows:
            continue
        parts.append(results_block(iso, rows).replace("<!-- RESULTS_SL_START -->", "").replace("<!-- RESULTS_SL_END -->", ""))
    if not parts:
        return None
    return ("<!-- RESULTS_SL_START -->\n" + "\n".join(parts) + "\n<!-- RESULTS_SL_END -->")


def replace_block(html, name, body):
    pattern = re.compile(rf"  var {name} = \[.*?\n  \];", re.S)
    if not pattern.search(html):
        raise SystemExit(f"could not find {name} in index.html")
    return pattern.sub(lambda m: f"  var {name} = [\n" + ",\n".join(body) + "\n  ];", html, count=1)


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


def ab_block_html(t, iso):
    """The rating A/B panel that sits beside the market re-check."""
    import html as _html
    esc = lambda s: _html.escape(str(s or ""))
    if not t or not t.get("races"):
        inner = ('<h3>Rating A/B</h3><p class="k pending">'
                 'No settled A/B days yet.</p>')
        if t and t.get("pending"):
            inner += (f'<p class="k">Waiting for results: '
                      f'{esc(", ".join(t["pending"]))}</p>')
    else:
        n = t["races"]
        inner = (f'<h3>Rating A/B &mdash; with and without a rating</h3>'
                 f'<p class="k">Across <b>{n}</b> settled races: '
                 f'<b>{t["with"]}</b> winners with the rating '
                 f'({100*t["with"]/n:.1f}%), <b>{t["without"]}</b> without '
                 f'({100*t["without"]/n:.1f}%). The rating changed the pick in '
                 f'{t["changed"]} races (won {t["changed_won"]}, '
                 f'lost {t["changed_lost"]}).</p>')
        rows = ''.join(
            f'<tr><td>{esc(d["date"])}</td><td class="num">{d["races"]}</td>'
            f'<td class="num">{d["with"]}</td><td class="num">{d["without"]}</td>'
            f'<td class="num">{d["changed"]}</td></tr>' for d in t["days"])
        inner += ('<table class="abt"><thead><tr><th>Day</th><th>Races</th>'
                  '<th>With</th><th>Without</th><th>Changed</th></tr></thead>'
                  f'<tbody>{rows}</tbody></table>')
        inner += f'<p class="k abverdict">{esc(t["verdict"])}</p>'
        if t.get("pending"):
            inner += (f'<p class="k">Waiting for results: '
                      f'{esc(", ".join(t["pending"]))}</p>')
    return ('<!-- AB_SL_START -->\n<div class="mktcheck abcheck">' + inner
            + '</div>\n<!-- AB_SL_END -->')


def main():
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
    hk = hk_day(iso)
    print(f"  HK today:    {len(hk)} races")

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
            os.makedirs(os.path.join(ROOT, "data", "results"), exist_ok=True)
            with open(os.path.join(ROOT, "data", "results", f"{yday}.json"), "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
            print(f"  results:     {len(rows)} races for {yday} -> data/results/{yday}.json")
            if kept:
                print(f"  picks kept:  {kept} settled races kept their original pick")
            settle_day(yday)

            # keep the with/without-rating A/B running: snapshot the day, then
            # fold it (and every earlier day) into the running tally
            try:
                import ab_day, ab_tally
                ab_day.main(yday)
                ab_tally.main()
                ab_result = ab_tally.collect()
            except SystemExit:
                pass
            except Exception as e:
                print(f"  ab:          skipped ({e})")

            block = results_block_all() or results_block(yday, rows)
            html_now = open(INDEX, encoding="utf-8").read()
            new = re.sub(r"<!-- RESULTS_SL_START -->.*?<!-- RESULTS_SL_END -->", block,
                         html_now, count=1, flags=re.S)
            if new != html_now:
                open(INDEX, "w", encoding="utf-8").write(new)
                print("  results tab: rebuilt")
        else:
            print(f"  results:     no settled races for {yday} (left as-is)")
    except Exception as e:
        print(f"  results:     skipped ({e})")

    au = au_day()
    print(f"  AU card:     {len(au)} races")
    archive(iso, {"GB": uk_today, "GB_tomorrow": uk_tomorrow, "US": us, "HK": hk, "AU": au})

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
