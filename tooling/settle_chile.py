#!/usr/bin/env python3
"""Settle Chile and rebuild the Chile tabs.

Chile comes from the same PMU programme as France: cards and definitive
arrivals, but no prices, so its picks are scored without the market factor and
the page says so. A feed that cannot be read exits non-zero and annotates the
run.
"""
import datetime as dt
import sys

import feed
import france


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    iso = argv[0] if argv else dt.date.today().isoformat()
    # A Chilean meeting runs late in the UTC evening, after the final 20-minute
    # run of the day. Settling yesterday as well is what stops those results
    # being stranded by the date rollover; it is idempotent and never rewrites
    # a stored pick.
    yday = (dt.date.fromisoformat(iso) - dt.timedelta(days=1)).isoformat()
    try:
        rows = france.settle(yday, "CL")
        rows += france.settle(iso, "CL")
        cards = france.refresh_page(iso, "CL")
    except feed.FeedError as e:
        print(f"Chile results: FAILED ({e})")
        print(f"::error::Chile feed unavailable for {iso}: {e}")
        return 1
    print(f"Chile results: {len(rows)} settled, {cards} cards on the page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
