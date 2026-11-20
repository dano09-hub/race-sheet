#!/usr/bin/env python3
"""The README's favourite-split caveat, counted from the 12 November card.

The caveat is about one settled day, so unlike the coverage paragraph it is
pinned rather than following the archive: the figures describe the card the
caveat was written from and should not move when a later day settles. Both
sources are stored copies of that day -- `data/results/2026-11-12.json`, which
keeps the pick written before the off, and the starting prices in
`data/dataset.csv.gz`, where the shortest `dec` price in a field is the
favourite.

Nothing here scores a card, restores a price or fills a gap. A race with no
stored starting prices is an error rather than a race quietly left out of the
count, because a caveat that silently counts 20 of 21 races would be worse than
one that stops.

usage: python3 tooling/caveat_figures.py [--write]
"""
import csv
import gzip
import json
import os
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DAY = "2026-11-12"
RESULTS = os.path.join(ROOT, "data", "results", DAY + ".json")
DATASET = os.path.join(ROOT, "data", "dataset.csv.gz")
README = os.path.join(ROOT, "README.md")

START = "<!-- caveat:start -->"
END = "<!-- caveat:end -->"

PROVENANCE = (f"*Pinned to the card in `data/results/{DAY}.json` and generated "
              f"from its stored pre-race picks and that day's starting prices "
              f"in `data/dataset.csv.gz` by `tooling/caveat_figures.py`; "
              f"`--write` rewrites the block, and the checks job fails when the "
              f"committed README differs.*")


def _favourites(field):
    """Every runner priced at the field's shortest starting price.

    A tie at that price counts every runner at it: the card says they were
    joint favourites, and choosing one would invent a ranking the prices do
    not carry.
    """
    priced = [(float(row["dec"]), row["horse"])
              for row in field if row.get("dec")]
    if not priced:
        return set()
    shortest = min(price for price, _ in priced)
    return {horse for price, horse in priced if abs(price - shortest) < 1e-9}


def collect(picks=None, prices=None):
    """Count the caveat from the day's stored picks and starting prices.

    `picks` and `prices` are the day's rows when a caller already has them;
    by default both are read from the archive.
    """
    if picks is None:
        with open(RESULTS, encoding="utf-8") as f:
            picks = json.load(f)
    if prices is None:
        with gzip.open(DATASET, "rt", encoding="utf-8") as f:
            prices = [row for row in csv.DictReader(f)
                      if row.get("date") == DAY]
    fields = {}
    for row in prices:
        fields.setdefault((row["course"], row["time"]), []).append(row)
    figs = {"races": 0, "favourites": 0, "favourites_found": 0,
            "others": 0, "others_found": 0}
    for race in picks:
        where = (race["course"], race["time"])
        favourites = _favourites(fields.get(where) or [])
        if not favourites:
            raise ValueError(f"{DAY}: no starting prices for "
                             f"{where[0]} {where[1]}")
        side = "favourites" if race["winner"] in favourites else "others"
        figs["races"] += 1
        figs[side] += 1
        figs[side + "_found"] += race["pick"] == race["winner"]
    return figs


def sentence(figs):
    """The caveat's first claim: the pick on favourites and on the rest."""
    return (f"The model is a favourite-ranker. On this card it found "
            f"{figs['favourites_found']} of the {figs['favourites']} winning "
            f"favourites and {figs['others_found']} of the {figs['others']} "
            f"winners that weren't the favourite.")


def sample(figs):
    """The second claim: the size of the sample the split is measured on."""
    return (f"{figs['races']} races is a tiny sample. The weights were tuned "
            f"in-sample; treat any improvement of one or two winners as noise.")


def block(figs=None):
    """The two bullets and their provenance, as a render writes them."""
    figs = collect() if figs is None else figs
    return "\n".join([
        textwrap.fill("* " + sentence(figs), width=78,
                      subsequent_indent="  ", break_on_hyphens=False),
        textwrap.fill("* " + sample(figs), width=78,
                      subsequent_indent="  ", break_on_hyphens=False),
        "",
        textwrap.fill(PROVENANCE, width=78, initial_indent="  ",
                      subsequent_indent="  ", break_on_hyphens=False),
    ])


def replace_block(text, figs=None):
    """Rewrite the marked bullets in `text`, leaving the rest alone."""
    start = text.find(START)
    end = text.find(END, start + 1)
    if start < 0 or end < 0:
        raise ValueError(f"README has no {START} ... {END} pair")
    return (text[:start] + START + "\n" + block(figs) + "\n"
            + END + text[end + len(END):])


def stale(text, figs=None):
    """True when the committed block is not what a render would write."""
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
        print("rewrote the caveat block" if write()
              else "the caveat block is already current")
        return 0
    if argv:
        print(f"unknown option: {' '.join(argv)}; only --write is supported")
        return 2
    print(block(collect()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
