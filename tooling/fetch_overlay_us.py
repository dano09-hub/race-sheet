#!/usr/bin/env python3
"""Snapshot The Overlay's US fair-chance favourite for a race day.

The public feed at /data_us.json supplies one price per US runner.  The Overlay
removes the card margin with the power method and calls the result each
runner's fair chance.  We keep only the fair favourite, race key and fair
percentage; this is a benchmark, not a model input.

usage: python3 tooling/fetch_overlay_us.py 2026-11-16
"""
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
URL = "https://theoverlay.co.uk/data_us.json"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/122 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://theoverlay.co.uk/us-racing.html",
}


def decimal_odds(value):
    s = str(value or "").strip().lower()
    if not s or s in ("sp", "-", "f"):
        return None
    if s in ("evs", "evens"):
        return 2.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        try:
            return 1 + float(m.group(1)) / float(m.group(2))
        except (ValueError, ZeroDivisionError):
            return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 1 else None


def devig_power(decs, max_iter=60, tol=1e-10):
    """Return (fair probabilities, overround, k, method), matching the site."""
    q = [1 / d for d in decs]
    book = sum(q)
    if not book > 1:
        return [x / book for x in q], book, 1.0, "proportional"
    logs = [math.log(x) for x in q]
    k = 1.0
    for _ in range(max_iter):
        terms = [math.exp(k * x) for x in logs]
        f = sum(terms) - 1
        if abs(f) < tol:
            return terms, book, k, "power"
        fp = sum(x * y for x, y in zip(terms, logs))
        if not fp < 0:
            break
        nxt = k - f / fp
        if not math.isfinite(nxt) or nxt <= 1:
            break
        k = nxt
    return [x / book for x in q], book, 1.0, "proportional"


def build(payload, date):
    day = None
    for key in ("today", "tomorrow"):
        candidate = payload.get(key) or {}
        if candidate.get("date") == date:
            day = candidate
            break
    if not day:
        return None

    races = []
    for meeting in day.get("meetings") or []:
        course = meeting.get("course") or ""
        for race in meeting.get("races") or []:
            live = [r for r in race.get("runners") or []
                    if not r.get("nr") and decimal_odds(r.get("odds"))]
            if len(live) < 2:
                continue
            decs = [decimal_odds(r["odds"]) for r in live]
            probs, book, k, method = devig_power(decs)
            if not probs:
                continue
            best = max(range(len(probs)), key=lambda i: probs[i])
            runner = live[best]
            races.append({
                "course": course,
                "time": race.get("time") or "",
                "race": race.get("name") or "",
                "overround": round(book, 4),
                "k": round(k, 4),
                "method": method,
                "favorite": runner.get("horse") or "",
                "favorite_odds": runner.get("odds") or "",
                "favorite_fair": round(100 * probs[best], 1),
                "runners": len(live),
            })

    return {
        "date": date,
        "source": "The Overlay /data_us.json",
        "url": "https://theoverlay.co.uk/us-racing.html",
        "generated": payload.get("generated") or "",
        "races": races,
    }


def fetch():
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def archive(date, force=False):
    path = os.path.join(ROOT, "data", "overlay_us", f"{date}.json")
    if os.path.exists(path) and not force:
        return 0
    payload = fetch()
    data = build(payload, date)
    if not data:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return len(data["races"])


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    count = archive(day, force="--force" in sys.argv[1:])
    print(f"{day}: {count} US races with fair-chance snapshots")
    sys.exit(0 if count or os.path.exists(os.path.join(ROOT, "data", "overlay_us", f"{day}.json")) else 1)
