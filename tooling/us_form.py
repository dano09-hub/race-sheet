#!/usr/bin/env python3
"""Archive the live US form, official rating and layoff for every runner.

The Horse Racing Nation card the page scores carries a price, a speed figure and
a draw, and little else: no form string, no official rating, no last-run date.
The Overlay's public US feeds do carry them for almost every runner, so they are
kept here under ``*_live`` keys. Those keys are not runner keys in the page's
serialiser, so the published pick does not change; tooling/us_form_day.py scores
a shadow arm with the form string in place and the A/B tally decides whether the
signal earns a weight.

usage: python3 tooling/us_form.py 2026-11-17
"""
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _norm(value):
    text = re.sub(r"\(.*?\)", "", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _raw_race_index(date):
    """(course, time) -> the day's live runners, scratches removed."""
    raw = _load(os.path.join(ROOT, "data", "us_data", f"{date}.json"), {})
    out = {}
    for meeting in raw.get("meetings") or []:
        course = _norm(meeting.get("course"))
        for race in meeting.get("races") or []:
            key = (course, str(race.get("time") or "")[:5])
            out[key] = [r for r in race.get("runners") or [] if not r.get("nr")]
    return out


def runner_index(date):
    """(course, time, horse) -> the live runner record for the day."""
    out = {}
    for (course, time), runners in _raw_race_index(date).items():
        for r in runners:
            out[(course, time, _norm(r.get("horse")))] = r
    return out


def last_run_index(date):
    """(course, time, horse) -> ISO date of the latest run, from the angle feed."""
    angles = _load(os.path.join(ROOT, "data", "us_angles", f"{date}.json"), {})
    out = {}
    for race in (angles.get("races") or {}).values():
        course = _norm(race.get("course"))
        time = str(race.get("off") or race.get("time") or "")[:5]
        for runner in (race.get("runners") or {}).values():
            last = runner.get("last") or []
            if last and isinstance(last[0], list) and last[0]:
                horse = runner.get("h") or runner.get("horse")
                out[(course, time, _norm(horse))] = str(last[0][0])[:10]
    return out


def days_since(last, date):
    """Whole days between an ISO last-run date and the card date."""
    try:
        return (dt.date.fromisoformat(str(date)[:10])
                - dt.date.fromisoformat(str(last)[:10])).days
    except (TypeError, ValueError):
        return None


def enrich_cards(cards, date):
    """Attach *_live values to US runners; the scored keys are left alone.

    Returns how many runners received at least one value.
    """
    raw = runner_index(date)
    last = last_run_index(date)
    filled = 0
    for card in cards or []:
        course = _norm(card.get("course"))
        time = str(card.get("time") or "")[:5]
        for runner in card.get("runners") or []:
            key = (course, time, _norm(runner.get("horse")))
            src = raw.get(key) or {}
            form = str(src.get("form") or "").strip()
            if form:
                runner["form_live"] = form
            rating = src.get("or")
            if isinstance(rating, (int, float)) and rating > 0:
                runner["or_live"] = int(rating)
            days = days_since(last.get(key), date)
            if days is not None and days >= 0:
                runner["days_live"] = days
            if form or str(runner.get("or_live") or "").strip() or "days_live" in runner:
                filled += 1
    return filled


def finish_positions(date):
    """(course, time, horse) -> {"horse": display name, "pos": finish}."""
    out = {}
    for (course, time), runners in _raw_race_index(date).items():
        for r in runners:
            pos = r.get("finish_position")
            if isinstance(pos, int) and pos > 0:
                out[(course, time, _norm(r.get("horse")))] = {
                    "horse": r.get("horse") or "", "pos": pos}
    return out


def merge_finish(rows, date, positions=None):
    """Extend the payout table's top four to every starter in the live feed.

    Positions the payout table already holds keep its spelling; the rest are
    added from the live feed, so a pick can be judged wherever it finished.
    """
    pos = finish_positions(date) if positions is None else positions
    if not pos:
        return rows
    for row in rows or []:
        course = _norm(row.get("course"))
        time = str(row.get("time") or "")[:5]
        found = {v["pos"]: v["horse"] for (c, t, _h), v in pos.items()
                 if c == course and t == time}
        if not found:
            continue
        finish = dict(row.get("finish") or {})
        taken = set(finish.values())
        for place, horse in sorted(found.items()):
            if place in taken or not horse:
                continue
            finish[horse] = place
        row["finish"] = finish
        order = sorted(finish.items(), key=lambda kv: kv[1])
        if order:
            row["places"] = [h for h, _ in order[:3]]
        row["field"] = max(int(row.get("field") or 0), max(finish.values()))
    return rows


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    path = os.path.join(ROOT, "data", "cards", "by_date", f"{day}.json")
    data = _load(path, {})
    us = data.get("US") if isinstance(data, dict) else []
    total = sum(len(c.get("runners") or []) for c in us or [])
    filled = enrich_cards(us, day)
    print(f"{day}: {filled} of {total} US runners carry a live value")
    if filled and isinstance(data, dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"wrote {os.path.relpath(path, ROOT)}")
