#!/usr/bin/env python3
"""What the exchange offered on the model's lay, and how the lays settled.

The Lays tab names the runner the six-input model rates last in every race it
can rate, and prices the liability at the archived *card* price. This module
adds the other half: the lay price Smarkets' public book showed for that same
runner, snapshotted before the off and stored append-only in
`data/smarkets_lays.jsonl`. The page shows the latest snapshot for the build's
day, and settles every snapshot taken before the off against the stored
winner, net of Smarkets' 2% commission on winning lays.

Nothing here picks a runner, invents a price or fills a gap. A race the model
cannot rate is absent; a race Smarkets does not list, or a runner with no
resting bid, is named as a blank rather than given a price. The quotes come
from the anonymous public API, so they are delayed and not executable: the
tab says so, and no stake is taken. "Most profitable" is therefore a
projection until the settled record says otherwise, and the record is read on
liability rather than strike rate -- one 30.0 lay losing undoes thirty
winning ones.

usage:
    python3 tooling/smarkets_lays.py            # print the record
    python3 tooling/smarkets_lays.py --write    # replace the page's blocks
"""
import datetime as dt
import glob
import html
import json
import os
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rescore_results as rr      # noqa: E402
import screen_results             # noqa: E402
import smarkets                   # noqa: E402

STORE = os.path.join(ROOT, "data", "smarkets_lays.jsonl")
RESULTS = os.path.join(ROOT, "data", "results")
LONDON = ZoneInfo("Europe/London")
FEE = 0.02             # Smarkets: 2% of net market winnings
# Smarkets prices run from 1 basis point up, so the floor quotes 10000.0
# decimal: a bid there risks £9,999 to win £1. It is a resting market order,
# not a price the model's lay can be judged at.
FLOOR = 10000.0
MARKERS = ("SMK_SL", "SMK_RESULTS_SL")
SOURCE = "smarkets_public_delayed"


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def rows(store=STORE):
    """Every stored snapshot row, oldest first."""
    if not os.path.exists(store):
        return []
    out = []
    with open(store, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def candidates(date, rated=None):
    """The model's lay in each rated race of `date`, keyed by race_key."""
    cards, scored, _restored = rated if rated is not None else rr.rate_day(date)
    out = {}
    for card in cards:
        got = scored.get(id(card)) or {}
        if not got.get("rated"):
            continue
        lay = screen_results.screen_race(got).get("lay")
        if not lay or not lay.get("horse"):
            continue
        dec = screen_results._dec(lay)
        chance = screen_results._chance(lay)
        if dec is None or chance is None:
            continue
        out[smarkets.race_key(card.get("course"), card.get("time"))] = {
            "course": card.get("course") or "",
            "time": str(card.get("time") or "")[:5],
            "runner": lay.get("horse") or "",
            "model_pct": chance,
            "card_price": dec,
        }
    return out


def snapshot(date=None, rated=None, books=None, store=STORE, now=None):
    """Append the exchange's lay price for the model's lay in each rated race.

    A row is written only where the exchange lists the race and the runner has
    a resting bid. An unchanged book writes nothing a second time, so repeated
    refreshes keep one row per price rather than one per run.
    """
    date = date or dt.datetime.now(LONDON).date().isoformat()
    wanted = candidates(date, rated)
    if not wanted:
        return []
    if books is None:
        books = smarkets.book(date, {k: v["course"] for k, v in wanted.items()})
    taken = now or dt.datetime.now(dt.timezone.utc)
    stamp = taken.isoformat(timespec="seconds")
    last = {}
    for row in rows(store):
        if row.get("date") == date:
            last[smarkets.race_key(row.get("course"), row.get("time"))] = row
    added = []
    for k, cand in sorted(wanted.items()):
        race = books.get(k)
        found = smarkets.runner(race, cand["runner"])
        if not found or not found.get("lay_odds"):
            continue
        row = {"snapshot_utc": stamp, "date": date,
               "course": cand["course"], "time": cand["time"],
               "runner": cand["runner"], "model_pct": cand["model_pct"],
               "card_price": cand["card_price"],
               "lay_odds": round(found["lay_odds"], 2),
               "lay_stake": round(found["lay_stake"] or 0.0, 2),
               "back_odds": (round(found["back_odds"], 2)
                             if found.get("back_odds") else None),
               "market_id": race.get("market_id"), "source": SOURCE,
               "fee": FEE}
        prev = last.get(k)
        if (prev and prev.get("runner") == row["runner"]
                and prev.get("lay_odds") == row["lay_odds"]
                and prev.get("lay_stake") == row["lay_stake"]):
            continue
        added.append(row)
    if added:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        with open(store, "a", encoding="utf-8") as f:
            for row in added:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return added


def floor_price(row):
    """True for a lay quoted at the exchange floor (one basis point)."""
    return (row.get("lay_odds") or 0.0) >= FLOOR - 1e-9


def _floor_note(dropped):
    """The sentence that says how many floor prices were left out."""
    if not dropped:
        return ""
    n = len(dropped)
    return (f'<p class="method">{n} stored price'
            f'{"" if n == 1 else "s"} at the exchange floor (10000.00, one '
            f'basis point) {"was" if n == 1 else "were"} dropped: a bid there '
            'is a resting order, not a lay price.</p>')


def latest(date=None, store=STORE):
    """The newest snapshot per race, for one day (every day when None)."""
    best = {}
    for row in rows(store):
        if date and row.get("date") != date:
            continue
        k = (row.get("date"), smarkets.race_key(row.get("course"), row.get("time")))
        if k not in best or row.get("snapshot_utc", "") >= best[k].get("snapshot_utc", ""):
            best[k] = row
    return sorted(best.values(), key=lambda r: (r.get("date", ""), r.get("time", "")))


def days(store=STORE):
    return sorted({r.get("date") for r in rows(store) if r.get("date")})


def latest_day(store=STORE):
    """The day the Smarkets tab should show: newest snapshot, else today."""
    got = days(store)
    return got[-1] if got else dt.datetime.now(LONDON).date().isoformat()


def _winners(date, results_dir=None):
    """The stored winner per race, keyed the way the cards name it."""
    path = os.path.join(results_dir or RESULTS, date + ".json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    races = data.get("races", []) if isinstance(data, dict) else data
    out = {}
    for row in races or []:
        if row.get("region") == "US":
            continue
        out[smarkets.race_key(row.get("course"), row.get("time"))] = (
            row.get("winner") or "")
    return out


def _pre_off(date, time, stamp):
    """True when a snapshot was taken before the off, so it was a real price."""
    try:
        off = dt.datetime.fromisoformat(f"{date}T{time}:00").replace(tzinfo=LONDON)
        when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return when <= off


def settled(store=STORE, results_dir=None):
    """Every snapshot taken before its off and settled, newest day first."""
    rows_by_race = {}
    for row in rows(store):
        k = (row.get("date"), smarkets.race_key(row.get("course"), row.get("time")))
        rows_by_race.setdefault(k, []).append(row)
    out = []
    pending = []
    for (date, _k), got in rows_by_race.items():
        usable = [r for r in got if not floor_price(r)]
        if not usable:
            continue
        winner = _winners(date, results_dir).get(_k)
        if not winner:
            pending.append(usable[-1])
            continue
        pre = [r for r in usable
               if _pre_off(date, r.get("time"), r.get("snapshot_utc", ""))]
        if not pre:
            pending.append(usable[-1])
            continue
        row = max(pre, key=lambda r: r.get("snapshot_utc", ""))
        liability = (row.get("lay_odds") or 1.0) - 1.0
        won = smarkets.key(row.get("runner")) != smarkets.key(winner)
        net = (1.0 - row.get("fee", FEE)) if won else -liability
        out.append(dict(row, winner=winner, liability=liability,
                        lay_won=won, net=net))
    out.sort(key=lambda r: (r.get("date", ""), r.get("time", "")), reverse=True)
    return out, pending


def summary(rows):
    """The record read on liability, the way the card-price lays are."""
    risked = sum(r["liability"] for r in rows)
    net = sum(r["net"] for r in rows)
    worst = max((r["liability"] for r in rows), default=0.0)
    return {"races": len(rows), "days": len({r.get("date") for r in rows}),
            "won": sum(1 for r in rows if r["lay_won"]),
            "lost": sum(1 for r in rows if not r["lay_won"]),
            "net": net, "risked": risked, "worst": worst,
            "pct": (100.0 * net / risked) if risked else None}


def _edge(row):
    """The model's chance minus the chance the lay price implies, in points."""
    if not row.get("lay_odds"):
        return None
    return (row.get("model_pct") or 0.0) - 100.0 / row["lay_odds"]


def live_block(day=None, store=STORE):
    """The tab's table: the model's lay against the exchange's own book."""
    day = day or latest_day(store)
    got = latest(day, store)
    dropped = [r for r in got if floor_price(r)]
    got = [r for r in got if not floor_price(r)]
    if not got:
        if dropped:
            return (f'<p class="method pending">Every price stored for '
                    f'{_esc(day)} sat at the exchange floor, so there is no '
                    f'usable lay to show.</p>' + _floor_note(dropped))
        return (f'<p class="method pending">No Smarkets snapshot is stored for '
                f'{_esc(day)} yet. The tab fills when a refresh finds a rated '
                f'race the exchange lists and the model\'s lay has a resting '
                f'bid.</p>')
    got.sort(key=lambda r: (r.get("time", ""), r.get("course", "")))
    body = []
    for row in got:
        edge = _edge(row)
        edge_txt = ("&mdash;" if edge is None else "%+.1f" % edge)
        stake = row.get("lay_stake")
        body.append(
            f'<tr><td><b>{_esc(row.get("course"))}</b> '
            f'<span class="rtime">{_esc(row.get("time"))}</span></td>'
            f'<td>{_esc(row.get("runner"))}</td>'
            f'<td class="num">{row.get("model_pct") or 0:.1f}%</td>'
            f'<td class="num">{row.get("card_price") or 0:.2f}</td>'
            f'<td class="num">{row.get("lay_odds") or 0:.2f}</td>'
            f'<td class="num">{"&mdash;" if stake is None else "%.2f" % stake}</td>'
            f'<td class="num">{(row.get("lay_odds") or 1) - 1:.2f}</td>'
            f'<td class="num">{edge_txt}</td></tr>')
    stamp = max((r.get("snapshot_utc") or "") for r in got)
    missing = ""
    try:
        rated = len(candidates(day))
    except Exception:
        rated = 0
    if rated > len(got) + len(dropped):
        n = rated - len(got) - len(dropped)
        missing = (f' {n} other rated race{"" if n == 1 else "s"} on the day '
                   'carried no resting bid for the model\'s lay and are absent '
                   'here rather than priced.')
    return ('<p class="method">In off order, one row per rated race whose '
            'model lay has a resting bid. The edge is the model\'s chance for '
            'the runner minus the chance the lay price implies, in points; a '
            'negative number is the model rating the horse less likely than '
            'the book. <b>This is a projection, not a record</b> &mdash; a thin '
            'pre-off book can quote far from the card, and the liability column '
            'is what taking the price would risk, so one losing lay at a big '
            'price is the whole profile. The results tab is where it is '
            'judged.</p>'
            '<div class="scroll"><table class="ledger"><thead><tr>'
            '<th>Course / off</th><th>Lay</th><th>Model %</th><th>Card</th>'
            '<th>Smarkets lay</th><th>Stake at lay</th><th>Liability (1)</th>'
            '<th>Edge (pts)</th></tr></thead><tbody>'
            + "".join(body) + '</tbody></table></div>'
            f'<p class="method">Smarkets public book, snapshotted '
            f'{_esc(stamp.replace("T", " ")[:16])} UTC on {_esc(day)}; the '
            'anonymous API serves delayed quotes, so the price is a snapshot '
            'and not an executable order. "Stake at lay" is the backer\'s '
            'money resting at the best bid, in pounds; the liability is the '
            'lay odds minus one per unit laid.' + missing + '</p>'
            + _floor_note(dropped))


def results_block(store=STORE, results_dir=None):
    """The settled Smarkets lays, net of commission, day by day."""
    got, pending = settled(store, results_dir)
    if not got:
        return _floor_note([r for r in rows(store) if floor_price(r)]) + (
                '<p class="method pending">No lay snapshot has settled yet. A '
                'row is judged only from a snapshot stored before the off and '
                'a stored winner; nothing is scored in afterwards.</p>'
                + (f'<p class="method">{len(pending)} snapshot'
                   f'{"" if len(pending) == 1 else "s"} waiting for the stored '
                   'result.</p>' if pending else ""))
    s = summary(got)
    pct = ("%+.1f%%" % s["pct"]) if s["pct"] is not None else "&mdash;"
    head = _floor_note([r for r in rows(store) if floor_price(r)]) + (
            f'<p class="method">Across <b>{s["races"]}</b> settled lay'
            f'{"" if s["races"] == 1 else "s"} over <b>{s["days"]}</b> day'
            f'{"" if s["days"] == 1 else "s"} the model\'s last-rated runner '
            f'won <b>{s["won"]}</b> time{"" if s["won"] == 1 else "s"} and '
            f'lost <b>{s["lost"]}</b>, at the exchange lay stored before each '
            f'off and net of {FEE * 100:.0f}% commission on the winning lays. '
            f'<b>{s["risked"]:.1f}</b> units of liability were at risk for a '
            f'net of <b>{s["net"]:+.1f}</b> ({pct} of what was at risk); the '
            f'largest single lay risked <b>{s["worst"]:.1f}</b>. Read that '
            'against the strike rate: one winner at a big price is the whole '
            'record.</p>')
    by_day = {}
    for row in got:
        by_day.setdefault(row.get("date"), []).append(row)
    blocks = []
    for day in sorted(by_day, reverse=True):
        body = []
        for row in by_day[day]:
            stake = row.get("lay_stake")
            body.append(
                f'<tr><td><b>{_esc(row.get("course"))}</b> '
                f'<span class="rtime">{_esc(row.get("time"))}</span></td>'
                f'<td>{_esc(row.get("runner"))}</td>'
                f'<td class="num">{row.get("lay_odds") or 0:.2f}</td>'
                f'<td class="num">{"&mdash;" if stake is None else "%.2f" % stake}</td>'
                f'<td class="num">{row.get("liability") or 0:.2f}</td>'
                f'<td>{_esc(row.get("winner"))}</td>'
                f'<td class="placed {"yes" if row["lay_won"] else "no"}">'
                f'{"won" if row["lay_won"] else "lost"}</td>'
                f'<td class="num">{row["net"]:+.2f}</td></tr>')
        when = dt.date.fromisoformat(day).strftime("%-d %B %Y")
        blocks.append(f'<h3>Settled &mdash; {_esc(when)}</h3>'
                      '<div class="scroll"><table class="ledger"><thead><tr>'
                      '<th>Course / off</th><th>Lay</th><th>Smarkets lay</th>'
                      '<th>Stake at lay</th><th>Liability</th><th>Winner</th>'
                      '<th>Lay</th><th>Net</th></tr></thead><tbody>'
                      + "".join(body) + '</tbody></table></div>')
    return head + "".join(blocks)


def _replace(html_text, name, body):
    start, end = f"<!-- {name}_START -->", f"<!-- {name}_END -->"
    i = html_text.find(start)
    j = html_text.find(end, i + 1)
    if i < 0 or j < 0:
        raise ValueError(f"index.html has no {start} ... {end} pair")
    return html_text[:i] + start + "\n" + body + "\n" + end + html_text[j + len(end):]


def replace_blocks(html_text, day=None, store=STORE, results_dir=None):
    """Rewrite both Smarkets panels from the stored snapshots."""
    live, res = MARKERS
    html_text = _replace(html_text, live, live_block(day, store))
    return _replace(html_text, res, results_block(store, results_dir))


def main(argv):
    if "--write" in argv:
        path = os.path.join(ROOT, "index.html")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new = replace_blocks(text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print("rewrote the Smarkets panels")
        return 0
    got, pending = settled()
    if not got:
        print(f"no settled Smarkets lay yet; {len(pending)} snapshot(s) pending")
        return 0
    s = summary(got)
    print(f"{s['races']} lays over {s['days']} days: {s['won']} won, "
          f"{s['lost']} lost, net {s['net']:+.2f} on {s['risked']:.2f} risked")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
