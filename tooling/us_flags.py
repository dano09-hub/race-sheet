#!/usr/bin/env python3
"""Read-only US flags for the model's picks.

The Overlay's public US feed is the source for runner context: horse id,
official rating, form, draw, trainer and jockey.  Its inform file supplies the
trainer/jockey 30-day strike rates and the last-run date for each horse id.

Nothing here touches the scoring model.  The pick is read from the existing US
A/B snapshot, and every flag is displayed beside it, never applied to it.

usage: python3 tooling/us_flags.py 2026-11-16
"""
import datetime as dt
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INFORM_PATH = os.path.join(ROOT, "data", "us_inform.json")

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _clean_tokens(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"\(.*?\)", "", text).lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return [t for t in text.split() if t not in _SUFFIXES]


def name_variants(value):
    """Names the two feeds might spell differently."""
    toks = _clean_tokens(value)
    if not toks:
        return []
    full = " ".join(toks)
    no_initials = " ".join(t for t in toks if len(t) > 1)
    out = [full]
    if no_initials and no_initials != full:
        out.append(no_initials)
    if len(toks) > 1:
        out.append(f"{toks[0][0]} {toks[-1]}")
        out.append(toks[-1])
    return list(dict.fromkeys(out))


def _person_index(entries):
    index = {}
    for key, stats in (entries or {}).items():
        for variant in name_variants(key):
            index.setdefault(variant, []).append((key, stats))
    return index


def match_person(entries, value):
    """Return (display name, stats) for a trainer/jockey, or (None, None)."""
    # A literal name beats a looser initial/surname match, even when a second
    # person shares that looser form.
    for key, stats in (entries or {}).items():
        if _norm(key) == _norm(value):
            return key, stats
    index = _person_index(entries)
    for variant in name_variants(value):
        found = index.get(variant) or []
        if len(found) == 1:
            return found[0]
    return None, None


def last_run(form):
    """Describe the first character of a form string."""
    s = str(form or "").strip()
    if not s:
        return ""
    first = s[0].upper()
    return {
        "1": "Won",
        "2": "2nd",
        "3": "3rd",
        "0": "Unplaced",
        "P": "Pulled up",
        "F": "Fell",
        "U": "Unseated",
        "R": "Refused",
        "D": "Disqualified",
    }.get(first, "Unplaced" if first.isdigit() else "")


def rating_rank(runners, horse):
    """Return a runner's official-rating rank, e.g. '1st of 8'."""
    ratings = []
    for r in runners or []:
        try:
            rating = int(float(r.get("or") or 0))
        except (TypeError, ValueError):
            rating = 0
        if rating > 0:
            ratings.append((r.get("horse") or "", rating))
    target = next((rating for name, rating in ratings
                   if _norm(name) == _norm(horse)), None)
    if target is None or len(ratings) < 2:
        return ""
    rank = 1 + sum(1 for _, rating in ratings if rating > target)
    suffix = "th" if 10 <= rank % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix} of {len(ratings)}"


def days_since(last, date):
    """Whole days between an ISO last-run date and the card date."""
    try:
        end = dt.date.fromisoformat(str(date)[:10])
        start = dt.date.fromisoformat(str(last)[:10])
    except (TypeError, ValueError):
        return None
    return (end - start).days


def _norm(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "",
                                      html.unescape(str(value or "")).lower())).strip()


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _card_index(cards):
    out = {}
    for card in cards or []:
        out.setdefault((_norm(card.get("course")), str(card.get("time") or "")[:5]), []).append(card)
    return out


def _find_card(index, row, pick):
    candidates = index.get((_norm(row.get("course")), str(row.get("time") or "")[:5])) or []
    for card in candidates:
        if any(_norm(r.get("horse")) == _norm(pick) for r in card.get("runners") or []):
            return card
    return candidates[0] if candidates else None


def _find_raw(index, row, pick):
    candidates = index.get((_norm(row.get("course")), str(row.get("time") or "")[:5])) or []
    for race in candidates:
        if any(_norm(r.get("horse")) == _norm(pick) for r in race.get("runners") or []):
            return race
    return candidates[0] if candidates else None


def _find_runner(race, pick):
    for runner in (race or {}).get("runners") or []:
        if _norm(runner.get("horse")) == _norm(pick):
            return runner
    return None


def _stat_cell(stats):
    if not stats:
        return None
    return {"sr": stats.get("sr30"), "runs": stats.get("r30")}


def build(date, cards_path=None, picks_path=None, raw_path=None, inform_path=None):
    """Build the data behind the read-only US Flags tab."""
    cards_path = cards_path or os.path.join(ROOT, "data", "cards", "by_date", f"{date}.json")
    picks_path = picks_path or os.path.join(ROOT, "data", "picks_ab_us", f"{date}.json")
    raw_path = raw_path or os.path.join(ROOT, "data", "us_data", f"{date}.json")
    inform_path = inform_path or INFORM_PATH

    card_data = _load(cards_path, {})
    cards = card_data.get("US") if isinstance(card_data, dict) else card_data
    picks_data = _load(picks_path, {})
    picks = picks_data.get("rows") if isinstance(picks_data, dict) else picks_data
    raw = _load(raw_path, {})
    raw_races = []
    for meeting in raw.get("meetings") or []:
        for race in meeting.get("races") or []:
            race = dict(race)
            race["course"] = meeting.get("course") or race.get("course") or ""
            raw_races.append(race)
    inform = _load(inform_path, {})
    trainers = inform.get("trainers") or {}
    jockeys = inform.get("jockeys") or {}
    horses = inform.get("horses") or {}

    raw_index = {}
    for race in raw_races:
        raw_index.setdefault((_norm(race.get("course")), str(race.get("time") or "")[:5]), []).append(race)
    card_index = _card_index(cards)

    rows = []
    for pick_row in picks or []:
        pick = pick_row.get("withRating") or pick_row.get("pick") or ""
        if not pick:
            continue
        card = _find_card(card_index, pick_row, pick)
        raw_race = _find_raw(raw_index, pick_row, pick)
        runner = _find_runner(raw_race, pick)
        card_runner = _find_runner(card, pick)
        trainer = (runner or {}).get("trainer") or (card_runner or {}).get("trainer") or ""
        jockey = (runner or {}).get("jockey") or (card_runner or {}).get("jockey") or ""
        form = (runner or {}).get("form") or (card_runner or {}).get("form") or ""
        draw = (runner or {}).get("draw")
        if draw in (None, ""):
            draw = (card_runner or {}).get("draw")
        or_value = (runner or {}).get("or")
        if or_value in (None, ""):
            or_value = (card_runner or {}).get("or")
        horse_id = str((runner or {}).get("horse_id") or "")
        last = (horses.get(horse_id) or {}).get("last") if horse_id else None
        tname, tstats = match_person(trainers, trainer)
        jname, jstats = match_person(jockeys, jockey)
        rows.append({
            "course": pick_row.get("course") or (card or {}).get("course") or "",
            "time": pick_row.get("time") or (card or {}).get("time") or "",
            "race": (card or {}).get("race") or "",
            "pick": pick,
            "trainer": trainer,
            "trainer_match": tname or "",
            "trainer_form": _stat_cell(tstats),
            "jockey": jockey,
            "jockey_match": jname or "",
            "jockey_form": _stat_cell(jstats),
            "form": form,
            "last_run": last_run(form),
            "or": int(float(or_value)) if str(or_value or "").strip().isdigit() else None,
            "rank": rating_rank((raw_race or card or {}).get("runners") or [], pick),
            "draw": draw,
            "last": last,
            "days": days_since(last, date),
            "live_detail": bool(raw_race and runner),
        })

    return {
        "date": date,
        "total": len(rows),
        "live_detail": sum(1 for r in rows if r["live_detail"]),
        "trainer_matched": sum(1 for r in rows if r["trainer_form"]),
        "jockey_matched": sum(1 for r in rows if r["jockey_form"]),
        "inform_updated": inform.get("updated") or "",
        "inform_note": inform.get("note") or "",
        "raw_generated": raw.get("generated") or "",
        "rows": rows,
    }


def block_html(data):
    """Render the read-only tab. Kept here so refresh/settle can share it."""
    esc = lambda s: html.escape(str(s if s is not None else ""))
    if not data or not data.get("rows"):
        return ('<p class="k pending">No US picks to show flags for yet.</p>')

    def person_cell(name, matched, form):
        if not form:
            who = esc(matched or name or "—")
            return (f'<td class="flagmissing"><div>—</div>'
                    f'<div class="flagsub">{who}</div></td>')
        sr, runs = form.get("sr"), form.get("runs")
        if sr is None:
            return '<td class="flagmissing">—</td>'
        label = f"{sr:g}%" if isinstance(sr, (int, float)) else esc(sr)
        title = f"{name or matched or '—'} · 30-day win strike rate"
        return (f'<td class="flagstat" title="{esc(title)}">'
                f'<b>{label}</b> <span class="flagsub">/ {esc(runs if runs is not None else "—")}</span>'
                f'<div class="flagsub">{esc(matched or name)}</div></td>')

    trs = []
    for r in data["rows"]:
        rank = esc(r.get("rank") or "—")
        or_value = f" · OR {r['or']}" if r.get("or") else ""
        days = (f"{r['days']}d" if isinstance(r.get("days"), int) and r["days"] >= 0
                else "—")
        draw = esc(r.get("draw") if r.get("draw") not in (None, "") else "—")
        form = esc(r.get("last_run") or "—")
        live = "" if r.get("live_detail") else ' <span class="flagsub">(card only)</span>'
        trs.append(
            '<tr>'
            f'<td class="num">{esc(r.get("time") or "—")}</td>'
            f'<td><b>{esc(r.get("course") or "—")}</b></td>'
            f'<td class="flagpick">{esc(r.get("pick") or "—")}{live}</td>'
            + person_cell(r.get("trainer"), r.get("trainer_match"), r.get("trainer_form"))
            + person_cell(r.get("jockey"), r.get("jockey_match"), r.get("jockey_form"))
            + f'<td>{form}</td>'
            f'<td class="num" title="Official rating rank{esc(or_value)}">{rank}</td>'
            f'<td class="num">{draw}</td>'
            f'<td class="num" title="{esc(r.get("last") or "no last-run date")}">{days}</td>'
            '</tr>'
        )

    updated = data.get("inform_updated") or ""
    try:
        updated = dt.datetime.fromisoformat(updated.replace("Z", "+00:00")).strftime("%-d %B %Y")
    except (TypeError, ValueError):
        pass
    note = (f"Trainer and jockey: 30-day win strike rate / runs from The Overlay inform file"
            + (f", as of {esc(updated)}" if updated else "")
            + ". Last run is the first character of the form string; days since last run comes "
            "from the horse id in the live US feed. This table is read-only and is not used in "
            "the score or the selection.")
    coverage = (f'<p class="k">Live runner detail for <b>{data["live_detail"]}</b> of '
                f'<b>{data["total"]}</b> US picks; the rest show card-only values. '
                f'Trainer form matched for {data["trainer_matched"]}, jockey form for '
                f'{data["jockey_matched"]}.</p>')
    return (
        '<div class="flagbox">'
        '<h3>US flags — read-only</h3>'
        f'<p class="k">{note}</p>'
        + coverage
        + '<div class="scroll"><table class="flagtable"><thead><tr>'
        '<th>Time</th><th>Course</th><th>Pick</th><th>Trainer 30d</th>'
        '<th>Jockey 30d</th><th>Last run</th><th>OR rank</th><th>Draw</th>'
        '<th>Days</th></tr></thead><tbody>'
        + "".join(trs)
        + '</tbody></table></div>'
        + '<p class="k flaglegend">Trainer/Jockey: strike rate / runs. '
          'OR rank uses the official rating; draw and days are shown only when the '
          'card or live feed supplies them.</p>'
        '</div>'
    )


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    data = build(date)
    print(json.dumps(data, indent=1)[:4000])
