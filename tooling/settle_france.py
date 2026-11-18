#!/usr/bin/env python3
"""Settle France and rebuild the France tabs.

The results job calls this after Hong Kong and before the page is written, so a
published page never carries a French ledger that lags PMU's definitive arrival.
A feed that cannot be read exits non-zero and prints a GitHub annotation.
"""
import datetime as dt
import sys

import feed
import france


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    iso = argv[0] if argv else dt.date.today().isoformat()
    # The last French races can finish after the final evening run. Settling
    # yesterday too is idempotent and never rewrites a stored pick.
    yday = (dt.date.fromisoformat(iso) - dt.timedelta(days=1)).isoformat()
    try:
        rows = france.settle(yday)
        rows += france.settle(iso)
        cards = france.refresh_page(iso)
    except feed.FeedError as e:
        print(f"France results: FAILED ({e})")
        print(f"::error::French feed unavailable for {iso}: {e}")
        return 1
    print(f"France results: {len(rows)} settled, {cards} cards on the page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
