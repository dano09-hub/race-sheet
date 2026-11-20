#!/usr/bin/env python3
"""Rebuild the betting screens' records from the strict six-input re-score.

The Lays tab and the Longshot, Overhyped and Place banker result panels all
read the same card the UK, Irish and US ledgers print: the re-score written by
tooling/rescore_results.py. A race the strict model cannot rate is left out of
every screen; a gap is never filled to make a row appear.

The rules are fixed in advance and printed on the page:

  Lays           the runner the model rates last, at its archived card price.
  Longshots      7/1 or bigger, in the model's top four, at least a 4% win
                 chance, and a model chance above the price's implied chance.
  Overhyped      the market favourite, where the model ranks it third or worse
                 or rates it five points below the price. A doubt record only:
                 no stake and no P&L.
  Place bankers  the model's top pick, also the market favourite, at least a
                 20% win chance, in a field of five or more.

A longshot, doubt or banker row is written only when the stored result names
the horse, so nothing is scored in after the fact. A lay settles either way:
the winner is on file, so the record says whether laying the model's last-rated
runner would have won, without needing that horse to finish in the places.

Card prices are the archived racecard prices. They are not exchange lay prices
and no liquidity is implied: a liability is the card price minus one per unit
staked, which is what that price would risk, not a quote from a venue.

usage:
    python3 tooling/screen_results.py            # print the tallies
    python3 tooling/screen_results.py --write    # replace the page's blocks
"""
import datetime as dt
import glob
import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rescore_results as rr  # noqa: E402

MIN_LONGSHOT_DEC = 8.0      # 7/1 on the card
LONG_RANK_MAX = 4
LONG_CHANCE_MIN = 4.0
OVER_RANK_MIN = 3
OVER_GAP = 5.0
PLACE_CHANCE_MIN = 20.0
PLACE_FIELD_MIN = 5

SCREENS = ("lays", "longshots", "overhyped", "placers")
MARKERS = {
    "lays": "LAY_RESULTS_SL",
    "longshots": "LONG_RESULTS_SL",
    "overhyped": "OVER_RESULTS_SL",
    "placers": "PLACE_RESULTS_SL",
}


def _chance(runner):
    try:
        return float(runner.get("chance"))
    except (TypeError, ValueError):
        return None


def _dec(runner):
    """The card price as decimal odds, or None when the card carried none."""
    try:
        got = float(runner.get("dec"))
    except (TypeError, ValueError):
        return None
    return got if got > 1 else None


def _share(runner):
    try:
        got = float(runner.get("share"))
    except (TypeError, ValueError):
        return None
    return got if got >= 0 else None


def screen_race(got):
    """The four screens' candidates for one rated race.

    `got` is the scorer's output for a card: runners ordered by rating, best
    first, each carrying its card price, market share and model chance. A
    runner missing either number cannot be screened and is passed over.
    """
    rows = [r for r in (got.get("runners") or []) if isinstance(r, dict)]
    out = {"lay": None, "longshots": [], "overhyped": [], "placers": []}
    if len(rows) < 2:
        return out

    for i, runner in enumerate(rows):
        dec, chance, share = _dec(runner), _chance(runner), _share(runner)
        if dec is None or chance is None or share is None:
            continue
        if (dec >= MIN_LONGSHOT_DEC and i < LONG_RANK_MAX
                and chance >= LONG_CHANCE_MIN and chance > share * 100):
            out["longshots"].append(dict(runner, rank=i + 1))

    fav_i, fav = max(enumerate(rows), key=lambda p: (_share(p[1]) or 0, -p[0]))
    fav_chance, fav_share = _chance(fav), _share(fav)
    if fav_chance is not None and fav_share is not None:
        if fav_i + 1 >= OVER_RANK_MIN or fav_chance <= fav_share * 100 - OVER_GAP:
            out["overhyped"].append(dict(fav, rank=fav_i + 1))

    top = rows[0]
    top_chance = _chance(top)
    if (top.get("horse") and rr.norm(top.get("horse")) == rr.norm(fav.get("horse"))
            and top_chance is not None and top_chance >= PLACE_CHANCE_MIN
            and len(rows) >= PLACE_FIELD_MIN):
        out["placers"].append(dict(top, rank=1))

    if _dec(rows[-1]) is not None:
        out["lay"] = dict(rows[-1], rank=len(rows))
    return out


def _settle(base, runner, row):
    """One screen row, settled against the stored result.

    Every settled race has a winner on file, so every flagged runner is a win
    or a loss; a record that kept only the horses named in the stored places
    would quietly drop every loser. A non-runner is the one row that cannot be
    settled: it voids rather than loses, so it is left out.
    """
    horse = runner.get("horse") or ""
    finish = rr.finish_of(row, horse)
    if rr.norm(finish) in ("nr", "non runner"):
        return None
    terms = rr.place_terms(row)
    pos = rr.position_number(finish)
    return dict(base, horse=horse, price=runner.get("odds") or "",
                dec=_dec(runner), chance=_chance(runner),
                rank=runner.get("rank"),
                finish=finish, won=rr.norm(horse) == rr.norm(row.get("winner")),
                placed=(pos <= terms) if (pos and terms) else None)


def _finish_text(entry):
    """The placing, when the stored result records one.

    Results keep the first three home, so a beaten runner outside them has no
    placing to print. That is shown as a dash, never called unplaced.
    """
    if entry.get("won"):
        return "won"
    finish = entry.get("finish") or ""
    if rr.position_number(finish):
        return _esc(finish)
    return "&mdash;"


def day_entries(date):
    """Every screen row the day's settled races produce."""
    cards, scored, _restored = rr.rate_day(date)
    out = {name: [] for name in SCREENS}
    for region, row in rr.result_rows(date):
        card = rr.pick_card(cards, row)
        got = scored.get(id(card)) if card else None
        if not got or not got.get("rated"):
            continue
        base = {
            "date": date,
            "course": row.get("course") or "",
            "time": str(row.get("time") or "")[:5],
            "race": row.get("race") or "",
            "winner": row.get("winner") or "",
            "region": region or (card or {}).get("region", ""),
        }
        found = screen_race(got)
        for name in ("longshots", "overhyped", "placers"):
            for runner in found[name]:
                entry = _settle(base, runner, row)
                if entry:
                    out[name].append(entry)
        if found["lay"]:
            entry = _settle(base, found["lay"], row)
            if entry:
                out["lays"].append(entry)
    return out


def collect():
    """Every settled day's screen rows, newest day first."""
    everything = {name: [] for name in SCREENS}
    files = sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json")),
                   reverse=True)
    for path in files:
        iso = os.path.basename(path)[:-5]
        got = day_entries(iso)
        for name in SCREENS:
            everything[name].extend(got[name])
    return everything


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def _price(entry):
    if entry.get("price"):
        return _esc(entry["price"])
    dec = entry.get("dec")
    return ("%.2f" % dec) if dec else "&mdash;"


def _pct(value):
    return "%.1f%%" % value if value is not None else "&mdash;"


def _day_label(iso):
    try:
        return dt.datetime.strptime(iso, "%Y-%m-%d").strftime("%-d %B %Y")
    except ValueError:
        return str(iso)


def _by_day(rows):
    order, groups = [], {}
    for row in rows:
        if row["date"] not in groups:
            groups[row["date"]] = []
            order.append(row["date"])
        groups[row["date"]].append(row)
    return [(day, groups[day]) for day in order]


def _table(heads, body):
    return ('<div class="scroll"><table class="ledger"><thead><tr>'
            + "".join("<th>%s</th>" % h for h in heads)
            + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


def _shell(name, summary, tables):
    return ("<!-- %s_START -->\n%s\n%s\n<!-- %s_END -->"
            % (MARKERS[name], summary, "\n".join(tables), MARKERS[name]))


def _off_cell(row):
    tag = ""
    if str(row.get("region") or "").upper() == "US":
        tag = '<span class="rtag">USA</span> '
    elif str(row.get("region") or "").upper() == "IRE":
        tag = '<span class="rtag">IRE</span> '
    time_html = (' <span class="rtime">%s</span>' % _esc(row["time"])
                 if row.get("time") else "")
    return "<td><b>%s%s</b>%s</td>" % (tag, _esc(row.get("course")), time_html)


def _day_head(day):
    return '<h3>Settled &mdash; %s</h3>' % _esc(_day_label(day))


def _cell(value, attr=""):
    return "<td%s>%s</td>" % ((' class="%s"' % attr) if attr else "", value)


def _tr(entry, *cells):
    return "<tr>" + _off_cell(entry) + "".join(cells) + "</tr>"


def _screen_block(name, rows, summary, heads, cells):
    """One panel: its summary, then a table for every settled day.

    The four screens differ only in the sentence above the table and the cells
    they print, so the day grouping, the table shell and the course/off cell
    are written once here.
    """
    tables = [_day_head(day) + _table(heads, [_tr(r, *cells(r)) for r in group])
              for day, group in _by_day(rows)]
    return _shell(name, summary, tables)


def _empty_block(name, note):
    return _shell(name, '<p class="method">%s</p>' % note, [])


def longshot_block(rows):
    if not rows:
        return _empty_block("longshots",
                            "No longshot has been flagged on a rated card yet. A "
                            "screen row needs a card the six-input model can rate "
                            "and a settled result.")
    wins = sum(1 for r in rows if r["won"])
    placed = sum(1 for r in rows if r["placed"])
    days = len({r["date"] for r in rows})
    summary = ('<p class="method">Across <b>%d</b> settled day%s the screen flagged '
               '<b>%d</b> longshot%s: <b>%d</b> won (%.1f%%) and <b>%d</b> were '
               'beaten. The stored result names the first three home, so a beaten '
               'longshot shows a placing only where it is named there and a dash '
               'otherwise; <b>%d</b> were in the frame. Each row would have been '
               'one unit at the archived card price. No stake is taken and no bet '
               'is placed.</p>'
               % (days, "" if days == 1 else "s", len(rows),
                  "" if len(rows) == 1 else "s", wins, 100.0 * wins / len(rows),
                  len(rows) - wins, placed))

    def cells(r):
        verdict = ("<span class=\"pick hit\">won</span>" if r["won"]
                   else _finish_text(r))
        return (_cell(_esc(r["horse"])), _cell(_price(r), "num"),
                _cell("%d" % r["rank"], "num"), _cell(_pct(r["chance"]), "num"),
                _cell(verdict))

    return _screen_block("longshots", rows, summary,
                         ("Course / off", "Longshot", "Card price", "Model rank",
                          "Model %", "Finished"), cells)


def overhyped_block(rows):
    if not rows:
        return _empty_block("overhyped",
                            "No favourite has been flagged on a rated card yet. A "
                            "doubt row needs a settled result.")
    beaten = sum(1 for r in rows if not r["won"])
    placed = sum(1 for r in rows if r["placed"])
    days = len({r["date"] for r in rows})
    summary = ('<p class="method">Across <b>%d</b> settled day%s the screen doubted '
               '<b>%d</b> market favourite%s: <b>%d</b> were beaten (%.1f%%) and '
               '<b>%d</b> still placed. This is a doubt record, not a bet: no stake '
               'and no P&amp;L. &ldquo;Placed&rdquo; uses the terms stored with the '
               'result and includes wins.</p>'
               % (days, "" if days == 1 else "s", len(rows),
                  "" if len(rows) == 1 else "s", beaten,
                  100.0 * beaten / len(rows), placed))

    def cells(r):
        return (_cell(_esc(r["horse"])), _cell(_price(r), "num"),
                _cell("%d" % r["rank"], "num"), _cell(_pct(r["chance"]), "num"),
                _cell(_finish_text(r)),
                _cell("yes" if not r["won"] else "no"))

    return _screen_block("overhyped", rows, summary,
                         ("Course / off", "Favourite", "Card price", "Model rank",
                          "Model %", "Finished", "Doubt held?"), cells)


def placer_block(rows):
    if not rows:
        return _empty_block("placers",
                            "No place banker has been flagged on a rated card yet. "
                            "A banker row needs the model pick to be the market "
                            "favourite and a settled result.")
    placed = sum(1 for r in rows if r["placed"])
    unknown = sum(1 for r in rows if r["placed"] is None and not r["won"])
    days = len({r["date"] for r in rows})
    summary = ('<p class="method">Across <b>%d</b> settled day%s the screen named '
               '<b>%d</b> place banker%s: <b>%d</b> placed under the standard terms '
               'stored with the result, and %d are not named in the places the '
               'result records. The model prices wins, not places, so this is a '
               'settlement record of a place profile, not a place probability. No '
               'stake is taken.</p>'
               % (days, "" if days == 1 else "s", len(rows),
                  "" if len(rows) == 1 else "s", placed, unknown))

    def cells(r):
        if r["placed"] is None:
            placed_cell = _cell("&mdash;", "placed unknown")
        else:
            placed_cell = _cell("Yes" if r["placed"] else "No",
                                "placed yes" if r["placed"] else "placed no")
        return (_cell(_esc(r["horse"])), _cell(_price(r), "num"),
                _cell(_pct(r["chance"]), "num"), _cell(_finish_text(r)),
                placed_cell)

    return _screen_block("placers", rows, summary,
                         ("Course / off", "Banker", "Card price", "Model %",
                          "Finished", "Placed?"), cells)


def lay_money(rows):
    """What the lay record is worth once liability is counted, not just wins.

    A lay wins the backer's stake, one unit, and loses the card price minus
    one. A strike rate on its own hides that: 51 winners at one unit each can
    sit beside a single loser that risks thirty times as much, and the money at
    risk across the record is many times the money won. The net is therefore
    reported against the total liability, with the largest single lay beside it.
    """
    risked = returned = lost = 0.0
    wins = 0
    worst = 0.0
    for row in rows:
        dec = row.get("dec")
        if not dec:
            continue
        liability = dec - 1
        risked += liability
        worst = max(worst, liability)
        if row["won"]:            # the horse won, so the lay lost its liability
            lost += liability
        else:
            wins += 1
            returned += 1.0
    net = returned - lost
    return {"wins": wins, "risked": risked, "returned": returned,
            "lost": lost, "net": net, "worst": worst,
            "pct": (100.0 * net / risked) if risked else None}


def lay_block(rows):
    if not rows:
        return _empty_block("lays",
                            "No rated race has settled yet, so the lay record is "
                            "empty. A lay is only written for a card the six-input "
                            "model can rate.")
    days = len({r["date"] for r in rows})
    money = lay_money(rows)
    won = money["wins"]            # the layer wins when the horse loses
    pct = ("%+.1f%%" % money["pct"]) if money["pct"] is not None else "&mdash;"
    times = (" &mdash; %.1f times the whole net." % (money["worst"] / money["net"])
             if money["net"] > 0 and money["worst"] > 0
             else " &mdash; larger than the whole net." if money["worst"] > abs(money["net"])
             else ".")
    summary = ('<p class="method">Across <b>%d</b> settled day%s the model laid '
               '<b>%d</b> runner%s, its last-rated horse in every race it could rate: '
               '<b>%d</b> lost (%.1f%%) and <b>%d</b> won. Read on liability rather '
               'than strike rate: the winning lays return <b>%.1f</b> units, the '
               'losing lays cost <b>%.1f</b>, and <b>%s</b> units of liability were '
               'at risk at the card prices, so the net of <b>%+.1f</b> units is '
               '<b>%s</b> of what was at risk. The largest single lay risked '
               '<b>%s</b> units%s A strike rate is not a strategy: one winner at '
               'that price undoes the record. Liability is the archived card price '
               'minus one per unit staked; card prices are racecard prices, not '
               'exchange lay prices, and no commission or available money is '
               'modelled, so this is the model&rsquo;s own reckoning, not a venue '
               'P&amp;L. No bet is placed.</p>'
               % (days, "" if days == 1 else "s", len(rows),
                  "" if len(rows) == 1 else "s", won, 100.0 * won / len(rows),
                  len(rows) - won, money["returned"], money["lost"],
                  "{:,.1f}".format(money["risked"]), money["net"], pct,
                  "{:,.1f}".format(money["worst"]), times))

    def cells(r):
        dec = r.get("dec")
        liability = "%.2f" % (dec - 1) if dec else "&mdash;"
        return (_cell(_esc(r["horse"])), _cell(_price(r), "num"),
                _cell(_pct(r["chance"]), "num"), _cell(liability, "num"),
                _cell(_finish_text(r)),
                _cell("lost" if r["won"] else "won",
                      "placed no" if r["won"] else "placed yes"))

    return _screen_block("lays", rows, summary,
                         ("Course / off", "Lay", "Card price", "Model %",
                          "Liability (stake 1)", "Finished", "Lay"), cells)


BUILDERS = {
    "lays": lay_block,
    "longshots": longshot_block,
    "overhyped": overhyped_block,
    "placers": placer_block,
}


def replace_blocks(html_text):
    """Put every screen's current record back into the page."""
    everything = collect()
    out = html_text
    for name in SCREENS:
        marker = MARKERS[name]
        body = BUILDERS[name](everything[name])
        pattern = re.compile(r"<!-- %s_START -->.*?<!-- %s_END -->" % (marker, marker),
                             re.S)
        if not pattern.search(out):
            raise SystemExit("could not find %s in index.html" % marker)
        out = pattern.sub(lambda m, b=body: b, out, count=1)
    return out


def main(argv):
    everything = collect()
    write = "--write" in argv
    for name in SCREENS:
        rows = everything[name]
        print("%-10s %4d rows over %2d days"
              % (name, len(rows), len({r["date"] for r in rows})))
    if write:
        path = os.path.join(ROOT, "index.html")
        with open(path, encoding="utf-8") as f:
            page = f.read()
        page = replace_blocks(page)
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
        os.makedirs(os.path.join(ROOT, "dist", "public"), exist_ok=True)
        with open(os.path.join(ROOT, "dist", "public", "index.html"),
                  "w", encoding="utf-8") as f:
            f.write(page)
        print("wrote index.html + dist/public/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
