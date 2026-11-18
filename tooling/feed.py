#!/usr/bin/env python3
"""One retry policy for every results feed.

Each scraper used to answer an empty string for every failure, which made an
unreachable feed and a quiet day the same thing: a run reported success while
writing nothing. This retries a page a few times with a growing wait and raises
when it never arrives, so the caller can end the run red instead of publishing
an empty ledger.

    feed.get_page(module.get, url, label="UK/IRE results")
"""
import sys
import time

TRIES = 3
BACKOFF = (2, 6, 18)          # seconds to wait before each further attempt


class FeedError(RuntimeError):
    """A feed could not be read at all, as opposed to having nothing on it."""


def get_page(fetch, url, tries=TRIES, backoff=BACKOFF, label=""):
    """Call ``fetch(url)`` until it returns something, then raise FeedError.

    A transport exception counts as an empty answer, since every scraper here
    reports failure by handing back nothing at all.
    """
    name = label or url
    for attempt in range(1, tries + 1):
        try:
            body = fetch(url)
        except Exception as e:
            if attempt == tries:
                raise FeedError(f"{name}: {e}") from e
            body = ""
        if body:
            return body
        if attempt == tries:
            break
        delay = backoff[min(attempt - 1, len(backoff) - 1)]
        print(f"  feed:        {name} gave nothing, retrying in {delay}s "
              f"({attempt}/{tries})", file=sys.stderr)
        time.sleep(delay)
    raise FeedError(f"{name}: no response after {tries} attempts")
