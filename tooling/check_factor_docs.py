#!/usr/bin/env python3
"""Verify that the factor documentation matches the real FACTORS array.

The source of truth is `var FACTORS` in index.html. This check derives the
factor count, the full nominal weight and the weight without Draw, then checks
the README, the README code block, the Method tab and the served dist copy.

    python3 tooling/check_factor_docs.py

Exit status is 0 when everything agrees, 1 otherwise.
"""
import re
import sys
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NUM_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty",
}


def fail(message):
    print("FAIL:", message)
    raise SystemExit(1)


def number_word(number):
    return NUM_WORDS.get(number, str(number))


def normalise(text):
    return " ".join(text.split())


def entries_in(text):
    pattern = r"\[\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(\d+)\s*\]"
    return [(key, label, int(weight))
            for key, label, weight in re.findall(pattern, text)]


def main():
    index_path = ROOT / "index.html"
    readme_path = ROOT / "README.md"
    dist_path = ROOT / "dist" / "public" / "index.html"
    for path in (index_path, readme_path, dist_path):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")

    index = index_path.read_text(encoding="utf-8")
    readme = readme_path.read_text(encoding="utf-8")

    match = re.search(r"\b(?:var|const|let)\s+FACTORS\s*=\s*\[(.*?)\n\s*\];",
                      index, re.S)
    if not match:
        fail("could not find the FACTORS declaration in index.html")
    factors = entries_in(match.group(1))
    if not factors:
        fail("FACTORS is empty or its entries could not be parsed")

    count = len(factors)
    total = sum(weight for _, _, weight in factors)
    draw = [weight for key, _, weight in factors if key == "draw"]
    if len(draw) != 1:
        fail(f"expected exactly one draw factor, found {len(draw)}")
    without_draw = total - draw[0]
    non_draw_count = count - 1

    readme_text = normalise(readme)
    expected = {
        "factor count": f"{number_word(count).capitalize()} factors",
        "full total": f"full array totals {total}",
        "GB/IE subtotal": (f"{number_word(non_draw_count)} GB/IE factors "
                           f"total {without_draw}"),
        "without Draw": f"{without_draw} without Draw",
    }
    for label, phrase in expected.items():
        if phrase not in readme_text:
            fail(f"README is missing the {label} statement: {phrase!r}")

    code_match = re.search(r"```js\s*\n(.*?)\n```", readme, re.S)
    if not code_match:
        fail("README has no JavaScript FACTORS block")
    readme_factors = entries_in(code_match.group(1))
    if len(readme_factors) != count:
        fail(f"README code block has {len(readme_factors)} factors; "
             f"FACTORS has {count}")
    if sum(weight for _, _, weight in readme_factors) != total:
        fail(f"README code block weights total "
             f"{sum(weight for _, _, weight in readme_factors)}; FACTORS totals {total}")

    method_match = re.search(
        r'<section class="panel" data-panel="method">(.*?)</section>',
        index, re.S)
    if not method_match:
        fail("could not find the Method tab in index.html")
    method = method_match.group(1)
    method_text = normalise(re.sub(r"<[^>]+>", " ", method))
    claimed = f"{number_word(count)} entries and {total} nominal weight"
    if claimed not in method_text:
        fail(f"Method tab is missing: {claimed!r}")

    labels = [unescape(re.sub(r"<[^>]+>", "", value)).strip()
              for value in re.findall(r"<dt>(.*?)</dt>", method, re.S)]
    method_labels = []
    method_weights = []
    for label in labels:
        weight_match = re.search(r"\((\d+)\)$", label)
        if not weight_match:
            fail(f"Method tab entry has no nominal weight: {label!r}")
        method_labels.append(re.sub(r"\s*\(\d+\)$", "", label).strip())
        method_weights.append(int(weight_match.group(1)))
    if method_labels != [label for _, label, _ in factors]:
        fail("Method tab labels do not match FACTORS labels in order")
    if method_weights != [weight for _, _, weight in factors]:
        fail("Method tab weights do not match FACTORS weights in order")

    if index_path.read_bytes() != dist_path.read_bytes():
        fail("dist/public/index.html does not match index.html")

    print(f"factor docs ok: {count} factors, {total} nominal, "
          f"{without_draw} without Draw")


if __name__ == "__main__":
    main()
