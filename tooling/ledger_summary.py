#!/usr/bin/env python3
"""Write the UK and Irish ledger freshness to the Actions run summary.

The results job already knows when it last changed a ledger; this puts that
fact in the run summary so a green or red run shows it without opening the
page. The same rule as the tabs: an exact UTC time when a row carries one, and
the latest result day with an honest note when the rows predate the field.
"""
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import refresh                                            # noqa: E402


def _pretty(iso):
    when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return when.strftime("%-d %B %Y, %H:%M UTC")


def _day(iso):
    return dt.datetime.strptime(iso, "%Y-%m-%d").strftime("%-d %B %Y")


def ledger_line(kind, label):
    updated, date_iso = refresh._results_meta(kind)
    if updated:
        try:
            when = _pretty(updated)
        except ValueError:
            when = updated
        return f"- **{label}:** last changed **{when}**."
    if date_iso:
        try:
            day = _day(date_iso)
        except ValueError:
            day = date_iso
        return (f"- **{label}:** latest result **{day}**; the settlement time "
                "was not recorded for rows filed before this change.")
    return f"- **{label}:** no results on file."


def render():
    return ("## UK and Irish result ledgers\n\n"
            + ledger_line("GB", "UK") + "\n"
            + ledger_line("IRE", "Irish") + "\n")


def main():
    text = render()
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
