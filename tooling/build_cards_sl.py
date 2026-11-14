#!/usr/bin/env python3
"""Rebuild a day's racecards from Sporting Life so settled races can be scored.

The live sheet only ever holds today/tomorrow; once it rolls over the cards are
gone and no pick can be calculated. Sporting Life keeps racecards online, so we
can reconstruct them after the fact.

Parsing is done per runner block (data-testid="runner") rather than by anchoring
on a single "Weight: ..." string: that anchor swallowed the odds whenever a
runner had no history flag, which is why half the rebuilt cards had no price.
"""
import json, re, sys, datetime as dt
import fetch_results_sl as f


def text_of(html):
    t = re.sub(r'<script.*?</script>', ' ', html, flags=re.S)
    t = re.sub(r'<style.*?</style>', ' ', t, flags=re.S)
    t = re.sub(r'<!--.*?-->', ' ', t, flags=re.S)
    t = re.sub(r'<[^>]+>', '|', t)
    return re.sub(r'(\s*\|+\s*)+', '|', t).strip()


def _clean(s):
    s = re.sub(r'<!--.*?-->', ' ', s or '')
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _first(block, pattern, group=1):
    m = re.search(pattern, block, re.S)
    return _clean(m.group(group)) if m else ""


def _runner(block):
    horse = _first(block, r'data-testid="horse-name-link"[^>]*>([^<]+)<')
    if not horse:
        return None
    stats = re.findall(r'data-testid="race-history-stat-\d+"[^>]*>([\s\S]*?)</span>', block)
    codes = {_clean(s).strip(' |') for s in stats if _clean(s).strip(' |')}
    stats = " ".join(sorted(codes))
    odds_raw = _first(block, r'data-testid="betting-odds-wrapper"[\s\S]{0,240}?<span[^>]*>([^<]+)<')
    odds = odds_raw if re.match(r'^\d', odds_raw) else ""
    form = _first(block, r'Form:\s*(?:</span>)?\s*(?:<!-- -->)?\s*([0-9PFURDU\-/]+)')
    days = _first(block, r'data-testid="last-ran"[^>]*>([^<]*)<')
    return {
        "horse": horse,
        "odds": odds,
        "form": form,
        "or": _first(block, r'OR:\s*(?:\d+\s*\|\s*)?(\d+)') or _first(block, r'OR:[\s\S]{0,20}?(\d+)'),
        "rpr": "",
        "ts": "",
        "weight": _first(block, r'Weight:\s*([0-9\-]+)'),
        "days": days,
        "course": "Y" if ({"C", "CD"} & codes) else "N",
        "distance": "Y" if ({"D", "CD"} & codes) else "N",
        "going": "?",
        # the sheet's jp/tp are numeric form codes, not names; leave the factor
        # empty rather than feed it a string it cannot score.
        "jp": "",
        "tp": "",
        "jockey": _first(block, r'J:\s*(?:<!-- -->)?\s*([^<]+)<'),
        "trainer": _first(block, r'T:\s*(?:<!-- -->)?\s*([^<]+)<'),
        "tips": "",
        "naps": "",
        "draw": _first(block, r'data-testid="stall-no"[^>]*>([^<]*)<'),
        "stat": stats,
    }


def parse_race(url):
    html = f.get(f.BASE + url)
    if not html:
        return None
    cc = f.country_of(html)
    if cc and cc not in f.GB_IRE:
        return None
    parts = url.strip('/').split('/')
    course = parts[3].replace('-', ' ').title()
    m = (re.search(r'MainTitle[^>]*>([\s\S]{0,80}?)</p>', html)
         or re.search(r'racecard-title[^>]*>(.*?)<', html, re.S))
    title = _clean(m.group(1)) if m else ""
    if not title:
        tm = re.search(r'"time":"(\d{1,2}:\d{2})"', html)
        title = (tm.group(1) + " " + course) if tm else ""
    t = re.match(r'(\d{1,2}:\d{2})', title)
    time = t.group(1) if t else ""
    txt = text_of(html)
    going = ""
    g = re.search(r'Going:\s*\|?\s*([A-Za-z][A-Za-z ]{2,20}?)\s*\|', txt)
    if g:
        going = g.group(1).strip()

    blocks = html.split('data-testid="runner"')[1:]
    runners, seen = [], set()
    for b in blocks:
        b = b.split('data-testid="runner"')[0]
        r = _runner(b)
        if not r or r["horse"] in seen or len(r["horse"]) < 2:
            continue
        seen.add(r["horse"])
        runners.append(r)
    if len(runners) < 3:
        return None
    # foreign meetings (Chile, Uruguay, Argentina) come through without a price
    # or a rating on any runner; they add nothing to a GB & IRE sheet.
    if not any(r["odds"] for r in runners) and not any(r["or"] for r in runners):
        return None
    return {"id": "sl_" + parts[5], "label": (time + " " + course).strip(), "course": course,
            "time": time, "race": title, "region": "GB", "day": parts[2], "going": going,
            "klass": "", "note": "", "runners": runners}


def build(date):
    idx = f.get(f"{f.BASE}/racing/racecards/{date}")
    links = sorted(set(re.findall(rf'href="(/racing/racecards/{date}/[^"]+)"', idx)))
    print(f"{len(links)} racecards for {date}")
    cards = []
    for l in links:
        c = parse_race(l)
        if c:
            cards.append(c)
            priced = sum(1 for r in c["runners"] if r["odds"])
            rated = sum(1 for r in c["runners"] if r["or"])
            print(f"  {c['course']:14} {c['time']:5} {len(c['runners']):2} runners"
                  f" | priced {priced:2} | rated {rated:2}")
    return cards


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    out = sys.argv[2] if len(sys.argv) > 2 else f"data/cards/{d}_sl.json"
    cards = build(d)
    json.dump(cards, open(out, "w"), ensure_ascii=False)
    print(f"wrote {len(cards)} cards -> {out}")
