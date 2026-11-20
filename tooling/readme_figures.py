#!/usr/bin/env python3
"""The README numbers a render already computes.

Four claims in the README repeat figures the page prints from stored data: the
twelve-factor chance record in `data/results`, the six-input re-score's chance
audit, the count of races with no stored chance, and the market-heavy shadow
tally. Each sits between markers and is rebuilt from the same collector the page
uses, so the README cannot describe an older build than the page.

Nothing here scores a race, reads a card for a pick or changes a figure: every
number is counted from the stored results, the stored re-score and the saved
A/B snapshots. The figures that cannot be re-derived here -- the share scales,
the whole-field audit's archive CSV, the Kaggle and Betfair reports -- stay as
typed prose beside these blocks.

usage: python3 tooling/readme_figures.py [--write]
"""
import glob
import json
import os
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ab_tally          # noqa: E402
import chance_audit      # noqa: E402
import market_arm_day    # noqa: E402

RESULTS = os.path.join(ROOT, "data", "results")
README = os.path.join(ROOT, "README.md")

BLOCKS = ("chance-record", "chance-rated", "blank-races", "shadow-tally")


def _count(n, noun):
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _markers(name):
    return f"<!-- {name}:start -->", f"<!-- {name}:end -->"


def collect():
    """Every repeated figure, from the collectors the page already uses."""
    record = chance_audit.collect()
    settled = 0
    for path in sorted(glob.glob(os.path.join(RESULTS, "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("races", []) if isinstance(data, dict) else data
        # The chance column is the twelve-factor record's, kept for GB/IRE only.
        settled += sum(1 for row in rows
                       if row.get("region") != "US" and row.get("winner"))
    return {
        "chance-record": record,
        "chance-rated": chance_audit.collect_rated(),
        "blank-races": {"blank": max(0, settled - record["races"]),
                        "races": settled},
        "shadow-tally": ab_tally.collect(snap_dir=market_arm_day.OUT_DIR,
                                         subject=market_arm_day.SUBJECT),
    }


def chance_record(figs):
    """The live archive's twelve-factor record against its own interval."""
    if not figs["races"]:
        return "No settled British or Irish race carries a stored chance yet."
    if figs["inside"]:
        state = "agrees on the direction but not yet on the size"
    elif figs["stated"] < figs["lo"]:
        state = "reads low"
    else:
        state = "reads high"
    return (f"The live archive {state}: on its own {figs['races']} GB/IRE "
            f"races the pick is shown at {figs['stated']:.1f}% and won "
            f"{figs['wins']} ({figs['rate'] * 100:.1f}%, 95% CI "
            f"{figs['lo']:.1f}-{figs['hi']:.1f}%).")


def chance_rated(figs):
    """The six-input re-score's chance audit, at the stage the block reports."""
    if not figs["races"]:
        return "No settled race has a card complete on all six inputs yet."
    if figs["more_early"]:
        stage = (f"so the block says the sample is not yet informative "
                 f"({figs['more_early']} more settled races)")
    elif figs["more_decisive"]:
        stage = (f"so the block calls the read informative but not yet "
                 f"decisive ({figs['more_decisive']} more settled races)")
    else:
        stage = "so the block says the sample is large enough to test the scale"
    return (f"At this build that is {figs['races']} rated races with "
            f"{_count(figs['wins'], 'winner')}: the re-scored picks carry "
            f"{figs['stated']:.1f}% on average and their win rate has a 95% "
            f"interval of {figs['lo']:.1f}-{figs['hi']:.1f}%, {stage}.")


def blank_races(figs):
    """How much of the settled record carries no stored chance."""
    return (f"{figs['blank']} of the {figs['races']} recent British and Irish "
            f"races stay blank.")


def shadow_tally(figs):
    """The market-heavy arm's record, with the pre-registered rule's status."""
    if not figs["races"]:
        return "No settled race has a shadow pick yet."
    rule = figs["rule"]
    if rule["status"] == "adopt":
        verdict = "which clears the pre-registered bar"
    elif rule["status"] == "retire":
        verdict = "which misses the pre-registered bar, so the arm is retired"
    else:
        verdict = (f"and a lead of {rule['lead']:+.1f} points at one-sided p "
                   f"{rule['p']:.2f}, still inside the pre-registered bar")
    return (f"The {len(figs['days'])} settled shadow days give {figs['races']} "
            f"races: {figs['with']} winners for the arm against "
            f"{figs['without']} for the page, with "
            f"{_count(figs['changed'], 'pick')} changed (won "
            f"{figs['changed_won']}, lost {figs['changed_lost']}), {verdict}.")


SENTENCES = {"chance-record": chance_record, "chance-rated": chance_rated,
             "blank-races": blank_races, "shadow-tally": shadow_tally}


def replace_block(text, name, figs=None):
    """Rewrite one marked paragraph; leave the rest of the README alone."""
    figs = collect() if figs is None else figs
    start, end = _markers(name)
    i = text.find(start)
    j = text.find(end, i + 1)
    if i < 0 or j < 0:
        raise ValueError(f"README has no {start} ... {end} pair")
    body = textwrap.fill(SENTENCES[name](figs[name]), width=78,
                         break_on_hyphens=False)
    return (text[:i] + start + "\n" + body + "\n" + end
            + text[j + len(end):])


def replace_all(text, figs=None):
    """Rewrite every marked block in `text`."""
    figs = collect() if figs is None else figs
    for name in BLOCKS:
        text = replace_block(text, name, figs)
    return text


def stale(text, figs=None):
    """True when a committed block is not what a render would write."""
    return replace_all(text, figs) != text


def write(path=README, figs=None):
    """Rewrite the README in place; True when something changed."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = replace_all(text, figs)
    if new == text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return True


def main(argv):
    if "--write" in argv:
        print("rewrote the README figures" if write()
              else "the README figures are already current")
        return 0
    if argv:
        print(f"unknown option: {' '.join(argv)}; only --write is supported")
        return 2
    figs = collect()
    for name in BLOCKS:
        print("* " + textwrap.fill(SENTENCES[name](figs[name]), width=78,
                                   subsequent_indent="  "))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
