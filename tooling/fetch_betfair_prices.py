#!/usr/bin/env python3
"""Download Betfair public daily win price CSVs.

    python3 tooling/fetch_betfair_prices.py \
        --from 2024-05-01 --to 2026-11-30 \
        --out /tmp/betfair_win

Betfair returns HTTP 403 to this cloud host (US egress), so run this from a
UK/IE network or another host that Betfair serves. The files are the same
public `dwbfprices{country}win{DDMMYYYY}.csv` files listed at
https://promo.betfair.com/betfairsp/prices.

The downloader is deliberately separate from the model. Once the directory is
populated, rerun:

    python3 tooling/backtest_places_betfair.py --db ... --prices /tmp/betfair_win
"""
import argparse
import datetime as dt
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://promo.betfair.com/betfairsp/prices"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def dates(start, end):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2024-05-01")
    ap.add_argument("--to", dest="end", default=dt.date.today().isoformat())
    ap.add_argument("--countries", default="uk,ire")
    ap.add_argument("--out", required=True)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many successful files (0 = all)")
    args = ap.parse_args()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    countries = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    os.makedirs(args.out, exist_ok=True)

    ok = skipped = blocked = 0
    for d in dates(start, end):
        for country in countries:
            name = "dwbfprices%swin%s.csv" % (
                country, d.strftime("%d%m%Y"))
            path = os.path.join(args.out, name)
            if os.path.exists(path) and not args.force:
                skipped += 1
                continue
            url = "%s/%s" % (BASE, name)
            try:
                data = fetch(url)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    blocked += 1
                    print("HTTP 403 for %s - Betfair blocks this network; "
                          "run from a UK/IE host." % name, file=sys.stderr)
                    print("aborted after first block; %d files saved" % ok)
                    return 2
                if e.code == 404:
                    skipped += 1
                    continue
                print("HTTP %s for %s" % (e.code, name), file=sys.stderr)
                continue
            except Exception as e:
                print("failed %s: %s" % (name, e), file=sys.stderr)
                continue
            if not (data.startswith(b"EVENT_ID") or data.startswith(b"event_id")):
                print("not a price CSV: %s" % name, file=sys.stderr)
                continue
            with open(path, "wb") as f:
                f.write(data)
            ok += 1
            print("saved %s (%d bytes)" % (name, len(data)))
            if args.sleep:
                time.sleep(args.sleep)
            if args.limit and ok >= args.limit:
                print("limit reached: %d saved, %d skipped, %d blocked" % (ok, skipped, blocked))
                return 0
    print("done: %d saved, %d skipped/existing, %d blocked" % (ok, skipped, blocked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
