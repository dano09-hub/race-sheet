#!/usr/bin/env python3
"""Read the public Smarkets exchange book for one day's races.

Smarkets is a betting exchange, so every contract has two sides. Its public
v3 API returns resting `bids` (buys, i.e. backers) and `offers` (sells, i.e.
layers) for each contract. The best available **lay** is therefore the
highest bid, and the best available **back** the lowest offer. Prices are
percentage basis points (5000 = 50%), so decimal odds are 10000 / price.
Quantity is the pot in hundredths of a penny, so the backer's stake a level
can match is quantity * price / 100,000,000.

Only the read endpoints are used, with no session. Smarkets delays quotes
for anonymous callers (it does not say by how much) and holds them to lower
limits than an approved API account, so this client pauses between quote
calls. Nothing here places an order, and nothing invents a price: a
contract with no resting bid has no lay price and is left out.

usage: python3 tooling/smarkets.py [YYYY-MM-DD]
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = "https://api.smarkets.com/v3"
LONDON = ZoneInfo("Europe/London")

# The public endpoints answer this client and answer a bare urllib request
# with 403, so the browser agent the site itself serves is sent.
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}

PAUSE = 3.2          # anonymous /quotes/ is limited to about 20 a minute
MAX_RACES = 60       # keep one refresh run bounded


def _get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def _off(event):
    """The race's off time where the card is, not the UTC instant."""
    raw = event.get("start_datetime")
    if not raw:
        return None
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(LONDON)


def key(name):
    """A course or horse name reduced to what two feeds can agree on."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def race_key(course, off):
    """(course, off) the way the stored cards name a race."""
    return (key(course), str(off or "")[:5])


def _course(event):
    """The course from the event slug: /sport/horse-racing/<course>/..."""
    parts = (event.get("full_slug") or "").split("/")
    slug = parts[3] if len(parts) > 3 else ""
    slug = re.sub(r"-(aus|usa|nz|ire|gb|uae)$", "", slug)
    return " ".join(w.capitalize() for w in slug.split("-") if w)


def races(day):
    """Every horse race whose local off date is `day`, newest first."""
    url = f"{BASE}/events/?type=horse_racing_race&state=upcoming&limit=200"
    out = []
    while url:
        data = _get(url)
        for event in data.get("events") or []:
            off = _off(event)
            if off and off.date().isoformat() == day:
                out.append({"id": str(event.get("id")),
                            "course": _course(event),
                            "off": off.strftime("%H:%M"),
                            "start": off.isoformat()})
        url = (f"{BASE}/events/{data['pagination']['next_page']}"
               if (data.get("pagination") or {}).get("next_page") else None)
    out.sort(key=lambda r: r["off"])
    return out


def _best(levels, better):
    """The best price and its quantity from one side of the book."""
    if not levels:
        return None, None
    top = better(levels, key=lambda q: q.get("price") or 0)
    return top.get("price"), top.get("quantity")


def runners(contracts, quotes):
    """One row per contract: its best lay and back, and the money at the lay.

    The best lay is the highest resting bid (a backer's price a layer can
    take); the best back is the lowest offer. A contract with no bid has no
    lay price and keeps None.
    """
    out = []
    for c in contracts:
        side = quotes.get(str(c.get("id"))) or {}
        lay, qty = _best(side.get("bids"),
                         lambda levels, key: max(levels, key=key))
        back, _ = _best(side.get("offers"),
                        lambda levels, key: min(levels, key=key))
        out.append({"name": c.get("name") or "",
                    "lay_price": lay,
                    "lay_odds": (10000.0 / lay) if lay else None,
                    "lay_stake": stake(lay, qty),
                    "back_odds": (10000.0 / back) if back else None,
                    "status": (c.get("info") or {}).get("status") or ""})
    return out


def stake(price, quantity):
    """The backer's stake a level can match, in pounds.

    Smarkets: stake = quantity * price / 100,000,000.
    """
    if not price or not quantity:
        return None
    return quantity * price / 100000000.0


def book(day, wanted, pause=PAUSE, limit=MAX_RACES):
    """The WINNER book for races `wanted` matches, keyed by race_key.

    `wanted` is a dict of race_key -> the label the caller uses for it. Events
    are matched on course and UK off time only; a race the exchange lists
    differently is not guessed at.
    """
    by_key = {}
    for race in races(day):
        k = race_key(race["course"], race["off"])
        if k in wanted:
            by_key[k] = race
    out = {}
    for i, (k, race) in enumerate(sorted(by_key.items())):
        if i >= limit:
            break
        markets = _get(f"{BASE}/events/{race['id']}/markets/").get("markets") or []
        win = next((m for m in markets
                    if (m.get("market_type") or {}).get("name") == "WINNER"
                    and m.get("state") == "open"), None)
        if not win:
            continue
        mid = str(win.get("id"))
        contracts = _get(f"{BASE}/markets/{mid}/contracts/").get("contracts") or []
        quotes = _get(f"{BASE}/markets/{mid}/quotes/")
        if pause:
            time.sleep(pause)
        out[k] = {"event_id": race["id"], "market_id": mid,
                  "course": race["course"], "off": race["off"],
                  "runners": runners(contracts, quotes)}
    return out


def runner(race, name):
    """The exchange contract for a runner, matched on the name alone."""
    want = key(name)
    for r in (race or {}).get("runners") or []:
        if key(r["name"]) == want:
            return r
    return None


def main(argv):
    day = argv[0] if argv else dt.datetime.now(LONDON).date().isoformat()
    listed = races(day)
    print(f"{day}: {len(listed)} Smarkets races")
    for race in listed:
        print(f"  {race['off']}  {race['course']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
