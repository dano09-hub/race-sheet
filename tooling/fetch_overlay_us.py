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
INFORM_URL = "https://theoverlay.co.uk/us_inform.json"
ANGLE_URL = "https://theoverlay.co.uk/us_ab/{date}.json"
RAW_DIR = os.path.join(ROOT, "data", "us_data")
ANGLE_DIR = os.path.join(ROOT, "data", "us_angles")
INFORM_PATH = os.path.join(ROOT, "data", "us_inform.json")
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



def _norm(value):
    text = re.sub(r"\(.*?\)", "", str(value or "")).lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def backfill_from_cards(date, folder="overlay_us", force=False):
    """Fill races missing from the live feed using the archived US card.

    Parx is often absent from the live feed because its card has already
    finished.  The archived card still has the morning-line prices, so the
    same power-method de-vig can fill that gap.  These rows are marked
    backfill so the page never presents them as a live Overlay snapshot.
    """
    base_path = os.path.join(ROOT, "data", folder, f"{date}.json")
    cards_path = os.path.join(ROOT, "data", "cards", "by_date", f"{date}.json")
    out_path = os.path.join(ROOT, "data", folder, f"{date}.backfill.json")
    if (os.path.exists(out_path) and not force) or not os.path.exists(base_path) or not os.path.exists(cards_path):
        return 0
    try:
        base = json.load(open(base_path, encoding="utf-8"))
        card_data = json.load(open(cards_path, encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    cards = card_data.get("US") if isinstance(card_data, dict) else card_data
    if not cards:
        return 0
    base_keys = {(_norm(r.get("course")), str(r.get("time") or "")[:5])
                 for r in base.get("races") or []}
    seen, races = set(), []
    for card in cards:
        key = (_norm(card.get("course")), str(card.get("time") or "")[:5])
        if key in base_keys or key in seen:
            continue
        runners = [r for r in card.get("runners") or []
                   if not r.get("scratched") and decimal_odds(r.get("odds"))]
        if len(runners) < 2:
            continue
        probs, book, k, method = devig_power([decimal_odds(r["odds"]) for r in runners])
        best = max(range(len(probs)), key=lambda i: probs[i])
        runner = runners[best]
        seen.add(key)
        races.append({"course": card.get("course") or "", "time": card.get("time") or "",
                      "race": card.get("race") or "", "overround": round(book, 4),
                      "k": round(k, 4), "method": method,
                      "favorite": runner.get("horse") or "",
                      "favorite_odds": runner.get("odds") or "",
                      "favorite_fair": round(100 * probs[best], 1),
                      "runners": len(runners), "backfill": True})
    if not races:
        return 0
    data = {"date": date, "source": "archived US card (Horse Racing Nation morning line)",
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "races": races}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return len(races)

def _fetch_url(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch():
    return _fetch_url(URL)


def _raw_day(payload, date):
    for key in ("today", "tomorrow"):
        day = payload.get(key) or {}
        if day.get("date") == date:
            return day
    return None


def _save_raw(payload, date):
    day = _raw_day(payload, date)
    if not day:
        return 0
    os.makedirs(RAW_DIR, exist_ok=True)
    data = {"date": date, "generated": payload.get("generated") or "",
            "source": "The Overlay /data_us.json",
            "url": "https://theoverlay.co.uk/us-racing.html",
            "meetings": day.get("meetings") or []}
    with open(os.path.join(RAW_DIR, f"{date}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return sum(len(m.get("races") or []) for m in data["meetings"])


def ensure_raw(date):
    """Cache the live US runner detail used by the read-only flags tab."""
    path = os.path.join(RAW_DIR, f"{date}.json")
    if os.path.exists(path):
        return 0
    return _save_raw(fetch(), date)


def fetch_angles(date, force=False):
    """Cache the public runner-flag feed used by the Angles tab."""
    path = os.path.join(ANGLE_DIR, f"{date}.json")
    if os.path.exists(path) and not force:
        return 0
    try:
        payload = _fetch_url(ANGLE_URL.format(date=date))
    except Exception:
        return 0
    if not payload.get("races"):
        return 0
    os.makedirs(ANGLE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return len(payload["races"])


def fetch_inform(force=False):
    """Refresh the weekly trainer/jockey/horse inform snapshot at most daily."""
    if os.path.exists(INFORM_PATH) and not force:
        age = dt.datetime.now().timestamp() - os.path.getmtime(INFORM_PATH)
        if age < 20 * 3600:
            return 0
    payload = _fetch_url(INFORM_URL)
    if not payload.get("ok"):
        return 0
    with open(INFORM_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return len(payload.get("trainers") or {})


def archive(date, force=False):
    path = os.path.join(ROOT, "data", "overlay_us", f"{date}.json")
    if os.path.exists(path) and not force:
        return 0
    payload = fetch()
    _save_raw(payload, date)
    data = build(payload, date)
    if not data:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return len(data["races"])


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    force = "--force" in sys.argv[1:]
    count = archive(day, force=force)
    filled = backfill_from_cards(day, force=force) if force else backfill_from_cards(day)
    print(f"{day}: {count} live fair-chance snapshots, {filled} archived-card backfills")
    sys.exit(0 if (count or filled or os.path.exists(os.path.join(ROOT, "data", "overlay_us", f"{day}.json"))) else 1)
