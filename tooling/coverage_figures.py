#!/usr/bin/env python3
"""The README's coverage paragraph, generated from the stored re-score.

The caveats section used to carry the figures as typed prose, so a settled day,
a fresh re-score or a price recovered from a stored copy left the README
describing an older build, and nothing noticed. The paragraph now sits between
two markers and is rebuilt from `data/results_rated` -- the same re-score the
ledgers and the screen panels print -- so the figures follow the data.

Nothing here scores a card: the numbers are counted from the stored re-score, so
a render writes exactly the same paragraph every time.

usage: python3 tooling/coverage_figures.py [--write]
"""
import glob
import json
import os
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "data", "results_rated")
README = os.path.join(ROOT, "README.md")

START = "<!-- coverage:start -->"
END = "<!-- coverage:end -->"

# Only British and Irish cards can carry the six inputs, so only they can be
# rated; any other region keeps its code rather than being given a name.
REGIONS = {"GB": "British", "IRE": "Irish"}

PROVENANCE = ("*Generated from `data/results_rated` by "
              "`tooling/coverage_figures.py`; `refresh.py --data-only` rewrites "
              "it, and the checks job fails when the committed README differs.*")


def collect(days=None):
    """Count the coverage figures from stored re-scores, or from `days` given.

    A race counts as settled whether or not it could be rated: the point of the
    figures is how much of the settled archive the strict model reaches. A rated
    race carries `price_source` only when the archived card had lost a price and
    a stored pre-off copy supplied it, so `restored` counts the races the
    restore is the reason we can rate.
    """
    if days is None:
        days = [json.load(open(path, encoding="utf-8"))
                for path in sorted(glob.glob(os.path.join(RESULTS, "*.json")))]
    settled = rated = winners = restored = 0
    regions = set()
    for got in days:
        for race in got.get("races") or []:
            settled += 1
            if race.get("state") != "rated":
                continue
            rated += 1
            winners += 1 if race.get("hit") else 0
            restored += 1 if race.get("price_source") else 0
            regions.add(race.get("region") or "unlabelled")
    return {"settled": settled, "rated": rated, "winners": winners,
            "restored": restored, "regions": sorted(regions)}


def _join(words):
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " or " + words[-1]


def sentence(figs):
    """The paragraph's first sentence, with the plural and regions it needs."""
    settled, rated = figs["settled"], figs["rated"]
    if not rated:
        return (f"Coverage at this build is 0 of {settled} settled races: no "
                f"settled race carries a six-input pick yet.")
    wins = figs["winners"]
    text = (f"Coverage at this build is {rated} of {settled} settled races "
            f"({wins} winner{'' if wins == 1 else 's'}), all on "
            f"{_join([REGIONS.get(c, c) for c in figs['regions']])} cards")
    if figs["restored"]:
        text += (f"; {figs['restored']} of those were held out by price alone "
                 f"and are rated only because every runner's price was found in "
                 f"a stored pre-off copy")
    return text + "."


def replace_block(text, figs=None):
    """Rewrite the marked paragraph in `text`, keeping the bullet's indent."""
    figs = collect() if figs is None else figs
    start = text.find(START)
    end = text.find(END, start + 1)
    if start < 0 or end < 0:
        raise ValueError(f"README has no {START} ... {END} pair")
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    body = "\n\n".join(
        textwrap.fill(part, width=78, initial_indent=indent,
                      subsequent_indent=indent)
        for part in (sentence(figs) + "\n\n" + PROVENANCE).split("\n\n"))
    return (text[:line_start] + indent + START + "\n" + body + "\n"
            + indent + END + text[end + len(END):])


def stale(text, figs=None):
    """True when the committed paragraph is not what a render would write."""
    return replace_block(text, figs) != text


def write(path=README, figs=None):
    """Rewrite the README in place; True when something changed."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = replace_block(text, figs)
    if new == text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return True


def main(argv):
    if "--write" in argv:
        print("rewrote the coverage paragraph" if write()
              else "the coverage paragraph is already current")
        return 0
    if argv:
        print(f"unknown option: {' '.join(argv)}; only --write is supported")
        return 2
    print(sentence(collect()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
