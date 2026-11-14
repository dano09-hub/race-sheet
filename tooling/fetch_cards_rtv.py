#!/usr/bin/env python3
"""Fetch GB & IRE racecards from the Racing TV JSON API.

Sporting Life gives prices and official ratings but no Timeform rating, no days
since last run and no course/distance badges. Racing TV's racecard endpoint has
those for every runner (100% Timeform rating, 96% days, 96% form on the day it
was measured) and covers Irish meetings, which is where the sheet is blind.

Prices are NOT in this feed before the race, so the Sporting Life cards are
merged in afterwards on course + time + horse.

usage: python3 tooling/fetch_cards_rtv.py 2026-11-14
writes data/cards/<date>_rtv.json and, when SL cards exist, _merged.json
"""
import datetime as dt, json, os, re, sys, time
import urllib.request, urllib.error
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = "https://api.racingtv.com/racing"
GB_IRE = {"ENG", "GB", "GBR", "SCO", "WAL", "NI", "NIR", "IRE", "IE"}
HEADERS = {
    "Origin": "https://www.racingtv.com",
    "Referer": "https://www.racingtv.com/",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i + 1 < tries:
                time.sleep(2 * (i + 1)); continue
            return None
        except Exception:
            if i + 1 < tries:
                time.sleep(1); continue
            return None
    return None


def _num(v):
    """'135p' -> '135'; anything unparseable -> ''."""
    m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
    return m.group(0) if m else ""


def _badges(r):
    b = {str(x).upper() for x in (r.get("text_badges") or [])}
    return b


def _runner(r):
    if r.get("withdrawn") or r.get("reserve"):
        return None
    b = _badges(r)
    course = "Y" if ({"C", "CD"} & b) else "N"
    distance = "Y" if ({"D", "CD"} & b) else "N"
    tip_rank = r.get("timeform_tipped_place")
    draw = r.get("barrier_number") or ""
    return {
        "horse": (r.get("horse_name") or "").strip(),
        "odds": "",
        "form": str(r.get("form") or "").strip(),
        "or": "",
        # legacy key name: this is Timeform's rating, not Racing Post's
        "rpr": _num(r.get("timeform_rating")),
        "ts": "",
        "weight": str(r.get("format_weight") or "").strip(),
        "days": ("" if r.get("days_since_last_run") in (None, "") else str(r.get("days_since_last_run"))),
        "course": course,
        "distance": distance,
        "going": "?",
        "jp": "",
        "tp": "",
        "tips": ("3" if tip_rank == 1 else "2" if tip_rank == 2 else "1" if tip_rank == 3 else ""),
        "naps": "",
        "draw": str(draw),
        "tf_rating": r.get("timeform_rating") or "",
        "tf_pick": tip_rank or "",
        "tf_comment": (r.get("analyst_comment") or "").strip(),
        "badges": "".join(sorted(b)),
    }


def race_card(date, track_slug, hhmm):
    data = get_json(f"{API}/racecards/{date}/{track_slug}/{hhmm}")
    if not data or not data.get("race"):
        return None
    race = data["race"]
    runners = [x for x in (_runner(r) for r in (data.get("runners") or [])) if x and x["horse"]]
    if len(runners) < 3:
        return None
    meeting = race.get("meeting") or {}
    track = meeting.get("track") or {}
    start = race.get("start_time_scheduled") or ""
    try:
        hhmm_out = dt.datetime.fromisoformat(start.replace("Z", "+00:00")) \
            .astimezone(ZoneInfo("Europe/London")).strftime("%H:%M")
    except Exception:
        hhmm_out = hhmm[:2] + ":" + hhmm[2:]
    return {
        "id": "rtv_" + str(race.get("id", "")),
        "label": (hhmm_out + " " + (track.get("name") or track_slug)).strip(),
        "course": track.get("name") or track_slug.replace("-", " ").title(),
        "time": hhmm_out,
        "race": race.get("title_full") or race.get("title") or "",
        "region": "GB",
        "day": date,
        "going": race.get("track_condition") or "",
        "klass": race.get("distance_formatted") or "",
        "note": race.get("analyst_verdict") or "",
        "runs": race.get("runner_count_text") or "",
        "runners": runners,
    }


def list_day(date):
    data = get_json(f"{API}/racecards/list/{date}")
    if not data:
        return []
    out = []
    for m in data.get("meetings") or []:
        track = m.get("track") or {}
        cc = (track.get("country_code") or {}).get("racing") or ""
        if cc not in GB_IRE:
            continue
        for r in m.get("races") or []:
            start = r.get("start_time_scheduled") or ""
            try:
                hhmm = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).strftime("%H%M")
            except Exception:
                continue
            out.append((track.get("slug", "").strip("/"), hhmm, track.get("name", "")))
    return out


def _norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def merge_sl(cards, sl_cards):
    """Fill prices, official ratings and anything else RTV left blank."""
    by_key = {}
    for c in sl_cards:
        by_key.setdefault((_norm(c.get("course")), str(c.get("time", ""))[:5]), []).append(c)
    filled_odds = filled_or = 0
    for c in cards:
        cands = by_key.get((_norm(c["course"]), c["time"])) or []
        if not cands:
            continue
        runners = {}
        for sc in cands:
            for r in sc.get("runners", []):
                runners.setdefault(_norm(r.get("horse")), r)
        for r in c["runners"]:
            src = runners.get(_norm(r["horse"]))
            if not src:
                continue
            for k in ("odds", "or"):
                if not r.get(k) and src.get(k):
                    r[k] = src[k]
                    filled_odds += (k == "odds")
                    filled_or += (k == "or")
            for k in ("form", "weight", "days", "draw"):
                if not r.get(k) and src.get(k):
                    r[k] = src[k]
    return cards, filled_odds, filled_or


def build(date):
    cards = []
    for slug, hhmm, name in list_day(date):
        c = race_card(date, slug, hhmm)
        if c:
            cards.append(c)
            rated = sum(1 for r in c["runners"] if r["rpr"])
            print(f"  {c['course']:14} {c['time']:5} {len(c['runners']):2} runners | rated {rated:2}")
    return cards


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    cards = build(date)
    os.makedirs(os.path.join(ROOT, "data", "cards"), exist_ok=True)
    if not cards:
        # no card list for that date (past days drop off the feed): leave any
        # earlier file alone rather than overwriting good cards with nothing
        print(f"no racecards returned for {date}; nothing written")
        raise SystemExit(0)
    out = os.path.join(ROOT, "data", "cards", f"{date}_rtv.json")
    json.dump(cards, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"wrote {len(cards)} cards -> {out}")

    sl_path = os.path.join(ROOT, "data", "cards", f"{date}_sl.json")
    if os.path.exists(sl_path):
        sl = json.load(open(sl_path, encoding="utf-8"))
        cards, odds, ors = merge_sl(cards, sl)
        merged = os.path.join(ROOT, "data", "cards", f"{date}_merged.json")
        json.dump(cards, open(merged, "w", encoding="utf-8"), ensure_ascii=False)
        n = sum(len(c["runners"]) for c in cards)
        with_odds = sum(1 for c in cards for r in c["runners"] if r["odds"])
        with_or = sum(1 for c in cards for r in c["runners"] if r["or"])
        with_rpr = sum(1 for c in cards for r in c["runners"] if r["rpr"])
        print(f"merged {len(cards)} cards / {n} runners: odds {with_odds} "
              f"({100*with_odds/max(1,n):.0f}%), OR {with_or}, rating {with_rpr}")
        print(f"wrote merged -> {merged}")
