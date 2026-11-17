#!/usr/bin/env python3
"""Attach the Model chance to races settled before the page printed one.

The Model column is new, so settlement stored the pick but not the chance it was
shown at. Every day that still has its archived cards can be re-scored with the
page's own model (tooling/score_cards.js). The chance is written only where the
rebuilt pick is exactly the pick on record: a race whose card cannot reproduce
its pick is left blank rather than given a number the page never showed.

Nothing else in a stored row is touched, and no pick is rewritten. Afterwards
the ledger blocks are rebuilt so the chance appears beside the result.

    python3 tooling/backfill_chance.py
    python3 tooling/backfill_chance.py --dry-run
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import chance_audit  # noqa: E402
import refresh       # noqa: E402
import settle        # noqa: E402


def _resolver(cards, picks):
    by_time, by_course = {}, {}
    for card, p in zip(cards, picks):
        if not p.get("pick"):
            continue
        by_time[(p.get("course"), str(p.get("time") or "")[:5])] = p
        by_course.setdefault(p.get("course"), []).append((card, p))

    def resolve(row):
        key = (row.get("course"), str(row.get("time") or "")[:5])
        if key in by_time:
            return by_time[key]
        for card, p in by_course.get(row.get("course"), []):
            if any(r.get("horse") == row.get("winner")
                   for r in card.get("runners", [])):
                return p
        return None
    return resolve


def backfill(dry_run=False):
    dates = sorted(os.path.basename(p)[:-5] for p in glob.glob(
        os.path.join(ROOT, "data", "cards", "by_date", "*.json")))
    totals = {"rows": 0, "attached": 0, "no_card": 0, "mismatch": 0}
    for iso in dates:
        rpath = os.path.join(ROOT, "data", "results", f"{iso}.json")
        if not os.path.exists(rpath):
            continue
        with open(rpath, encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, dict):
            rows = rows.get("races", [])
        cards = settle._cards_for(iso)
        resolve = _resolver(cards, settle.score_cards(cards))
        changed = 0
        for row in rows:
            if row.get("region") == "US" or not row.get("pick"):
                continue
            if row.get("chance") is not None:
                continue
            totals["rows"] += 1
            p = resolve(row)
            if not p:
                totals["no_card"] += 1
                continue
            if p["pick"] != row["pick"]:
                totals["mismatch"] += 1
                continue
            row["chance"] = p.get("chance")
            changed += 1
        totals["attached"] += changed
        print(f"  {iso}: {changed} chances attached")
        if changed and not dry_run:
            with open(rpath, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
    return totals


def rebuild(dry_run=False):
    """Re-render the ledgers so a backfilled chance is visible."""
    html = open(refresh.INDEX, encoding="utf-8").read()
    uk = refresh.results_block_all()
    us = refresh.results_block_all_us()
    if uk:
        html = re.sub(r"<!-- RESULTS_SL_START -->.*?<!-- RESULTS_SL_END -->",
                      uk, html, count=1, flags=re.S)
    if us:
        html = re.sub(r"<!-- US_RESULTS_SL_START -->.*?<!-- US_RESULTS_SL_END -->",
                      us, html, count=1, flags=re.S)
    html = re.sub(r"<!-- CAL_SL_START -->.*?<!-- CAL_SL_END -->",
                  refresh.chance_block_html(chance_audit.collect()),
                  html, count=1, flags=re.S)
    if not dry_run:
        open(refresh.INDEX, "w", encoding="utf-8").write(html)
        os.makedirs(os.path.join(ROOT, "dist", "public"), exist_ok=True)
        open(os.path.join(ROOT, "dist", "public", "index.html"),
             "w", encoding="utf-8").write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    totals = backfill(args.dry_run)
    rebuild(args.dry_run)
    print(f"  rows without a chance: {totals['rows']}; attached "
          f"{totals['attached']}; no archived card {totals['no_card']}; "
          f"card would change the pick {totals['mismatch']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
