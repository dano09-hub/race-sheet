#!/usr/bin/env python3
"""Hong Kong declared runners from the HKJC GraphQL gateway.

    python3 tooling/scrape_hk.py 2026-11-14 --venue ST > hk.json

info.cld.hkjc.com/graphql/base/ answers plain POSTs (no browser, no key) but
whitelists exact query strings, so the query below is the one their own client
sends. It returns, per runner: programme number, English name, barrier draw,
handicap weight (lbs), current rating, last six finishing positions, win odds
once betting opens, jockey and trainer.

Card times are not in the payload; races run on a 30-minute cycle from 12:30
Hong Kong time (UTC+8), so race N is placed at that offset.
"""
import gzip, json, re, sys, urllib.request

URL = "https://info.cld.hkjc.com/graphql/base/"
QUERY = "query racing($date: String, $venueCode: String) {\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    status\n    currentNumberOfRace\n    totalInvestment\n    races {\n      no\n      status\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n  }\n}"


def fetch(date, venue="ST"):
    body = json.dumps({"operationName": "racing",
                       "variables": {"date": date, "venueCode": venue},
                       "query": QUERY}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept-Encoding": "gzip",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
        "Origin": "https://bet.hkjc.com", "Referer": "https://bet.hkjc.com/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    return json.loads(raw)


def form_string(last6):
    """'3/13/1/2/5' -> '3125' style figures, newest last."""
    if not last6:
        return ""
    parts = [p.strip() for p in last6.split("/") if p.strip()]
    return "".join(p if len(p) == 1 else "0" for p in parts)


def cards(payload, date_iso):
    out = []
    meets = (payload.get("data") or {}).get("raceMeetings") or []
    if not meets:
        return out
    for race in meets[0].get("races") or []:
        no = int(race.get("no") or 0)
        if not no:
            continue
        hh, mm = 12, 30 + (no - 1) * 30
        hh += mm // 60
        mm %= 60
        runners = []
        for r in race.get("runners") or []:
            runners.append({
                "horse": (r.get("name_en") or "").title(),
                "odds": str(r.get("winOdds") or ""),
                "form": form_string(r.get("last6run")),
                "or": str(int(r.get("currentRating") or 0)) if r.get("currentRating") else "",
                "rpr": "", "ts": "",
                "weight": str(r.get("handicapWeight") or ""),
                "days": "", "course": "N", "distance": "N", "going": "?",
                "jp": (r.get("jockey") or {}).get("name_en") or "",
                "tp": (r.get("trainer") or {}).get("name_en") or "",
                "tips": "", "naps": "",
                "draw": str(r.get("barrierDrawNumber") or ""),
            })
        if not runners:
            continue
        out.append({
            "id": f"hk-sha-tin-{date_iso[5:].replace('-', '')}-r{no}",
            "label": f"Race {no}", "course": "Sha Tin",
            "time": f"{hh-8:02d}:{mm:02d}", "day": date_iso, "region": "HK",
            "iso": f"{date_iso}T{hh-8:02d}:{mm:02d}:00.000Z",
            "race": f"Race {no}", "going": "", "klass": "", "note": "HKJC card: rating, last six, draw, weight",
            "runners": runners,
        })
    return out


if __name__ == "__main__":
    date = sys.argv[1]
    venue = "ST"
    for a in sys.argv[2:]:
        if a.startswith("--venue"):
            venue = a.split("=")[1]
    payload = fetch(date, venue)
    out = cards(payload, date)
    print(json.dumps(out, indent=1))
    print(f"# {len(out)} races, {sum(len(c['runners']) for c in out)} runners", file=sys.stderr)
