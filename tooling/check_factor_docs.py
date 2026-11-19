#!/usr/bin/env python3
"""Verify that the rating documentation matches the real RATED_FACTORS array.

The source of truth is `var RATED_FACTORS` in index.html: the six inputs that
every rated race must carry for every runner. This check derives the input
count and the nominal weight, then checks the README, its JavaScript block, the
Method tab and the served dist copy.

The archived `ice/` pages keep their own older `FACTORS` array, so they are
checked against themselves (their own README and Method tab) rather than
against the main page.

    python3 tooling/check_factor_docs.py

Exit status is 0 when everything agrees, 1 otherwise.
"""
import re
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


def parse_array(html, name, source):
    match = re.search(r"\b(?:var|const|let)\s+%s\s*=\s*\[(.*?)\n\s*\];" % name,
                      html, re.S)
    if not match:
        fail(f"could not find {name} in {source}")
    factors = entries_in(match.group(1))
    if not factors:
        fail(f"{name} is empty or its entries could not be parsed in {source}")
    return factors


def check_rated_readme(readme, factors, source):
    count = len(factors)
    total = sum(weight for _, _, weight in factors)
    readme_text = normalise(readme)
    expected = {
        "factor count": f"{number_word(count).capitalize()} inputs",
        "full total": f"totalling {total}",
    }
    for label, phrase in expected.items():
        if phrase not in readme_text:
            fail(f"{source} is missing the {label} statement: {phrase!r}")

    blocks = re.findall(r"```js\s*\n(.*?)\n```", readme, re.S)
    code = next((b for b in blocks if "RATED_FACTORS" in b), None)
    if code is None:
        fail(f"{source} has no JavaScript RATED_FACTORS block")
    readme_factors = entries_in(code)
    if len(readme_factors) != count:
        fail(f"{source} code block has {len(readme_factors)} inputs; "
             f"RATED_FACTORS has {count}")
    if sum(weight for _, _, weight in readme_factors) != total:
        fail(f"{source} code block weights total "
             f"{sum(weight for _, _, weight in readme_factors)}; "
             f"RATED_FACTORS totals {total}")


def check_rated_method(html, factors, source):
    count = len(factors)
    total = sum(weight for _, _, weight in factors)
    method_match = re.search(
        r'<section class="panel" data-panel="method">(.*?)</section>',
        html, re.S)
    if not method_match:
        fail(f"could not find the Method tab in {source}")
    method = method_match.group(1)
    method_text = normalise(re.sub(r"<[^>]+>", " ", method))
    claimed = f"{number_word(count)} entries and {total} nominal weight"
    if claimed not in method_text:
        fail(f"{source} Method tab is missing: {claimed!r}")

    labels = [unescape(re.sub(r"<[^>]+>", "", value)).strip()
              for value in re.findall(r"<dt>(.*?)</dt>", method, re.S)]
    method_labels = []
    method_weights = []
    for label in labels:
        weight_match = re.search(r"\((\d+)\)$", label)
        if not weight_match:
            fail(f"{source} Method tab entry has no nominal weight: {label!r}")
        method_labels.append(re.sub(r"\s*\(\d+\)$", "", label).strip())
        method_weights.append(int(weight_match.group(1)))
    if method_labels != [label for _, label, _ in factors]:
        fail(f"{source} Method tab labels do not match RATED_FACTORS labels "
             "in order")
    if method_weights != [weight for _, _, weight in factors]:
        fail(f"{source} Method tab weights do not match RATED_FACTORS weights "
             "in order")


def check_archive(readme, html, factors, source):
    """The frozen archive keeps its own factor set; check it against itself."""
    count = len(factors)
    total = sum(weight for _, _, weight in factors)
    readme_text = normalise(readme)
    expected = {
        "factor count": f"{number_word(count).capitalize()} factors",
        "full total": f"full array totals {total}",
    }
    for label, phrase in expected.items():
        if phrase not in readme_text:
            fail(f"{source} is missing the {label} statement: {phrase!r}")

    method_match = re.search(
        r'<section class="panel" data-panel="method">(.*?)</section>',
        html, re.S)
    if not method_match:
        fail(f"could not find the Method tab in {source}")
    method = method_match.group(1)
    method_text = normalise(re.sub(r"<[^>]+>", " ", method))
    claimed = f"{number_word(count)} entries and {total} nominal weight"
    if claimed not in method_text:
        fail(f"{source} Method tab is missing: {claimed!r}")


def main():
    index_path = ROOT / "index.html"
    readme_path = ROOT / "README.md"
    dist_path = ROOT / "dist" / "public" / "index.html"
    archive_index_path = ROOT / "ice" / "index-all-regions.html"
    archive_readme_path = ROOT / "ice" / "README-all-regions.md"
    for path in (index_path, readme_path, dist_path,
                 archive_index_path, archive_readme_path):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")

    index = index_path.read_text(encoding="utf-8")
    readme = readme_path.read_text(encoding="utf-8")
    factors = parse_array(index, "RATED_FACTORS", "index.html")
    count = len(factors)
    total = sum(weight for _, _, weight in factors)

    check_rated_readme(readme, factors, "README.md")
    check_rated_method(index, factors, "index.html")

    if index_path.read_bytes() != dist_path.read_bytes():
        fail("dist/public/index.html does not match index.html")

    archive_index = archive_index_path.read_text(encoding="utf-8")
    archive_readme = archive_readme_path.read_text(encoding="utf-8")
    archive_factors = parse_array(archive_index, "FACTORS",
                                  "ice/index-all-regions.html")
    check_archive(archive_readme, archive_index, archive_factors,
                  "ice/README-all-regions.md")

    print(f"factor docs ok: {count} rated inputs, {total} nominal; "
          f"archive {len(archive_factors)} factors")


if __name__ == "__main__":
    main()
