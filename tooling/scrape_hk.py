#!/usr/bin/env python3
"""Hong Kong declared runners from the official HKJC sources.

    python3 tooling/scrape_hk.py 2026-11-18 > hk.json

info.cld.hkjc.com/graphql/base/ answers plain POSTs (no browser, no key) but
whitelists exact query strings, so the query below is the one their own client
sends. It returns, per runner: programme number, English name, barrier draw,
handicap weight (lbs), current rating, last six finishing positions, win odds
once betting opens, jockey and trainer. Runners still on the reserve list come
back with a standby number and no draw; they are not part of the field.

The gateway carries no venue or post-time field. Those come from the official
race-card page for the same date, one page per race
(racing.hkjc.com/.../RaceCard.aspx?RaceDate=YYYY/MM/DD&RaceNo=N), which prints
the meeting, race name, post time (Hong Kong time), surface, distance, going
and class. A race is published only when both sources agree: a runner list
with no official time is not a card, and the time is never guessed.
"""
import datetime as dt
import gzip
import html
import json
import re
import sys
import urllib.request

URL = "https://info.cld.hkjc.com/graphql/base/"
CARD_URL = "https://racing.hkjc.com/racing/information/English/Racing/RaceCard.aspx"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
QUERY = "query racing($date: String, $venueCode: String) {\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    status\n    currentNumberOfRace\n    totalInvestment\n    races {\n      no\n      status\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n  }\n}"

# A runner on this status is declared but will not run, so it is not in the
# field and must not be scored.
NON_RUNNERS = {"standby", "reserve", "scratched", "withdrawn"}

_HEAD = re.compile(
    r"Race\s+(?P<no>\d+)\s*-\s*(?P<name>[^<]+?)\s*</span><br>\s*"
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
    r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4}),\s*"
    r"(?P<venue>[^,<]+),\s*(?P<time>\d{1,2}:\d{2})\s*<br>\s*"
    r"(?P<line>[^<]+?)\s*<br>\s*Prize Money:[^<]*?"
    r"Rating:\s*(?P<rating>[\d\-]+),\s*Class\s*(?P<klass>\d+)",
    re.S)


def _request(url, data=None, headers=None):
    hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


def fetch(date):
    """The HKJC GraphQL meeting payload for a date (YYYY/MM/DD or YYYY-MM-DD)."""
    date = str(date).replace("-", "/")
    body = json.dumps({"operationName": "racing",
                       "variables": {"date": date, "venueCode": "ST"},
                       "query": QUERY}).encode()
    raw = _request(URL, data=body, headers={
        "Content-Type": "application/json",
        "Origin": "https://bet.hkjc.com",
        "Referer": "https://bet.hkjc.com/",
    })
    return json.loads(raw)


def race_numbers(payload):
    meet = ((payload.get("data") or {}).get("raceMeetings") or [{}])[0]
    return sorted({int(r.get("no") or 0) for r in meet.get("races") or []
                   if r.get("no")})


def _parse_head(page, date_iso, no):
    m = _HEAD.search(page or "")
    if not m or int(m.group("no")) != int(no):
        return None
    try:
        when = dt.datetime.strptime(
            "%s %s %s" % (m.group("month"), m.group("day"), m.group("year")),
            "%B %d %Y").date()
    except ValueError:
        return None
    # A stale or redirected page for another day is not this card.
    if when.isoformat() != date_iso:
        return None
    parts = [p.strip() for p in m.group("line").split(",") if p.strip()]
    distance, going, di = "", "", None
    for i, part in enumerate(parts):
        if re.fullmatch(r"\d+\s*M", part, re.I):
            distance, di = re.sub(r"\s+", "", part).upper(), i
            break
    if di is not None and len(parts) > di + 1:
        going = parts[di + 1]
    hh, mm = [int(x) for x in m.group("time").split(":")]
    post = dt.datetime.combine(when, dt.time(hh, mm)) - dt.timedelta(hours=8)
    return {
        "no": int(no),
        "name": m.group("name").strip(),
        "venue": m.group("venue").strip(),
        "hkt": "%02d:%02d" % (hh, mm),
        "time": post.strftime("%H:%M"),
        "iso": post.strftime("%Y-%m-%dT%H:%M:00.000Z"),
        "surface": parts[0] if parts else "",
        "distance": distance,
        "going": going,
        "class": int(m.group("klass")),
        "rating": m.group("rating"),
    }


def fetch_meta(date_iso, nos):
    """Official venue, name, post time and going, one race-card page each."""
    out = {}
    for no in sorted(nos):
        url = "%s?RaceDate=%s&Racecourse=HV&RaceNo=%d" % (
            CARD_URL, date_iso.replace("-", "/"), int(no))
        try:
            page = _request(url)
        except Exception as e:
            print("# HK R%d: %s" % (no, e), file=sys.stderr)
            continue
        meta = _parse_head(page, date_iso, no)
        if meta:
            out[int(no)] = meta
        else:
            print("# HK R%d: no official race-card header; race skipped" % no,
                  file=sys.stderr)
    return out


def form_string(last6):
    """'3/13/1/2/5' -> '3125' style figures, newest last."""
    if not last6:
        return ""
    parts = [p.strip() for p in last6.split("/") if p.strip()]
    return "".join(p if len(p) == 1 else "0" for p in parts)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def cards(payload, date_iso, meta=None):
    out = []
    meets = (payload.get("data") or {}).get("raceMeetings") or []
    if not meets:
        return out
    meta = meta or {}
    for race in meets[0].get("races") or []:
        no = int(race.get("no") or 0)
        info = meta.get(no)
        if not no or not info:
            continue
        runners, declared = [], 0
        for r in race.get("runners") or []:
            if str(r.get("standbyNo") or "").strip():
                continue
            if str(r.get("status") or "").strip().lower() in NON_RUNNERS:
                continue
            declared += 1
            name = (r.get("name_en") or "").strip()
            if not name:
                continue
            runners.append({
                "horse": name.title(),
                "no": str(r.get("no") or ""),
                "odds": str(r.get("winOdds") or ""),
                "form": form_string(r.get("last6run")),
                "or": str(int(r.get("currentRating") or 0)) if r.get("currentRating") else "",
                "rpr": "", "ts": "",
                "weight": str(r.get("handicapWeight") or ""),
                "days": "", "course": "N", "distance": "N", "going": "?",
                "draw": str(r.get("barrierDrawNumber") or ""),
                "jp": (r.get("jockey") or {}).get("name_en") or "",
                "tp": (r.get("trainer") or {}).get("name_en") or "",
                "tips": "", "naps": "",
            })
        if not runners:
            continue
        if len(runners) != declared:
            # The same rule as the overseas feed: a race we cannot read in full
            # is not a field, so it is left out rather than scored in part.
            print("# HK R%d: read %d of %d declared runners; race skipped"
                  % (no, len(runners), declared), file=sys.stderr)
            continue
        klass = info["distance"] or "HK"
        if info.get("class"):
            klass += " · Class %d" % info["class"]
        out.append({
            "id": "hk-%s-%s-r%d" % (_slug(info["venue"]),
                                    date_iso[5:].replace("-", ""), no),
            "label": "Race %d" % no,
            "course": info["venue"],
            "time": info["time"],
            "day": date_iso,
            "region": "HK",
            "iso": info["iso"],
            "race": info["name"].title() if info["name"].isupper() else info["name"],
            "going": info["going"],
            "klass": klass,
            "note": "HKJC card: official rating, last six, barrier draw, weight.",
            "runners": runners,
        })
    return out


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    iso = date.replace("/", "-")
    payload = fetch(iso)
    out = cards(payload, iso, fetch_meta(iso, race_numbers(payload)))
    print(json.dumps(out, indent=1))
    print(f"# {len(out)} races, {sum(len(c['runners']) for c in out)} runners",
          file=sys.stderr)
