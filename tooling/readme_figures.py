#!/usr/bin/env python3
"""The README numbers a render already computes.

Six claims in the README repeat figures the stored data decides: the
twelve-factor chance record in `data/results`, the six-input re-score's chance
audit, the count of races with no stored chance, the market-heavy shadow tally,
the 0.04/0.0498 scale gap set against the interval each sample size would give,
and how much of the Australia/NZ snapshot carries each field. Each sits between
markers and is rebuilt from the same collector the page uses, so the README
cannot describe an older build than the page.

Nothing here changes a figure: every number is counted from the stored results,
the stored re-score, the archived cards, the Australia/NZ cards and the saved
A/B snapshots. The figures
that cannot be re-derived here -- the four-year fit itself, the whole-field
audit's archive CSV, the Kaggle and Betfair reports -- stay as typed prose
beside these blocks.

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
AU_CARDS = os.path.join(ROOT, "data", "au_cards.json")
README = os.path.join(ROOT, "README.md")

BLOCKS = ("chance-record", "chance-rated", "au-fields", "scale-sensitivity",
          "blank-races", "shadow-tally")
BULLETS = ("au-fields",)   # blocks that are a list item, not a paragraph


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
    gap = chance_audit.scale_gap()
    widths = chance_audit.half_widths(record)
    return {
        "chance-record": record,
        "chance-rated": chance_audit.collect_rated(),
        "scale-sensitivity": {"races": gap["races"], "gap": gap["gap"],
                              "decimals": gap["decimals"],
                              "early": widths[chance_audit.EARLY],
                              "decisive": widths[chance_audit.DECISIVE],
                              "early_n": chance_audit.EARLY,
                              "decisive_n": chance_audit.DECISIVE},
        "au-fields": au_coverage(),
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


def scale_sensitivity(figs):
    """The scale gap against the interval each decision sample would give."""
    if not figs["races"] or not figs["early"]:
        return ("No settled race can be re-scored for the 0.04 and 0.0498 "
                "scale gap yet, so the sample sizes are still estimates.")
    verdict = chance_audit.scale_verdict(figs["early"], figs["gap"])
    return (f"At {figs['early_n']} races the interval is about "
            f"+/-{figs['early']:.1f} points and the 0.04 and 0.0498 scales put "
            f"the same picks {chance_audit.gap_points(figs)} apart over the "
            f"{_count(figs['races'], 'archived race')} whose card still "
            f"reproduces its stored pick; {verdict}. At {figs['decisive_n']:,} "
            f"races it narrows to about +/-{figs['decisive']:.1f} points and the "
            f"question can be settled.")


def _present(value):
    """A stored field counts only when it carries something.

    An empty string is a blank; a form of "0" is a real entry (unplaced), so
    the test is emptiness, not falsiness.
    """
    return value is not None and str(value).strip() != ""


def au_coverage(cards=None):
    """How much of the Australia/NZ snapshot carries each field.

    Counted per card and per runner: a card is filled when at least one
    runner has form, weight and barrier, and a runner counts for a field only
    where that field is non-empty. A blank is never inferred.
    """
    if cards is None:
        try:
            with open(AU_CARDS, encoding="utf-8") as f:
                cards = json.load(f)
        except (OSError, ValueError):
            cards = []
    runners = [r for c in cards for r in (c.get("runners") or [])]

    def card_has(card, *keys):
        return all(any(_present(r.get(k)) for r in card.get("runners") or [])
                   for k in keys)

    figs = {"cards": len(cards),
            "filled": sum(1 for c in cards
                          if card_has(c, "form", "weight", "draw")),
            "runners": len(runners)}
    for field, key in (("form", "form"), ("weight", "weight"),
                       ("barrier", "draw"), ("rating", "or"),
                       ("price", "odds")):
        figs[field] = sum(1 for r in runners if _present(r.get(key)))
    figs["full"] = sum(
        1 for c in cards
        if c.get("runners") and all(_present(r.get("or"))
                                    and _present(r.get("odds"))
                                    for r in c["runners"]))
    figs["names_only"] = figs["cards"] - figs["filled"]
    return figs


def _cards(n):
    return f"{n} card{'' if n == 1 else 's'}"


def au_fields(figs):
    """What the stored Australia/NZ cards actually carry, field by field."""
    if not figs.get("cards"):
        return "No Australia/NZ card is stored yet."
    full = (f"{_cards(figs['full'])} "
            f"{'has' if figs['full'] == 1 else 'have'} both a rating and a "
            f"price for every runner")
    words = (f"**Australia/NZ now carry real fields, though not for every "
             f"runner.** {figs['filled']} of the {figs['cards']} cards "
             f"harvested from Punters' form guide carry form, weight and "
             f"barrier; across their {figs['runners']} runners "
             f"{figs['form']} show a form string, {figs['weight']} a weight, "
             f"{figs['barrier']} a barrier, {figs['rating']} a rating and "
             f"{figs['price']} a price, so only {full}.")
    if figs["names_only"]:
        words += (f" {_cards(figs['names_only'])} "
                  f"{'is' if figs['names_only'] == 1 else 'are'} "
                  f"names-only.")
    return words


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
             "au-fields": au_fields, "scale-sensitivity": scale_sensitivity,
             "blank-races": blank_races, "shadow-tally": shadow_tally}


def _wrap(name, text):
    """A block's text at the width the README uses.

    A bullet keeps its continuation lines at the bullet's own indent, so the
    item stays one list item when the README is rendered.
    """
    if name in BULLETS:
        return textwrap.fill("* " + text, width=78, subsequent_indent="  ",
                             break_on_hyphens=False)
    return textwrap.fill(text, width=78, break_on_hyphens=False)


def replace_block(text, name, figs=None):
    """Rewrite one marked paragraph; leave the rest of the README alone."""
    figs = collect() if figs is None else figs
    start, end = _markers(name)
    i = text.find(start)
    j = text.find(end, i + 1)
    if i < 0 or j < 0:
        raise ValueError(f"README has no {start} ... {end} pair")
    body = _wrap(name, SENTENCES[name](figs[name]))
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
