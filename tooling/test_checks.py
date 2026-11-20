#!/usr/bin/env python3
"""Fixture tests for the two blocks on the Results tab.

Covers the rating A/B tally (ab_tally.collect + refresh.ab_block_html) and the
market re-check (recheck_market.run + refresh.market_block_html). No network and
no race day needed: each case builds throwaway cards, snapshots and results in a
temp directory, then checks the numbers and the HTML they produce.

    python3 tooling/test_checks.py

Exit status is 0 when every check passes, 1 otherwise.
"""
import contextlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

TOOLING = Path(__file__).resolve().parent
ROOT = TOOLING.parent
sys.path.insert(0, str(TOOLING))

import ab_tally        # noqa: E402
import market_arm_day  # noqa: E402
import calibrate_share  # noqa: E402
import collect_eachway_prices as cep  # noqa: E402
import caveat_figures  # noqa: E402
import coverage_figures  # noqa: E402
import eachway         # noqa: E402
import fetch_formfav as ff  # noqa: E402
import fetch_results_sl as sl  # noqa: E402
import refresh         # noqa: E402
import ledger_summary as lsum  # noqa: E402
import parse_cards as pcards  # noqa: E402
import rescore_results  # noqa: E402
import screen_results  # noqa: E402
import recheck_market  # noqa: E402
import settle          # noqa: E402
import us_flags        # noqa: E402
import us_angles       # noqa: E402
import us_form         # noqa: E402
import us_form_day     # noqa: E402

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    flag = "  ok   " if cond else "  FAIL "
    print(flag + name + (("  -> " + str(detail)) if detail and not cond else ""))


def skip(name, why):
    SKIPPED.append(name)
    print("  skip " + name + "  -> " + why)


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _clock(i):
    return "%02d:%02d" % (13 + i // 60, i % 60)


def _write_day(root, date, races, with_hits, without_hits, changed):
    """A day whose winner is always horse W<i>, so hit counts are exact."""
    rows, results = [], []
    for i in range(races):
        rows.append({"course": "C", "time": _clock(i),
                     "withRating": "W%d" % i if i < with_hits else "L%d" % i,
                     "withoutRating": "W%d" % i if i < without_hits else "M%d" % i,
                     "changed": i < changed})
        results.append({"course": "C", "time": _clock(i), "winner": "W%d" % i})
    with open(Path(root, "data", "picks_ab", date + ".json"), "w") as f:
        json.dump({"date": date, "rows": rows}, f)
    with open(Path(root, "data", "results", date + ".json"), "w") as f:
        json.dump(results, f)


@contextlib.contextmanager
def project(days=(), pending=(), real_snapshot=False, alias_file=None):
    """A temp project root; days are (date, races, with, without, changed)."""
    with tempfile.TemporaryDirectory(prefix="ab-tally-test-") as d:
        os.makedirs(Path(d, "data", "picks_ab"))
        os.makedirs(Path(d, "data", "results"))
        for spec in days:
            _write_day(d, *spec)
        for date in pending:
            with open(Path(d, "data", "picks_ab", date + ".json"), "w") as f:
                json.dump({"date": date, "rows": [
                    {"course": "C", "time": "13:00", "withRating": "P",
                     "withoutRating": "Q", "changed": True}]}, f)
        if real_snapshot:
            src = Path(ROOT, "data", "picks_ab", "2026-11-14.json")
            if src.exists():
                shutil.copy(src, Path(d, "data", "picks_ab", "2026-11-14.json"))
        if alias_file:
            with open(Path(d, "data", "picks_ab", alias_file), "w") as f:
                json.dump({"date": "2026-11-14", "rows": [{"course": "x"}]}, f)
        old = ab_tally.ROOT
        ab_tally.ROOT = d
        try:
            yield d
        finally:
            ab_tally.ROOT = old



CARDS = [
    {"course": "Cheltenham", "time": "13:10",
     "runners": [{"horse": "Alpha", "odds": ""},
                 {"horse": "O'Brien's Star (IRE)", "odds": ""}]},
    {"course": "Navan", "time": "14:00",
     "runners": [{"horse": "Bravo", "odds": ""}, {"horse": "Charlie", "odds": ""}]},
    {"course": "Uttoxeter", "time": "15:00", "runners": [{"horse": "Delta", "odds": ""}]},
]
SL = [
    {"course": "cheltenham", "time": "13:10",
     "runners": [{"horse": "alpha", "odds": "4/1"}, {"horse": "O'Briens Star", "odds": "2/1"}]},
    {"course": "NAVAN", "time": "14:00",
     "runners": [{"horse": "Bravo", "odds": "6/1"}, {"horse": "Charlie", "odds": "1/1"}]},
]
SL_ELSEWHERE = [{"course": "Ascot", "time": "13:10",
                 "runners": [{"horse": "Alpha", "odds": "4/1"}]}]
BEFORE = [{"course": "Cheltenham", "time": "13:10", "pick": "Alpha"},
          {"course": "Navan", "time": "14:00", "pick": "Charlie"}]


@contextlib.contextmanager
def market_project(cards, before=None):
    """A temp project with saved cards, an optional earlier pick snapshot."""
    with tempfile.TemporaryDirectory(prefix="market-test-") as d:
        os.makedirs(Path(d, "data", "picks_ab"))
        cards_path = Path(d, "cards.json")
        cards_path.write_text(json.dumps(cards))
        if before is not None:
            with open(Path(d, "data", "picks_ab", "test_2026-11-15.json"), "w") as f:
                json.dump({"date": "2026-11-15", "rows": before}, f)
        old = recheck_market.ROOT
        recheck_market.ROOT = d
        try:
            yield str(cards_path)
        finally:
            recheck_market.ROOT = old


@contextlib.contextmanager
def fake_scorer():
    """Stand-in for settle.score_cards: the shortest price becomes the pick.

    Keeps the test hermetic and lets the merge and survival logic be checked
    without running the page's JavaScript model.
    """
    calls = []

    def score(cards):
        calls.append(cards)
        out = []
        for c in cards:
            best = None
            for r in c.get("runners", []):
                try:
                    num, den = str(r.get("odds") or "").split("/")
                    val = float(num) / float(den)
                except (ValueError, ZeroDivisionError):
                    continue
                if best is None or val < best[0]:
                    best = (val, r["horse"])
            out.append({"course": c["course"], "time": c["time"],
                        "pick": best[1] if best else ""})
        return out

    old = settle.score_cards
    settle.score_cards = score
    try:
        yield calls
    finally:
        settle.score_cards = old


print("1. empty project")
with project():
    t = ab_tally.collect()
    check("no days, no pending", t["races"] == 0 and t["days"] == [] and t["pending"] == [])
    check("verdict says nothing settled", t["verdict"] == "No settled A/B days yet.", t["verdict"])
    check("panel says nothing settled",
          "No settled A/B days yet." in text_of(refresh.ab_block_html(t, "2026-11-13")))

print("2. pending day is named, not counted")
with project(pending=["2026-11-14"]):
    t = ab_tally.collect()
    check("pending listed", t["pending"] == ["2026-11-14"], t["pending"])
    check("pending not counted in races", t["races"] == 0)
    block = refresh.ab_block_html(t, "2026-11-13")
    check("pending shown even with nothing settled",
          "Waiting for results: 2026-11-14" in text_of(block))

print("3. two days, known hits")
with project(days=[("2026-11-10", 4, 3, 2, 4), ("2026-11-11", 2, 0, 1, 1)]):
    t = ab_tally.collect()
    check("races 6", t["races"] == 6, t["races"])
    check("with 3", t["with"] == 3, t["with"])
    check("without 3", t["without"] == 3, t["without"])
    check("changed 5", t["changed"] == 5, t["changed"])
    check("changed won 1 lost 1", (t["changed_won"], t["changed_lost"]) == (1, 1),
          (t["changed_won"], t["changed_lost"]))
    check("one row per day", [x["date"] for x in t["days"]] == ["2026-11-10", "2026-11-11"])
    check("short sample verdict", t["verdict"].startswith("The bar was fixed in advance"), t["verdict"])
    check("shortfall maths 1994", "1994 more are needed" in t["verdict"], t["verdict"])
    html = refresh.ab_block_html(t, "2026-11-11")
    check("panel has both days", "2026-11-10" in html and "2026-11-11" in html)
    check("panel headline 50.0%", "50.0%" in text_of(html), text_of(html)[:120])
    check("one block only", html.count("AB_SL_START") == 1)

print("4. 300 races ahead is still short of the pre-registered bar")
with project(days=[("2026-11-0%d" % (i + 1), 60, 27, 15, 8) for i in range(5)]):
    t = ab_tally.collect()
    check("300 races", t["races"] == 300, t["races"])
    check("not enough at 300", t["enough"] is False, t["enough"])
    check("verdict holds the bar", t["verdict"].startswith("The bar was fixed in advance"), t["verdict"])
    check("lead reported as 20 points", "lead of +20.0 points" in t["verdict"], t["verdict"])
    check("CI with clears CI without", t["ci_with"][0] > t["ci_without"][1],
          "%s vs %s" % (t["ci_with"], t["ci_without"]))

print("4b. 2,000 races ahead -> the bar is met")
with project(days=[("2026-11-%02d" % (i + 1), 400, 280, 240, 40) for i in range(5)]):
    t = ab_tally.collect()
    check("2,000 races", t["races"] == 2000, t["races"])
    check("enough flag at the decision point", t["enough"] is True)
    check("verdict adopts", t["verdict"].startswith("The rating cleared the pre-registered bar"), t["verdict"])
    check("rule detail on the tally", t["rule"]["status"] == "adopt", t.get("rule"))

print("5. 2,000 races behind -> the arm is retired")
with project(days=[("2026-11-%02d" % (i + 1), 400, 240, 280, 40) for i in range(5)]):
    t = ab_tally.collect()
    check("verdict retires", t["verdict"].startswith("The rating missed the pre-registered bar"), t["verdict"])
    check("retired arm is not enough", t["enough"] is True and t["rule"]["status"] == "retire")

print("6. 2,000-race dead heat -> no adoption")
with project(days=[("2026-11-%02d" % (i + 1), 400, 240, 240, 40) for i in range(5)]):
    t = ab_tally.collect()
    check("verdict no separation at 2,000", "missed the pre-registered bar" in t["verdict"], t["verdict"])

print("6b. the pre-registered rule itself")
import shadow_rule
_d = shadow_rule.decide(123, 4, 3)
check("rule: nothing adopted before the decision point",
      _d["status"] == "not yet" and _d["need"] == 1877 and abs(_d["lead"] - 0.8) < 0.05, _d)
check("rule: exact p for 4 v 3 is a coin flip", abs(shadow_rule.mcnemar(4, 3) - 0.5) < 1e-12,
      shadow_rule.mcnemar(4, 3))
check("rule: exact p for 10 v 0 is 1/1024", abs(shadow_rule.mcnemar(10, 0) - 1 / 1024) < 1e-12,
      shadow_rule.mcnemar(10, 0))
check("rule: a 3-point lead at 2,000 races adopts",
      shadow_rule.decide(2000, 60, 0)["status"] == "adopt", shadow_rule.decide(2000, 60, 0))
check("rule: 1.5 points is below the floor",
      shadow_rule.decide(2000, 30, 0)["status"] == "retire", shadow_rule.decide(2000, 30, 0))
check("rule: a tie retires the arm",
      shadow_rule.decide(2000, 10, 10)["status"] == "retire", shadow_rule.decide(2000, 10, 10))
check("rule: sentence names the bar before the sample",
      "fixed in advance" in shadow_rule.sentence(_d, "rating"))

print("7. real 35-race snapshot, simulated winners")
with project(real_snapshot=True, alias_file="test_2026-11-14.json") as d:
    src = Path(d, "data", "picks_ab", "2026-11-14.json")
    if not src.exists():
        skip("real snapshot case", "data/picks_ab/2026-11-14.json is not in this checkout")
    else:
        snap = json.loads(src.read_text())
        expected_without = sum(1 for r in snap["rows"]
                               if r["withoutRating"] == r["withRating"])
        winners = [{"course": r["course"], "time": r["time"], "winner": r["withRating"]}
                   for r in snap["rows"]]
        with open(Path(d, "data", "results", "2026-11-14.json"), "w") as f:
            json.dump(winners, f)
        t = ab_tally.collect()
        check("real snapshot counted", t["races"] == len(snap["rows"]), t["races"])
        check("with-rating picks all hit", t["with"] == len(snap["rows"]), t["with"])
        check("without-rating hits match data", t["without"] == expected_without,
              "%s vs %s" % (t["without"], expected_without))
        check("pending cleared once settled", t["pending"] == [], t["pending"])
        block = refresh.ab_block_html(t, "2026-11-14")
        check("alias snapshot did not add a second pending entry",
              "Waiting for results" not in text_of(block), text_of(block)[-120:])
        again = re.sub(r"<!-- AB_SL_START -->.*?<!-- AB_SL_END -->", block,
                       block, count=1, flags=re.S)
        check("replacement is idempotent", again.count("AB_SL_START") == 1)

print("8. escaping")
toy = {"races": 1, "with": 1, "without": 0, "changed": 1, "changed_won": 1,
       "changed_lost": 0,
       "days": [{"date": "2026-11-12", "races": 1, "with": 1, "without": 0, "changed": 1}],
       "pending": [], "verdict": "<script>alert(1)</script>"}
block = refresh.ab_block_html(toy, "2026-11-12")
check("verdict escaped", "<script>" not in block and "&lt;script&gt;" in block)


print("9. market re-check: nothing saved yet")
res = recheck_market.run("2026-11-15", "/nonexistent/cards.json", sl_cards=[])
check("missing cards reported, not raised",
      res["ok"] is False and "no saved cards" in res["reason"], res["reason"])
check("missing-cards block shows the reason",
      "no saved cards" in text_of(refresh.market_block_html(res, "2026-11-15")))

print("10. market re-check: prices not published")
with market_project(CARDS) as path, fake_scorer():
    res = recheck_market.run("2026-11-15", path, sl_cards=[])
check("no SL cards -> pending reason",
      res["ok"] is False and "no prices to test with" in res["reason"], res["reason"])
with market_project(CARDS) as path, fake_scorer():
    res = recheck_market.run("2026-11-15", path, sl_cards=SL_ELSEWHERE)
check("cards but no name match -> priced 0",
      res["ok"] is False and res["priced"] == 0 and "no runner matched" in res["reason"],
      res["reason"])

print("11. market re-check: merge prices, then re-score")
with market_project(CARDS, BEFORE) as path, fake_scorer() as calls:
    res = recheck_market.run("2026-11-15", path, sl_cards=SL)
    scored = calls[0]
    disk = Path(path).read_text()
check("run reports ok", res["ok"] is True, res["reason"])
check("only races with prices are re-scored", len(res["rows"]) == 2, len(res["rows"]))
check("all matched runners priced", (res["priced"], res["runners"]) == (4, 4),
      (res["priced"], res["runners"]))
check("one pick kept, one changed", (res["kept"], res["changed"]) == (1, 1),
      (res["kept"], res["changed"]))
check("kept + changed + unknown == rows",
      res["kept"] + res["changed"] + sum(1 for r in res["rows"] if r["survives"] is None)
      == len(res["rows"]))
chel = [c for c in scored if c["course"] == "Cheltenham"][0]
star = [r for r in chel["runners"] if "Star" in r["horse"]][0]
check("apostrophe + (IRE) name still matched", star.get("odds") == "2/1", star.get("odds"))
moved = [r for r in res["rows"] if r["survives"] is False][0]
check("changed row carries before and after",
      (moved["before"], moved["after"]) == ("Alpha", "O'Brien's Star (IRE)"),
      (moved["before"], moved["after"]))
check("race with no prices is left out", all(r["course"] != "Uttoxeter" for r in res["rows"]),
      [r["course"] for r in res["rows"]])
check("saved cards on disk are not rewritten",
      all(not r["odds"] for c in json.loads(disk) for r in c["runners"]))

print("12. market re-check: panel wording")
block = refresh.market_block_html(res, "2026-11-15")
check("headline counts", "1 of 2" in text_of(block), text_of(block)[:140])
check("changed pick is bulleted", "<li>" in block and "Alpha" in block and "Star" in block)
check("panel uses the market marker", block.count("MARKET_SL_START") == 1)
check("panel names the day", "15 November" in text_of(block), text_of(block)[:80])
held_before = [{"course": "Cheltenham", "time": "13:10", "pick": "O'Brien's Star (IRE)"},
               {"course": "Navan", "time": "14:00", "pick": "Charlie"}]
with market_project(CARDS, held_before) as path, fake_scorer():
    held = recheck_market.run("2026-11-15", path, sl_cards=SL)
held_block = refresh.market_block_html(held, "2026-11-15")
check("everything held -> kept 2 changed 0", (held["kept"], held["changed"]) == (2, 0))
check("everything held -> says so", "Every pick held." in text_of(held_block)
      and "<li>" not in held_block and "2 of 2" in text_of(held_block))

print("13. market re-check: no earlier picks, warning, escaping")
with market_project(CARDS) as path, fake_scorer():
    fresh = recheck_market.run("2026-11-15", path, sl_cards=SL)
check("nothing saved -> every row survives None",
      all(r["survives"] is None for r in fresh["rows"]))
check("banner admits there is nothing to compare",
      "no earlier picks saved" in recheck_market.banner(fresh),
      recheck_market.banner(fresh)[:160])
check("banner names the changed pick",
      "SURVIVED" in recheck_market.banner(res) and "changed: Cheltenham 13:10" in recheck_market.banner(res))
check("pending reason is escaped",
      "<b>" not in refresh.market_block_html(
          {"ok": False, "reason": "no prices for <b>14 Nov</b>"}, "2026-11-15"))
check("no queued check says so",
      "No re-check queued" in text_of(refresh.market_block_html(None, "2026-11-15")))


print("14. refresh gate")
with tempfile.TemporaryDirectory(prefix="gate-test-") as d:
    os.makedirs(Path(d, "data"))
    old_root, old_run, old_checks = refresh.ROOT, refresh.run_checks, refresh.CHECKS
    refresh.ROOT = d

    def must_not_run():
        raise AssertionError("run_checks called despite --skip-checks")

    refresh.run_checks = must_not_run
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = refresh.preflight(True)
    finally:
        refresh.run_checks = old_run
    log = Path(d, "data", "checks_skipped.log")
    check("skip lets the build continue", rc is None)
    check("skip is loud", "CHECKS SKIPPED" in out.getvalue(), out.getvalue()[:80])
    check("skip is recorded", log.exists() and "--skip-checks" in log.read_text(),
          log.read_text() if log.exists() else "no log written")
    check("skip does not run the suite", "AssertionError" not in out.getvalue())

    fail = Path(d, "fail_checks.py")
    fail.write_text("print('FAIL demo')\nimport sys\nsys.exit(1)\n")
    refresh.CHECKS = str(fail)
    with contextlib.redirect_stdout(io.StringIO()):
        check("failing suite stops with exit 3", refresh.preflight() == 3)
    good = Path(d, "ok_checks.py")
    good.write_text("print('passed 1, failed 0')\n")
    refresh.CHECKS = str(good)
    with contextlib.redirect_stdout(io.StringIO()):
        check("passing suite lets the build through", refresh.preflight() is None)

    refresh.ROOT, refresh.CHECKS = old_root, old_checks

print("15. --skip-checks reaches the gate")
seen = {}


def spy_preflight(skip_checks=False):
    seen["skip"] = skip_checks
    return 3


old_preflight, old_argv = refresh.preflight, sys.argv
refresh.preflight = spy_preflight
sys.argv = ["refresh.py", "--skip-checks", "--days=3"]
try:
    rc = refresh.main()
finally:
    refresh.preflight, sys.argv = old_preflight, old_argv
check("main forwards --skip-checks", seen.get("skip") is True, seen)
check("main returns the gate's exit code", rc == 3, rc)

print("16. archive files every meeting under the day it runs")
atmp = tempfile.mkdtemp(prefix="archive-")
old_root = refresh.ROOT
refresh.ROOT = atmp
try:
    with contextlib.redirect_stdout(io.StringIO()):
        refresh.archive("2026-11-13", {
            "GB": [{"id": "a", "course": "Cheltenham", "time": "13:10", "day": "2026-11-13", "race": "R1", "runners": []}],
            "GB_tomorrow": [{"id": "b", "course": "Navan", "time": "11:52", "day": "2026-11-14", "race": "R2", "runners": []}],
        })
        refresh.archive("2026-11-13", {
            "US": [{"id": "c", "course": "Belmont Park", "time": "18:00", "day": "2026-11-13", "race": "R3", "runners": []}],
            "GB": [{"id": "a", "course": "Cheltenham", "time": "13:10", "day": "2026-11-13", "race": "R1", "runners": [], "note": "prices added"}],
        })
        snap_first = json.load(open(os.path.join(atmp, "data", "cards", "2026-11-13.json"), encoding="utf-8"))
        refresh.archive("2026-11-13", {
            "US": [{"id": "c", "course": "Belmont Park", "time": "18:00", "day": "2026-11-13", "race": "r3", "runners": [], "note": "case refresh"}],
        })
    day13 = json.load(open(os.path.join(atmp, "data", "cards", "by_date", "2026-11-13.json"), encoding="utf-8"))
    day14 = json.load(open(os.path.join(atmp, "data", "cards", "by_date", "2026-11-14.json"), encoding="utf-8"))
    snap = json.load(open(os.path.join(atmp, "data", "cards", "2026-11-13.json"), encoding="utf-8"))
    check("later run keeps meetings an earlier run archived", "GB" in day13 and "US" in day13, list(day13))
    check("repeat run does not duplicate a race", len(day13.get("GB", [])) == 1, len(day13.get("GB", [])))
    check("repeat run refreshes the stored card", day13["GB"][0].get("note") == "prices added", day13["GB"][0].get("note"))
    check("US case variant does not duplicate a race", len(day13.get("US", [])) == 1, len(day13.get("US", [])))
    check("US case variant refreshes the stored card",
          day13["US"][0].get("note") == "case refresh", day13["US"][0].get("note"))
    check("US archive key treats 6 f and 6F as the same race",
          refresh._archive_key({"course": "Zia Park", "time": "20:53", "race": "6 f, Dirt"}, "US")
          == refresh._archive_key({"course": "Zia Park", "time": "20:53", "race": "6F, Dirt"}, "US"),
          refresh._archive_key({"course": "Zia Park", "time": "20:53", "race": "6 f, Dirt"}, "US"))
    check("tomorrow's GB card is filed under the day it runs",
          len(day14.get("GB", [])) == 1 and day14["GB"][0]["course"] == "Navan", day14.get("GB"))
    check("dated snapshot still written for the multi-region run",
          set(snap_first) == {"GB", "US"}, list(snap_first))
    check("dated snapshot for the later run has its own regions", set(snap) == {"US"}, list(snap))
finally:
    refresh.ROOT = old_root
    shutil.rmtree(atmp, ignore_errors=True)

print("16b. US by-date archives have no duplicate normalized races")
bdir = os.path.join(refresh.ROOT, "data", "cards", "by_date")
dupes = []
archive_files = 0
if os.path.isdir(bdir):
    for name in sorted(os.listdir(bdir)):
        if not name.endswith(".json"):
            continue
        archive_files += 1
        path = os.path.join(bdir, name)
        try:
            with open(path, encoding="utf-8") as f:
                day = json.load(f)
        except (OSError, ValueError):
            continue
        cards = day.get("US", []) if isinstance(day, dict) else []
        if not isinstance(cards, list):
            continue
        seen = {}
        for card in cards:
            key = refresh._archive_key(card, "US")
            if key in seen:
                dupes.append((name, key, seen[key].get("id"), card.get("id")))
            else:
                seen[key] = card
check("US by-date archive files were found", archive_files > 0, bdir)
check("US by-date archives contain no duplicate normalized races", not dupes, dupes[:3])

print("16c. every settled pick winner maps to an archived card")
results_dir = os.path.join(refresh.ROOT, "data", "results")
picked_rows = 0
card_not_kept = 0
unmapped = []
result_files = 0
if os.path.isdir(results_dir):
    for name in sorted(os.listdir(results_dir)):
        if not name.endswith(".json"):
            continue
        result_files += 1
        path = os.path.join(results_dir, name)
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, dict):
            rows = rows.get("races", [])
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        cards_path = os.path.join(bdir, name)
        card_keys = set()
        if os.path.exists(cards_path):
            try:
                with open(cards_path, encoding="utf-8") as f:
                    day = json.load(f)
            except (OSError, ValueError):
                day = {}
            if isinstance(day, dict):
                for cards in day.values():
                    if not isinstance(cards, list):
                        continue
                    for card in cards:
                        course = refresh._result_name(card.get("course"))
                        for runner in card.get("runners") or []:
                            card_keys.add((course, refresh._result_name(runner.get("horse"))))
        for row in rows:
            if not row.get("pick"):
                card_not_kept += 1
                continue
            picked_rows += 1
            key = (refresh._result_name(row.get("course")),
                   refresh._result_name(row.get("winner")))
            if key not in card_keys:
                unmapped.append((name, row.get("course"), row.get("winner")))
check("settled result files were found", result_files > 0, results_dir)
check("settled result picks were found", picked_rows > 0, picked_rows)
check("every settled result with a pick maps to an archived card", not unmapped, unmapped[:5])
print(f"  checked {picked_rows} picked result rows; "
      f"{card_not_kept} unpicked card-not-kept rows were left out")

print("16d. all-results audit of cards not kept")
# These are historical result rows where no card was archived.  They were
# never settled with a pick; list them explicitly so a future gap can fail
# without pretending the known 13 Nov rows are model-settled.
known_gap_courses = {
    ("2026-11-13", "dundalk"),
    ("2026-11-13", "sedgefield"),
    ("2026-11-13", "southwell"),
}
known_gaps, unexpected_gaps = [], []
all_result_rows = 0
if os.path.isdir(results_dir):
    for name in sorted(os.listdir(results_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(results_dir, name), encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, dict):
            rows = rows.get("races", [])
        cards_path = os.path.join(bdir, name)
        card_keys = set()
        if os.path.exists(cards_path):
            try:
                with open(cards_path, encoding="utf-8") as f:
                    day = json.load(f)
            except (OSError, ValueError):
                day = {}
            if isinstance(day, dict):
                for cards in day.values():
                    if not isinstance(cards, list):
                        continue
                    for card in cards:
                        course = refresh._result_name(card.get("course"))
                        for runner in card.get("runners") or []:
                            card_keys.add((course, refresh._result_name(runner.get("horse"))))
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            all_result_rows += 1
            course = refresh._result_name(row.get("course"))
            key = (course, refresh._result_name(row.get("winner")))
            if key in card_keys:
                continue
            gap = {"date": name[:-5], "course": course,
                   "winner": key[1], "picked": bool(row.get("pick"))}
            if not gap["picked"] and (gap["date"], course) in known_gap_courses:
                known_gaps.append(gap)
            else:
                unexpected_gaps.append(gap)
check("all result rows were audit-scanned", all_result_rows > 0, all_result_rows)
check("no unexpected result rows lack an archived card", not unexpected_gaps, unexpected_gaps[:5])
check("the known 13 Nov card gaps are still unpicked",
      all(not g["picked"] for g in known_gaps), known_gaps[:3])
check("the known 13 Nov card gap set is exactly 20 rows", len(known_gaps) == 20, len(known_gaps))
print(f"  all-result audit: {all_result_rows} rows; {len(known_gaps)} known 13 Nov "
      f"card gaps, {len(unexpected_gaps)} unexpected")

print("17. settled picks survive a results re-scrape")
old_rows = [{"course": "Cheltenham", "time": "14:55", "winner": "Thunder Rock",
             "pick": "Mahons Glory", "hit": False, "pick_finish": "3rd"}]
new_rows = [{"course": "Cheltenham", "time": "14:55", "winner": "Thunder Rock"},
            {"course": "Dundalk", "time": "19:00", "winner": "Some Horse"}]
kept = refresh.carry_picks(old_rows, new_rows)
check("carry keeps the pre-race pick", new_rows[0].get("pick") == "Mahons Glory", new_rows[0])
check("carry keeps the finish and fate",
      new_rows[0].get("pick_finish") == "3rd" and new_rows[0].get("hit") is False, new_rows[0])
check("a genuinely new race stays unsettled", not new_rows[1].get("pick"), new_rows[1])
check("carry reports one race kept", kept == 1, kept)
fallback = [{"course": "Cheltenham", "time": "", "winner": "Thunder Rock"}]
refresh.carry_picks(old_rows, fallback)
check("carry falls back to course and winner when the time is missing",
      fallback[0].get("pick") == "Mahons Glory", fallback[0])

print("18. settle.py never rewrites a stored pick")
stmp = tempfile.mkdtemp(prefix="settle-")
old_root = settle.ROOT
old_cards, old_score = settle._cards_for, settle.score_cards
settle.ROOT = stmp
try:
    os.makedirs(os.path.join(stmp, "data", "results"))
    rpath = os.path.join(stmp, "data", "results", "2026-11-13.json")
    json.dump([{"course": "Cheltenham", "time": "14:55", "race": "R", "winner": "Thunder Rock",
                "pick": "Mahons Glory", "hit": False}], open(rpath, "w"))
    settle._cards_for = lambda d: [{"course": "Cheltenham", "time": "14:55",
                                    "runners": [{"horse": "Thunder Rock"}]}]
    settle.score_cards = lambda cards: [{"course": "Cheltenham", "time": "14:55", "pick": "Thunder Rock"}]
    with contextlib.redirect_stdout(io.StringIO()):
        settle.main("2026-11-13")
    kept_rows = json.load(open(rpath))
    check("existing pick is untouched", kept_rows[0]["pick"] == "Mahons Glory", kept_rows[0]["pick"])
    check("existing fate is untouched", kept_rows[0].get("hit") is False, kept_rows[0].get("hit"))
finally:
    settle.ROOT = old_root
    settle._cards_for, settle.score_cards = old_cards, old_score
    shutil.rmtree(stmp, ignore_errors=True)

print("19. market-favourite baseline")
check("fractional price parsed", ab_tally.decimal_odds("5/2") == 3.5, ab_tally.decimal_odds("5/2"))
check("decimal price parsed", ab_tally.decimal_odds("2.60") == 2.60, ab_tally.decimal_odds("2.60"))
check("evens parsed", ab_tally.decimal_odds("EVS") == 2.0, ab_tally.decimal_odds("EVS"))
check("blank odds ignored", ab_tally.decimal_odds("") is None, ab_tally.decimal_odds(""))
check("non-price ignored", ab_tally.decimal_odds("SP") is None, ab_tally.decimal_odds("SP"))

btmp = tempfile.mkdtemp(prefix="baseline-")
old_root = ab_tally.ROOT
ab_tally.ROOT = btmp
try:
    for sub in (("data", "picks_ab"), ("data", "results"), ("data", "cards", "by_date")):
        os.makedirs(os.path.join(btmp, *sub))
    snap = {"date": "2026-11-12", "rows": [
        {"course": "C", "time": "13:00", "withRating": "Alpha", "withoutRating": "Bravo", "changed": True},
        {"course": "C", "time": "13:10", "withRating": "Bravo", "withoutRating": "Bravo", "changed": False}]}
    json.dump(snap, open(os.path.join(btmp, "data", "picks_ab", "2026-11-12.json"), "w"))
    results = [
        {"course": "C", "time": "13:00", "winner": "Alpha", "pick": "Bravo", "hit": False},
        {"course": "C", "time": "13:10", "winner": "Bravo", "pick": "Bravo", "hit": True}]
    json.dump(results, open(os.path.join(btmp, "data", "results", "2026-11-12.json"), "w"))
    cards = {"GB": [
        {"course": "C", "time": "13:00", "runners": [
            {"horse": "Alpha", "odds": "2/1"}, {"horse": "Bravo", "odds": "5/1"}, {"horse": "Charlie", "odds": "10/1"}]},
        {"course": "C", "time": "13:10", "runners": [
            {"horse": "Alpha", "odds": "2/1"}, {"horse": "Bravo", "odds": "5/1"}, {"horse": "Charlie", "odds": "10/1"}]}]}
    json.dump(cards, open(os.path.join(btmp, "data", "cards", "by_date", "2026-11-12.json"), "w"))
    t = ab_tally.collect()
    check("baseline covers races with a pick and a price", t["bench_races"] == 2, t["bench_races"])
    check("favourite win counted once", t["fav_wins"] == 1, t["fav_wins"])
    check("our pick win counted once", t["pick_hits"] == 1, t["pick_hits"])
    check("backing the favourite counted", t["pick_fav"] == 0, t["pick_fav"])
    html = refresh.ab_block_html(t, "2026-11-12")
    txt = text_of(html)
    check("panel shows the benchmark", "Benchmark on the same races" in txt, txt[:160])
    check("panel names the favourite total", "favourite won 1 of 2" in txt, txt[:220])

    tie = {"GB": [{"course": "C", "time": "13:20", "runners": [
        {"horse": "Alpha", "odds": "2/1"}, {"horse": "Bravo", "odds": "2/1"}]}]}
    json.dump(tie, open(os.path.join(btmp, "data", "cards", "by_date", "2026-11-12.json"), "w"))
    favs = ab_tally.card_favourites("2026-11-12")
    check("joint favourites are not called a market pick", ("c", "13:20") not in favs, favs)
finally:
    ab_tally.ROOT = old_root
    shutil.rmtree(btmp, ignore_errors=True)

print("20. each-way terms and EV")
check("5-7 runners: 2 places at 1/4", eachway.place_terms(7, False) == (2, 0.25),
      eachway.place_terms(7, False))
check("8-11 handicap: 3 places at 1/5", eachway.place_terms(10, True) == (3, 0.20),
      eachway.place_terms(10, True))
check("12-15 handicap: 3 places at 1/4", eachway.place_terms(14, True) == (3, 0.25),
      eachway.place_terms(14, True))
check("16+ handicap: 4 places at 1/4", eachway.place_terms(16, True) == (4, 0.25),
      eachway.place_terms(16, True))
check("8+ non-handicap: 3 places at 1/5", eachway.place_terms(16, False) == (3, 0.20),
      eachway.place_terms(16, False))
check("under 5 runners is win-only", eachway.place_terms(4, True) == (0, 0.0),
      eachway.place_terms(4, True))
check("8/1 at 1/4 gives 3.0 place decimal", eachway.place_decimal(9.0, 0.25) == 3.0,
      eachway.place_decimal(9.0, 0.25))
check("each-way EV example is +0.13",
      abs(eachway.each_way_ev(0.12, 0.35, 9.0, 0.25) - 0.13) < 1e-12,
      eachway.each_way_ev(0.12, 0.35, 9.0, 0.25))
check("place-only EV example is +0.05",
      abs(eachway.place_only_ev(0.35, 9.0, 0.25) - 0.05) < 1e-12,
      eachway.place_only_ev(0.35, 9.0, 0.25))
check("win-only EV is None", eachway.each_way_ev(0.2, 0.5, 3.0, 0.0) is None)

print("21. each-way price snapshot")
_snap_cards = [{
    "region": "GB", "course": "C", "time": "13:00", "race": "Handicap",
    "day": "2026-01-01",
    "runners": [{"horse": "H%d" % i, "odds": "8/1"} for i in range(10)],
}]
_snap = cep.collect(_snap_cards, "2026-01-01", "test")
check("collector emits one row per priced runner", len(_snap) == 10, len(_snap))
check("10-runner handicap uses 3 places at 1/5",
      _snap and (_snap[0]["places"], _snap[0]["place_fraction"]) == (3, 0.20),
      _snap[0] if _snap else None)
check("8/1 with 1/5 terms derives 2.60 place odds",
      _snap and abs(_snap[0]["place_odds"] - 2.6) < 1e-12,
      _snap[0]["place_odds"] if _snap else None)
check("derived price is labelled",
      _snap and _snap[0]["place_odds_kind"] == "derived_standard_terms",
      _snap[0] if _snap else None)
check("foreign cards are ignored",
      cep.collect([{"region": "US", "runners": [{"horse": "X", "odds": "2"}]}],
                  "2026-01-01", "test") == [])
check("win-only cards record no place price",
      (cep.collect([{"region": "GB", "runners": [
          {"horse": "X%d" % i, "odds": "2"} for i in range(4)]}],
          "2026-01-01", "test") or [{}])[0].get("place_odds") is None)
check("fractional and EVS odds parse",
      cep.parse_decimal("8/1") == 9.0 and cep.parse_decimal("EVS") == 2.0)

print("23. FormFav comparison")
check("FormFav normalises a horse name",
      ff.norm("O'Brien's Star (IRE)") == "obriens star", ff.norm("O'Brien's Star (IRE)"))
check("FormFav converts UTC to a BST race clock",
      ff.local_hhmm("2026-06-14T11:35:00Z", "Europe/London") == "12:35",
      ff.local_hhmm("2026-06-14T11:35:00Z", "Europe/London"))
_ff_meetings = [{"track": "Cheltenham", "slug": "cheltenham", "country": "gb",
                 "races": [{"raceNumber": 1, "startTime": "2026-11-14T12:35:00Z"}]}]
_ff_cards = [{"course": "Cheltenham", "time": "12:35", "runners": [{"horse": "Alpha"}]}]
_ff_pairs, _ff_unmatched = ff.match_cards(_ff_cards, _ff_meetings)
check("FormFav matches course and local clock",
      len(_ff_pairs) == 1 and not _ff_unmatched, (_ff_pairs, _ff_unmatched))

with tempfile.TemporaryDirectory(prefix="formfav-test-") as _d:
    os.makedirs(Path(_d, "data", "cards"))
    os.makedirs(Path(_d, "data", "picks_ab"))
    _card = {"id": "cheltenham-1235", "course": "Cheltenham", "time": "12:35",
             "day": "2026-11-14", "region": "GB", "race": "Test Race",
             "runners": [{"horse": "Alpha", "form": "123", "or": "100"}]}
    _path = Path(_d, "data", "cards", "2026-11-14.json")
    _path.write_text(json.dumps({"GB": [_card]}))
    _calls = []

    def _fake_get(path, params, key):
        _calls.append((path, params, key))
        if path == "/form/meetings":
            return {"date": "2026-11-14", "meetings": _ff_meetings}
        if path == "/form":
            return {"date": "2026-11-14", "track": "Cheltenham", "raceNumber": 1,
                    "runners": [{"name": "Alpha", "form": "111", "last20Starts": "111",
                                 "jockey": "J", "trainer": "T",
                                 "stats": {"overall": {"starts": 10, "wins": 4,
                                                       "places": 7, "winPercent": 0.4,
                                                       "placePercent": 0.7},
                                            "track": {"placePercent": 0.5},
                                            "distance": {"placePercent": 0.6}}}]}
        raise AssertionError("unexpected FormFav path " + path)

    _old_root = ff.ROOT
    ff.ROOT = _d
    try:
        _res = ff.collect_file(str(_path), "2026-11-14", key="test-key", getter=_fake_get)
    finally:
        ff.ROOT = _old_root
    check("FormFav comparison reports one matched race",
          _res.get("ok") and _res["races"] == 1 and _res["runners"] == 1, _res)
    check("FormFav sends the key in the header argument, not the URL",
          all(c[2] == "test-key" for c in _calls), _calls)
    _csv = Path(_d, "data", "picks_ab", "formfav_compare_2026-11-14.csv")
    _raw = Path(_d, "data", "cards", "2026-11-14_formfav.json")
    check("FormFav writes the comparison CSV", _csv.exists(), _csv)
    check("FormFav writes the raw response", _raw.exists(), _raw)
    check("FormFav CSV carries form and stats",
          _csv.exists() and all(x in _csv.read_text() for x in
                                ("Alpha", "111", "0.7", "0.5", "0.6")))
    check("FormFav raw response does not contain the key",
          _raw.exists() and "test-key" not in _raw.read_text())
    check("FormFav does not edit the card snapshot",
          json.loads(_path.read_text()) == {"GB": [_card]})

print("24. Pace experiment")
try:
    import backtest_pace as bp
    check("pace: made all is a front-runner", bp.style_of("made all, soon clear") == 3)
    check("pace: held-up run stays held-up despite a late lead",
          bp.style_of("held up in rear, headway to lead towards finish") == 0)
    check("pace: tracked leaders stays prominent",
          bp.style_of("tracked leaders, led 2f out") == 2)
    check("pace: mid-division stays mid", bp.style_of("mid-division, headway over 1f out") == 1)
    check("pace: prominent stays prominent", bp.style_of("prominent, outpaced after 11th") == 2)
    check("pace: non-race comment is missing", bp.style_of("self certificate") is None)
    _p = bp.pace_features([3, 2, 1], 0.5)
    check("pace: front-run rate uses prior runs",
          abs(_p["pace_front"] - 2 / 3) < 1e-9 and abs(_p["pace_led"] - 1 / 3) < 1e-9, _p)
    check("pace: no history is flagged missing",
          bp.pace_features([], 0.5)["pace_missing"] == 1)

    # Two races for one horse: the second race may use the first comment, but
    # must not use its own comment. This is the no-look-ahead check.
    with tempfile.TemporaryDirectory(prefix="pace-test-") as _d:
        _raw = Path(_d, "2026-01-01.json")
        _raw.write_text(json.dumps({"meetings": [{"course": "C", "races": [
            {"time": "13:00", "runners": [{"horse": "A", "comment": "made all"}]},
            {"time": "13:30", "runners": [{"horse": "A", "comment": "held up"}]},
        ]}]}))
        _old_glob = bp.RAW_GLOB
        bp.RAW_GLOB = str(_raw)
        try:
            _idx, _counts = bp.build_pace_index()
        finally:
            bp.RAW_GLOB = _old_glob
        _first = _idx[("2026-01-01", "c", "13:00", "a")]
        _second = _idx[("2026-01-01", "c", "13:30", "a")]
        check("pace: first run has no prior history", _first["pace_missing"] == 1, _first)
        check("pace: second run uses the first race, not its own comment",
              _second["pace_style"] == 3 and _second["pace_front"] == 1, _second)
        check("pace: causal fixture counts two races and two runners",
              _counts["races"] == 2 and _counts["runners"] == 2, dict(_counts))
except Exception as exc:
    check("pace experiment imports and parses", False, exc)

print("25. Course/going front-runner bias")
try:
    import backtest_course_bias as cb
    check("bias: going token strips the description",
          cb.going_token("Good (Watered)") == "GOOD", cb.going_token("Good (Watered)"))
    check("bias: blank going is missing", cb.going_token("") is None)
    check("bias: empty cell returns the global prior",
          cb.shrunk_excess(None, 0.1) == 0.1)
    _one = cb.shrunk_excess({"runs": 1, "wins": 1, "market": 0.2}, 0.1, alpha=20)
    check("bias: one-run cell is shrunk toward the global prior",
          0.1 < _one < 0.2, _one)
    check("bias: tenth place is not a winner",
          cb.pos_num("10th") == 10 and cb.pos_num("1st") == 1)

    with tempfile.TemporaryDirectory(prefix="bias-test-") as _d:
        _raw = Path(_d, "2026-01-01.json")
        _raw.write_text(json.dumps({"meetings": [{"course": "C", "races": [
            {"time": "13:00", "going": "GOOD", "runners": [
                {"horse": "A", "pos": "1st", "sp": "2/1"},
                {"horse": "B", "pos": "2nd", "sp": "3/1"}]},
            {"time": "13:30", "going": "GOOD", "runners": [
                {"horse": "A", "pos": "2nd", "sp": "2/1"},
                {"horse": "B", "pos": "1st", "sp": "3/1"}]},
        ]}]}))
        _pace = {
            ("2026-01-01", "c", "13:00", "a"): {"pace_led": 1.0},
            ("2026-01-01", "c", "13:00", "b"): {"pace_led": 0.0},
            ("2026-01-01", "c", "13:30", "a"): {"pace_led": 1.0},
            ("2026-01-01", "c", "13:30", "b"): {"pace_led": 0.0},
        }
        _old_glob = bp.RAW_GLOB
        bp.RAW_GLOB = str(_raw)
        try:
            _idx, _counts = cb.build_bias_index(_pace)
        finally:
            bp.RAW_GLOB = _old_glob
        check("bias: fixture marks the prior front-runner",
              _idx[("2026-01-01", "c", "13:00", "a")]["front"] == 1.0)
        check("bias: fixture updates two complete priced races",
              _counts["complete_races"] == 2, dict(_counts))
except Exception as exc:
    check("course/going bias imports and parses", False, exc)

print("26. Factor docs guard")
with tempfile.TemporaryDirectory(prefix="factor-docs-test-") as _d:
    _root = Path(_d)
    (_root / "tooling").mkdir()
    (_root / "dist" / "public").mkdir(parents=True)
    shutil.copyfile(ROOT / "index.html", _root / "index.html")
    shutil.copyfile(ROOT / "README.md", _root / "README.md")
    shutil.copyfile(ROOT / "dist" / "public" / "index.html",
                    _root / "dist" / "public" / "index.html")
    (_root / "ice").mkdir()
    shutil.copyfile(ROOT / "ice" / "index-all-regions.html",
                    _root / "ice" / "index-all-regions.html")
    shutil.copyfile(ROOT / "ice" / "README-all-regions.md",
                    _root / "ice" / "README-all-regions.md")
    _checker = _root / "tooling" / "check_factor_docs.py"
    shutil.copyfile(ROOT / "tooling" / "check_factor_docs.py", _checker)
    _ok = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                         capture_output=True, text=True)
    check("factor docs: baseline passes", _ok.returncode == 0,
          _ok.stdout + _ok.stderr)
    _readme = _root / "README.md"
    _readme_text = _readme.read_text(encoding="utf-8")
    _readme.write_text(
        _readme_text.replace("Six inputs", "Five inputs", 1),
        encoding="utf-8")
    _bad = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                          capture_output=True, text=True)
    _out = _bad.stdout + _bad.stderr
    check("factor docs: wrong documented count is rejected",
          _bad.returncode != 0 and "factor count" in _out, _out)
    _readme.write_text(
        _readme_text.replace("totalling 56", "totalling 60", 1),
        encoding="utf-8")
    _total_bad = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                                capture_output=True, text=True)
    _total_out = _total_bad.stdout + _total_bad.stderr
    check("factor docs: wrong documented total is rejected",
          _total_bad.returncode != 0 and "full total" in _total_out,
          _total_out)
    _readme.write_text(_readme_text, encoding="utf-8")
    _index = _root / "index.html"
    _index_text = _index.read_text(encoding="utf-8")
    _method_text = _index_text.replace("<dt>Weight (2)</dt>", "<dt>Weight (5)</dt>", 1)
    if _method_text == _index_text:
        check("factor docs: Method-tab weight fixture found", False,
              "Weight (2) not found")
    _index.write_text(_method_text, encoding="utf-8")
    _method_bad = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                                 capture_output=True, text=True)
    _method_out = _method_bad.stdout + _method_bad.stderr
    check("factor docs: wrong Method-tab weight is rejected",
          _method_bad.returncode != 0 and "Method tab weights" in _method_out,
          _method_out)
    _index.write_text(_index_text, encoding="utf-8")
    _archive_readme = _root / "ice" / "README-all-regions.md"
    _archive_readme_text = _archive_readme.read_text(encoding="utf-8")
    _archive_readme.write_text(
        _archive_readme_text.replace("Twelve factors", "Eleven factors", 1),
        encoding="utf-8")
    _archive_bad = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                                  capture_output=True, text=True)
    _archive_out = _archive_bad.stdout + _archive_bad.stderr
    check("factor docs: archived count drift is rejected",
          _archive_bad.returncode != 0
          and "ice/README-all-regions.md" in _archive_out
          and "factor count" in _archive_out,
          _archive_out)

print("27. placed status on settled results")
ptmp = tempfile.mkdtemp(prefix="placed-test-")
old_root = refresh.ROOT
refresh.ROOT = ptmp
try:
    os.makedirs(os.path.join(ptmp, "data", "results"))

    def _runner(pos, horse):
        return {"pos": pos, "horse": horse}

    full = {"meetings": [{"course": "C", "races": [
        {"time": "13:00", "title": "Handicap Hurdle",
         "runners": [_runner("%d" % i, "H%d" % i) for i in range(1, 17)]},
        {"time": "14:00", "title": "Novices' Hurdle",
         "runners": [_runner("%d" % i, "J%d" % i) for i in range(1, 8)]},
        {"time": "15:00", "title": "Selling Hurdle",
         "runners": [_runner("%d" % i, "K%d" % i) for i in range(1, 4)]},
    ]}]}
    with open(os.path.join(ptmp, "data", "2026-11-13.json"), "w") as f:
        json.dump(full, f)
    rows = [
        {"course": "C", "time": "13:00", "race": "13:00 C", "winner": "H1",
         "pick": "H4", "hit": False},
        {"course": "C", "time": "14:00", "race": "14:00 C", "winner": "J1",
         "pick": "J3", "hit": False},
        {"course": "C", "time": "15:00", "race": "15:00 C", "winner": "K1",
         "pick": "K1", "hit": True},
        {"course": "C", "time": "16:00", "race": "16:00 C", "winner": "X",
         "pick": "Y", "hit": False},
    ]
    annotated = refresh.annotate_result_rows("2026-11-13", rows)
    check("placed: three races matched", annotated == 3, annotated)
    check("placed: 16-runner handicap fourth is placed",
          rows[0].get("placed") is True and rows[0].get("place_places") == 4, rows[0])
    check("placed: 7-runner third misses two places",
          rows[1].get("placed") is False and rows[1].get("place_places") == 2, rows[1])
    check("placed: three-runner race is win only",
          rows[2].get("place_places") == 0, rows[2])
    check("placed: pick and win flag untouched",
          rows[0].get("pick") == "H4" and rows[0].get("hit") is False, rows[0])
    html = refresh.results_block("2026-11-13", rows)
    check("placed: Results column is rendered", "Placed?" in html, html[:200])
    check("placed: Yes, No and Win only are rendered",
          "placed yes" in html and "placed no" in html and "placed winonly" in html,
          html)
    check("A/B: race-label time fallback",
          ab_tally.time_key({"time": "", "race": "14:35 Cheltenham"}) == "14:35",
          ab_tally.time_key({"time": "", "race": "14:35 Cheltenham"}))

    check("US flags: form 2 is second", us_flags.last_run("233412") == "2nd",
          us_flags.last_run("233412"))
    check("US flags: pulled-up form is named", us_flags.last_run("P") == "Pulled up",
          us_flags.last_run("P"))
    check("US flags: no form is blank", us_flags.last_run("") == "")
    check("US flags: days since last run", us_flags.days_since("2026-09-25", "2026-11-16") == 52)
    check("US flags: rating rank with a tie",
          us_flags.rating_rank([{"horse": "A", "or": 90},
                                {"horse": "B", "or": 90},
                                {"horse": "C", "or": 80}], "C") == "3rd of 3",
          us_flags.rating_rank([{"horse": "A", "or": 90},
                                {"horse": "B", "or": 90},
                                {"horse": "C", "or": 80}], "C"))
    name, stats = us_flags.match_person({"R Rodriguez": {"sr30": 20, "r30": 10}},
                                        "Rudy R. Rodriguez")
    check("US flags: trainer initial is matched", name == "R Rodriguez" and stats["sr30"] == 20,
          (name, stats))
    name, stats = us_flags.match_person({"J Smith": {}, "Johnny Smith": {}}, "John Smith")
    check("US flags: ambiguous surname is rejected", name is None and stats is None,
          (name, stats))
    name, stats = us_flags.match_person({"M Ferraro": {"sr30": 12.5, "r30": 16},
                                         "Michael Ferraro": {"sr30": 9.1, "r30": 11}},
                                        "M Ferraro")
    check("US flags: exact name wins over surname variant",
          name == "M Ferraro" and stats["sr30"] == 12.5, (name, stats))

    with tempfile.TemporaryDirectory(prefix="us-flags-test-") as fd:
        def write(name, value):
            path = Path(fd, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        write("cards.json", {"US": [{
            "course": "Test Park", "time": "17:00", "race": "Test Stakes",
            "runners": [{"horse": "Horse A", "trainer": "Rudy R. Rodriguez",
                         "jockey": "J. M. Rohena", "form": "233", "draw": "3"}]}]})
        write("picks.json", {"rows": [{"course": "Test Park", "time": "17:00",
                                       "withRating": "Horse A"}]})
        write("raw.json", {"date": "2026-11-16", "meetings": [{
            "course": "Test Park", "races": [{
                "time": "17:00", "race": "Test Stakes",
                "runners": [
                    {"horse": "Horse A", "horse_id": 1, "trainer": "R Rodriguez",
                     "jockey": "J Rohena", "form": "233", "or": 90, "draw": 3},
                    {"horse": "Horse B", "horse_id": 2, "trainer": "Other",
                     "jockey": "Other", "form": "1", "or": 95, "draw": 1}]}]}]})
        write("inform.json", {"updated": "2026-11-09T04:01:23+00:00",
                              "trainers": {"R Rodriguez": {"sr30": 20, "r30": 10}},
                              "jockeys": {"J Rohena": {"sr30": 25, "r30": 4}},
                              "horses": {"1": {"last": "2026-09-25"}}})
        built = us_flags.build("2026-11-16", cards_path=str(Path(fd, "cards.json")),
                               picks_path=str(Path(fd, "picks.json")),
                               raw_path=str(Path(fd, "raw.json")),
                               inform_path=str(Path(fd, "inform.json")))
        row = built["rows"][0]
        check("US flags: build keeps the pick", row["pick"] == "Horse A", row)
        check("US flags: build ranks the runner", row["rank"] == "2nd of 2", row)
        check("US flags: build matches trainer and jockey",
              row["trainer_form"] == {"sr": 20, "runs": 10}
              and row["jockey_form"] == {"sr": 25, "runs": 4}, row)
        check("US flags: build gets last run and days",
              row["last_run"] == "2nd" and row["days"] == 52, row)
        html = refresh.us_flags_block_html(built, "2026-11-16")
        check("US flags: read-only tab is rendered",
              "US flags" in html and "Trainer 30d" in html and "Horse A" in html, html[:200])
        check("US flags: no scoring controls",
              "input" not in html.lower() and "button" not in html.lower(), html[:200])

    check("US angles: fair fraction converts to the standard ladder",
          us_angles.fair_fraction(15.0) == "14/1"
          and us_angles.fair_fraction(1.5) == "1/2", us_angles.fair_fraction(15.0))

    with tempfile.TemporaryDirectory(prefix="us-angles-test-") as ad:
        def awrite(name, value):
            path = Path(ad, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        awrite("cards.json", {"US": [
            {"course": "Alpha", "time": "17:00", "race": "Alpha Stakes",
             "runners": [
                 {"horse": "A", "odds": "2.00", "draw": "1", "form": "1",
                  "trainer": "T", "jockey": "J", "ts": "111", "or": "90"},
                 {"horse": "B", "odds": "3.00", "draw": "2", "form": "23",
                  "trainer": "T", "jockey": "J", "ts": "90", "or": "80"}]},
            {"course": "Beta", "time": "18:00", "race": "Beta Stakes",
             "runners": [{"horse": "C", "odds": "1.50", "draw": "3", "form": "2",
                          "trainer": "T", "jockey": "J", "ts": "95", "or": "70"},
                         {"horse": "D", "odds": "4.00", "draw": "1", "form": "0",
                          "trainer": "T", "jockey": "J", "ts": "80", "or": "60"}]},
        ]})
        awrite("angles.json", {"generated": "2026-11-16T12:00:00+00:00", "races": {
            "alpha|17:00": {"course": "Alpha", "off": "17:00", "name": "Alpha Stakes",
                            "surf": "dirt", "field": 2, "runners": {
                                "a": {"h": "A", "draw": 1, "odds": "2.00",
                                      "f": ["fav", "topor", "inside", "wonlto"],
                                      "c": [["wonlto", "Won last time"]]},
                                "b": {"h": "B", "draw": 2, "odds": "3.00",
                                      "f": ["wide", "plclto"], "c": []}}}}})
        awrite("picks.json", {"rows": [{"course": "Alpha", "time": "17:00",
                                        "withRating": "A"}]})
        awrite("inform.json", {"updated": "2026-11-09T04:01:23+00:00",
                               "trainers": {}, "jockeys": {}, "horses": {}})
        built = us_angles.build("2026-11-16", cards_path=str(Path(ad, "cards.json")),
                                angles_path=str(Path(ad, "angles.json")),
                                picks_path=str(Path(ad, "picks.json")),
                                inform_path=str(Path(ad, "inform.json")))
        check("US angles: card dedupe/race coverage",
              built["total_races"] == 2 and built["live_races"] == 1
              and built["card_only_races"] == 1, built)
        check("US angles: feed flags are carried",
              built["races"][0]["runners"][0]["flags"][0]["key"] == "fav"
              and built["races"][0]["runners"][1]["flags"][0]["key"] == "wide",
              built["races"][0]["runners"])
        check("US angles: card-only flags are derived",
              any(f["key"] == "fav" for f in built["races"][1]["runners"][0]["flags"]),
              built["races"][1]["runners"][0])
        check("US angles: pick is marked without changing it",
              built["races"][0]["runners"][0]["pick"] is True
              and built["races"][0]["runners"][0]["horse"] == "A",
              built["races"][0]["runners"][0])
        check("US angles: fair chance is present",
              built["races"][0]["runners"][0]["fair"]["pct"] > 0,
              built["races"][0]["runners"][0])
        ahtml = us_angles.block_html(built)
        check("US angles: tab includes catalog and runners",
              "Filter flags" in ahtml and "Alpha Stakes" in ahtml
              and 'data-angle-filter="fav"' in ahtml, ahtml[:200])
        check("US angles: filter is an input, not a model control",
              "<input" not in ahtml.lower() and "<form" not in ahtml.lower(), ahtml[:200])
        built["health"]["result_card_gaps"] = [
            {"date": "2026-11-13", "course": "dundalk", "winner": "x", "picked": False}]
        ahtml = us_angles.block_html(built)
        check("US angles: all-result card audit is rendered",
              "Result card audit:" in ahtml and "all unpicked" in ahtml, ahtml[:200])
finally:
    refresh.ROOT = old_root
    shutil.rmtree(ptmp, ignore_errors=True)

print("28. manual results entry")
_page = (ROOT / "index.html").read_text(encoding="utf-8")
_dist = (ROOT / "dist" / "public" / "index.html").read_text(encoding="utf-8")
check("manual: source and dist match", _page == _dist)
for _needle in ('id="manualOpen"', 'id="manualForm"', 'id="manualRace"',
                'id="manualWinner"', 'id="manualPickFinish"', 'id="manualList"'):
    check("manual: %s is rendered" % _needle.split('"')[1], _needle in _page)
check("manual: scheduled pull is linked", "actions/workflows/results.yml" in _page)
check("manual: entries are labelled browser-local",
      "saved in this browser only" in _page.lower())
check("manual: entries use their own store",
      "racing-sheet-manual-results-v1" in _page
      and "localStorage.setItem(MANUAL_STORE" in _page)
check("manual: the published RESULTS array is not written by the form",
      "RESULTS.push" not in _page and "RESULTS =" in _page)

print("29. UK/IRE and US results split")
_split_rows = [
    {"course": "UK Test", "time": "13:05", "race": "13:05 UK Test", "winner": "UK Winner",
     "pick": "UK Winner", "hit": True, "places": ["UK Winner"], "placed": True,
     "place_places": 3, "region": "GB"},
    {"course": "US Test", "time": "20:01", "race": "US Test Stakes", "winner": "US Winner",
     "pick": "US Pick", "hit": False, "places": ["US Winner"], "placed": False,
     "place_places": 3, "region": "US"},
]
_uk = refresh.results_block("2026-11-16", _split_rows)
_us = refresh.results_block_us("2026-11-16", _split_rows)
check("results split: UK block excludes US", "UK Test" in _uk and "US Test" not in _uk)
check("results split: US block excludes UK", "US Test" in _us and "UK Test" not in _us)
check("results split: off time is shown",
      '<span class="rtime">13:05</span>' in _uk
      and '<span class="rtime">20:01</span>' in _us)
check("results split: block markers", "RESULTS_SL_START" in _uk
      and "US_RESULTS_SL_START" in _us)
check("results split: Course / off header", "Course / off" in _uk)
check("results split: US summary is US-only", "1 US race settled" in _us)
_tabs_js = _page.split("var TAB_PANELS", 1)[1][:500] if "var TAB_PANELS" in _page else ""
check("results split: the US tab holds the US results panel",
      "['us', 'US racing'" in _page and "usresults" in _tabs_js, _tabs_js[:120])
_us_panel = _page.split('<section class="panel" data-panel="usresults">', 1)[1].split(
    '<section class="panel" data-panel="results">', 1)[0]
_res_panel = _page.split('<section class="panel" data-panel="results">', 1)[1].split(
    '<section class="panel" data-panel="method">', 1)[0]
check("results split: US panel owns the US A/B", "US_AB_SL_START" in _us_panel)
check("results split: Results no longer owns the US A/B",
      "US_AB_SL_START" not in _res_panel)
check("results split: US ledger marker is in the US panel",
      "US_RESULTS_SL_START" in _us_panel)
check("results split: off-time style is present", ".rtime{" in _page)

print("30. latest settled race audit")
_old_root = refresh.ROOT
_old_rate_day = rescore_results.rate_day
with tempfile.TemporaryDirectory(prefix="audit-test-") as ad:
    _ar = Path(ad)
    (_ar / "data" / "results").mkdir(parents=True)
    (_ar / "data" / "results_rated").mkdir(parents=True)
    (_ar / "data" / "results" / "2026-11-17.json").write_text(json.dumps([
        {"course": "US C", "time": "20:00", "winner": "US Horse", "region": "US",
         "pick": "US Horse", "hit": True, "placed": True}]), encoding="utf-8")
    (_ar / "data" / "results" / "2026-11-16.json").write_text(json.dumps([
        {"course": "C", "time": "13:00", "winner": "A", "pick": "A", "hit": True,
         "placed": True, "pick_finish": "1st", "region": "GB"},
        {"course": "C", "time": "13:00", "winner": "B", "pick": "A", "hit": False,
         "placed": False, "pick_finish": "2nd", "region": "GB", "sp": "7.50"},
        {"course": "D", "time": "14:00", "winner": "D1", "pick": "D1", "hit": True,
         "placed": True, "pick_finish": "1st", "region": "GB"}]), encoding="utf-8")
    (_ar / "data" / "results_rated" / "2026-11-16.json").write_text(
        json.dumps({"date": "2026-11-16", "model": "six-input",
                    "races": [{"state": "rated"}]}), encoding="utf-8")
    refresh.ROOT = str(_ar)
    # Two archived copies of the D race, as the day's own card pull and the
    # Racing TV pull produce. pick_card takes the first; the re-score leaves
    # that one unrated, so the audit must not borrow the other copy's rank.
    _cards = [
        {"id": "c1", "course": "C", "time": "13:00",
         "runners": [{"horse": "A"}, {"horse": "B"}]},
        {"id": "d_first", "course": "D", "time": "14:00",
         "runners": [{"horse": "D1"}, {"horse": "D2"}]},
        {"id": "d_second", "course": "D", "time": "14:00",
         "runners": [{"horse": "D1"}, {"horse": "D2"}]},
    ]
    _scored = {
        id(_cards[0]): {
            "course": "C", "time": "13:00", "rated": True, "pick": "A", "chance": 50.0,
            "runners": [{"horse": "A", "total": 80.0, "share": 0.5, "odds": "2.00"},
                        {"horse": "B", "total": 40.0, "share": 0.2, "odds": ""}]},
        id(_cards[1]): {
            "course": "D", "time": "14:00", "rated": False, "pick": "", "chance": None,
            "missing": [{"what": "price", "n": 2, "of": 2}]},
        id(_cards[2]): {
            "course": "D", "time": "14:00", "rated": True, "pick": "D1", "chance": 40.0,
            "runners": [{"horse": "D1", "total": 70.0, "share": 0.4, "odds": "3.00"},
                        {"horse": "D2", "total": 30.0, "share": 0.1, "odds": "9.00"}]},
    }
    rescore_results.rate_day = lambda iso: (_cards, _scored, {})
    _audit = [json.loads(x) for x in refresh.audit_rows()]
    check("audit: latest UK day is selected", len(_audit) == 3, _audit)
    check("audit: winner rank and field size",
          _audit[0]["r"] == 1 and _audit[0]["n"] == 2, _audit[0])
    check("audit: market rank and card price",
          _audit[0]["m"] == 1 and _audit[0]["sp"] == "2.00", _audit[0])
    check("audit: hit and miss verdicts",
          _audit[0]["v"] == "hit" and _audit[1]["v"] == "miss", _audit)
    check("audit: pick and finish carried",
          _audit[0]["p"] == "A" and _audit[0]["pf"] == "1st", _audit[0])
    check("audit: US-only later day is skipped",
          all(x["c"] != "US C" for x in _audit), _audit)
    check("audit: settled SP fills a blank card price",
          _audit[1]["sp"] == "7.50", _audit[1])
    check("audit: a rated race also ranks the winner in the market",
          _audit[1]["m"] == 2, _audit[1])
    check("audit: a duplicate card never lends its rank to another copy's pick",
          _audit[2]["p"] == "not rated" and _audit[2]["r"] is None
          and _audit[2]["n"] == 0, _audit[2])
refresh.ROOT = _old_root
rescore_results.rate_day = _old_rate_day
check("names: an HTML-escaped spelling normalises like the plain one",
      rescore_results.norm("Who&#x27;s Going") == rescore_results.norm("Who's Going")
      and rescore_results.norm("Paddy&#x27;s Milestone") == "paddys milestone")
check("names: a winner is matched to its card without a time",
      rescore_results.pick_card(
          [{"course": "Test", "time": "13:00",
            "runners": [{"horse": "Who's Going"}]}],
          {"course": "Test", "time": "", "winner": "Who&#x27;s Going"}) is not None)
check("audit: a bad off cannot beat the card that holds the winner",
      rescore_results.pick_card(
          [{"id": "time-only", "course": "C", "time": "13:00",
            "runners": [{"horse": "Other"}]},
           {"id": "winner-only", "course": "C", "time": "13:05",
            "runners": [{"horse": "Winner"}]}],
          {"course": "C", "time": "13:00", "winner": "Winner"}).get("id")
      == "winner-only")
check("audit: a card matching both the off and the winner is preferred",
      rescore_results.pick_card(
          [{"id": "time-only", "course": "C", "time": "13:00",
            "runners": [{"horse": "Other"}]},
           {"id": "both", "course": "C", "time": "13:00",
            "runners": [{"horse": "Winner"}]},
           {"id": "winner-only", "course": "C", "time": "13:05",
            "runners": [{"horse": "Winner"}]}],
          {"course": "C", "time": "13:00", "winner": "Winner"}).get("id") == "both")
check("audit: day label is dynamic", 'id="auditDay"' in _page)
check("audit: fixed snapshot removed", "fixed 12 November snapshot" not in _page)
check("audit: price header", "<th>Price</th>" in _page)
check("audit: starting-price fallback is documented",
      "settled starting price where the card had none" in _page)
check("audit: market favourite wording", "market favourite" in _page)
check("audit: generator wired",
      "audit = audit_rows()" in (ROOT / "tooling" / "refresh.py").read_text(encoding="utf-8")
      and "refresh.audit_rows()" in (ROOT / "tooling" / "settle_today.py").read_text(encoding="utf-8"))

print("30b. results merge guards")
import settle_today as st
check("merge: dot-time race label is read as a time",
      st._time({"race": "11.50 Carlisle"}) == "11:50")
_rows = [
    {"course": "Carlisle", "time": "11:50", "winner": "Galatrona",
     "pick": "They're Chancers", "hit": False},
    {"course": "Carlisle", "race": "11.50 Carlisle", "winner": "Galatrona"},
]
_dropped = st._dedupe(_rows)
check("merge: duplicate race is dropped", _dropped == 1 and len(_rows) == 1, _rows)
check("merge: the copy with the pick survives",
      _rows[0].get("pick") == "They're Chancers", _rows)
check("merge: finish comes from the parsed order",
      st._finish_for("Poet's Duke", [(1, "Poet's Duke")],
                     ["Awordinyourear", "Poet's Duke", "Checkpoint Georgie"]) == 1)
check("merge: a pick outside the frame uses the parsed order",
      st._finish_for("Outsider", [(6, "Outsider")], ["A", "B", "C"]) == 6)
check("merge: a missed runner falls back to the placings",
      st._finish_for("Poet's Duke", [], ["Awordinyourear", "Poet's Duke"]) == 2)

print("30c. result-page position parsing")
_fixture = (
    '<div data-testid="racecard-title">12:10 Limerick</div>'
    '<div data-test-id="position-no"><span class="Ordinal__OrdinalWrapper-sc-1">1'
    '<sup class="Ordinal__OrdinalSup-sc-2">st</sup></span>'
    '<div data-testid="saddle-cloth-no">8</div>'
    '<a href="/racing/profiles/horse/1">Poet&#x27;s Duke</a></div>'
    '<div data-test-id="position-no"><span class="Ordinal__OrdinalWrapper-sc-1">2'
    '<sup class="Ordinal__OrdinalSup-sc-2">nd</sup></span>'
    '<div>4 \u00bd</div><div data-testid="saddle-cloth-no">3</div>'
    '<a href="/racing/profiles/horse/2">Con Did It</a></div>'
    '<div data-test-id="position-no"><span class="Ordinal__OrdinalWrapper-sc-1">3'
    '<sup class="Ordinal__OrdinalSup-sc-2">rd</sup></span>'
    '<div>2 \u00be</div><div data-testid="saddle-cloth-no">9</div>'
    '<a href="/racing/profiles/horse/3">Storm Bresie</a></div>')
_ft, _fname, _frunners = sl.parse_race(_fixture)
check("parse: race name and off come from the title",
      _fname == "12:10 Limerick" and _ft == "12:10", (_ft, _fname))
check("parse: positions pair with their own horse",
      _frunners == [(1, "Poet's Duke"), (2, "Con Did It"), (3, "Storm Bresie")],
      _frunners)
check("parse: an HTML entity is decoded before the row is stored",
      sl.parse_race('<div data-testid="racecard-title">The &#x27;Boy&#x27; Handicap</div>')[1]
      == "The 'Boy' Handicap")
check("parse: a title with no clock stores no time",
      sl.parse_race('<div data-testid="racecard-title">Handicap Chase</div>')[:2]
      == ("", "Handicap Chase"))

print("30d. card prices")
check("sp: fractional odds become decimal",
      [st._decimal_sp(v) for v in ("2/1fav", "14/1", "4/11Jfav", "evens", "9/2")]
      == ["3", "15", "1.36", "2", "5.50"],
      [st._decimal_sp(v) for v in ("2/1fav", "14/1", "4/11Jfav", "evens", "9/2")])
check("sp: an unusable price stays blank",
      st._decimal_sp("") == "" and st._decimal_sp("fav") == "")
_prows = [{"odds": "4.50"} for _ in range(6)]
check("cards: an all-round default price is dropped",
      pcards._blank_placeholder_odds(_prows) == 6
      and all(r["odds"] == "" for r in _prows), _prows)
_prows = [{"odds": o} for o in ("2", "3.5", "6", "9")]
check("cards: a real market is kept",
      pcards._blank_placeholder_odds(_prows) == 0 and _prows[0]["odds"] == "2", _prows)

print("30e. factor inputs are read as labelled")
_page = (
    '<section data-time="13:00">'
    '<h4 class="chase-title">Test Hurdle</h4>'
    '<span class="chase-text">4yo+, 6 Runners</span>'
    '<span class="chase-text">Jumps,Turf , 2m , GOOD</span>'
    '<div data-tip="0" data-coursewinner="0" data-distancewinner="0" '
    'data-wonlasttimeout="0" data-oddsdecimal="9999999" data-runnernumber="1" '
    'data-topspeed="0" data-rating="0" class="row-runner">'
    '<div class="fs-form">7</div>'
    '<div class="fri-name"><a href="/runners/alpha">Alpha</a> '
    '<span class="fir-last-ran">21</span></div>'
    '<span class=\'fi-tl\'>15%</span><span class=\'fi-tl\'>20%</span>'
    '<div class="fi-item-weight">Weight: <span>11-4</span></div>'
    '<div class="frd-ratings">RPR: -, Topspeed: -</div>'
    '</div>'
    '<div data-tip="1" data-coursewinner="1" data-distancewinner="0" '
    'data-wonlasttimeout="1" data-oddsdecimal="3.5" data-runnernumber="2" '
    'data-topspeed="88" data-rating="101" class="row-runner">'
    '<div class="fs-form">1</div>'
    '<div class="fri-name"><a href="/runners/beta">Beta</a> '
    '<span class="fir-last-ran">14</span></div>'
    '<div class="fti-tips-total">3 Tips</div>'
    '<div class="fi-item-weight">Weight: <span>11-0</span></div>'
    '<div class="frd-ratings">OR: 105, RPR: 101, Topspeed: 88</div>'
    '</div>'
    '<div data-tip="0" data-coursewinner="0" data-distancewinner="0" '
    'data-wonlasttimeout="1" data-oddsdecimal="6" data-runnernumber="3" '
    'data-topspeed="77" data-rating="99" class="row-runner">'
    '<div class="fri-name"><a href="/runners/gamma">Gamma</a></div>'
    '<div class="frd-ratings">RPR: 99, Topspeed: 77</div>'
    '</div>'
    '</section>')
_cards = pcards.parse_course_page(_page, "Test", "01-01-26")
check("cards: the linked-name markup parses", len(_cards) == 1 and len(_cards[0]["runners"]) == 3,
      _cards)
_r = _cards[0]["runners"] if _cards else []
check("cards: 9999999 is no price", _r and _r[0]["odds"] == "", _r[:1])
check("cards: a zero figure is missing, not a rating",
      _r and (_r[0]["or"], _r[0]["rpr"], _r[0]["ts"]) == ("", "", ""), _r[:1])
check("cards: won-last-time-out 0 is N", _r and _r[0]["going"] == "N", _r[:1])
check("cards: a real price and figures survive",
      _r and (_r[1]["odds"], _r[1]["or"], _r[1]["rpr"], _r[1]["ts"]) == ("3.5", "105", "101", "88"),
      _r[1:2])
check("cards: won-last-time-out 1 is Y", _r and _r[1]["going"] == "Y", _r[1:2])
check("cards: data-rating is RPR, never the official rating",
      _r and (_r[2]["or"], _r[2]["rpr"], _r[2]["ts"]) == ("", "99", "77"), _r[2:3] if _r else _r)

print("30f. placeholders are not scored")
_scard = {"id": "placeholders", "course": "Test", "time": "13:00", "runners": [
    {"horse": "Priced", "odds": "2", "form": "1", "or": "100", "rpr": "100",
     "ts": "100", "weight": "11-0", "days": "20", "course": "N", "distance": "N",
     "going": "N", "jp": "10", "tp": "10", "tips": "2"},
    {"horse": "NoPriceZero", "odds": "9999999", "form": "1", "or": "0", "rpr": "0",
     "ts": "0", "weight": "11-0", "days": "20", "course": "N", "distance": "N",
     "going": "N", "jp": "10", "tp": "10", "tips": "2"},
    {"horse": "NoPriceBlank", "odds": "", "form": "1", "or": "", "rpr": "",
     "ts": "", "weight": "11-0", "days": "20", "course": "N", "distance": "N",
     "going": "N", "jp": "10", "tp": "10", "tips": "2"}]}
_srows = {x["horse"]: x for x in settle.score_cards([_scard])[0]["runners"]}
check("scorer: a zero figure scores the same as a blank",
      _srows["NoPriceZero"]["total"] == _srows["NoPriceBlank"]["total"], _srows)
check("scorer: the no-price sentinel is not a market price",
      _srows["NoPriceZero"]["mktRank"] is None, _srows)
check("scorer: a real price still ranks",
      _srows["Priced"]["mktRank"] == 1 and _srows["Priced"]["odds"] == "2", _srows)

print("31. a late refresh keeps the day's archived meetings")
with tempfile.TemporaryDirectory(prefix="archive-test-") as d:
    os.makedirs(Path(d, "data", "cards", "by_date"))
    Path(d, "data", "cards", "by_date", "2026-11-17.json").write_text(json.dumps({
        "GB": [{"course": "Carlisle", "time": "12:50", "race": "A",
                "runners": [{"horse": "One"}]},
               {"course": "Limerick", "time": "13:10", "race": "B", "runners": []}],
        "US": [{"course": "Parx Racing", "time": "18:00", "race": "C",
                "runners": [{"horse": "Two"}]}],
    }))
    old_root = refresh.ROOT
    refresh.ROOT = d
    try:
        kept = refresh.archived_cards("2026-11-17")
        missing = refresh.archived_cards("2026-11-19")
    finally:
        refresh.ROOT = old_root
    check("refresh: GB meetings already on file are kept",
          [c["course"] for c in kept] == ["Carlisle"], kept)
    check("refresh: empty cards and other regions are left out",
          len(kept) == 1 and kept[0]["runners"][0]["horse"] == "One", kept)
    check("refresh: a day without an archive is not an error", missing == [], missing)

print("32. US live form is archived, never scored")
with tempfile.TemporaryDirectory(prefix="us-form-") as d:
    os.makedirs(Path(d, "data", "us_data"))
    os.makedirs(Path(d, "data", "us_angles"))
    Path(d, "data", "us_data", "2026-11-17.json").write_text(json.dumps({
        "meetings": [{"course": "Parx Racing", "races": [{
            "time": "13:00",
            "runners": [
                {"horse": "Alpha", "form": "112", "or": 101, "finish_position": 1},
                {"horse": "Beta", "form": "3-2", "or": 99, "finish_position": 2},
                {"horse": "Gamma", "form": "555", "or": 95, "finish_position": 3},
                {"horse": "Delta", "form": "4", "or": 90, "finish_position": 7},
            ]}]}]}))
    Path(d, "data", "us_angles", "2026-11-17.json").write_text(json.dumps({
        "races": {"parx racing|13:00": {
            "course": "Parx Racing", "off": "13:00",
            "runners": {"alpha": {"h": "Alpha", "last": [
                ["2026-10-01", "Parx Racing", "dirt", 1, 6, "2/1"]]}}}}}))
    old_root = us_form.ROOT
    us_form.ROOT = d
    try:
        cards = [{"course": "Parx Racing", "time": "13:00", "runners": [
            {"horse": "Alpha", "form": "", "or": ""},
            {"horse": "Beta", "form": "", "or": ""},
            {"horse": "Epsilon", "form": "", "or": ""}]}]
        filled = us_form.enrich_cards(cards, "2026-11-17")
        positions = us_form.finish_positions("2026-11-17")
        rows = [{"course": "Parx Racing", "time": "13:00",
                 "finish": {"Alpha": 1}, "places": ["Alpha"], "field": 1}]
        merged = us_form.merge_finish(rows, "2026-11-17")
    finally:
        us_form.ROOT = old_root
    alpha, beta, epsilon = cards[0]["runners"]
    check("us form: the live form lands under form_live",
          alpha.get("form_live") == "112", alpha)
    check("us form: the scored form and rating are untouched",
          alpha.get("form") == "" and alpha.get("or") == "", cards[0]["runners"])
    check("us form: the layoff is archived in days_live",
          alpha.get("days_live") == 47, alpha)
    check("us form: the live rating is archived separately",
          alpha.get("or_live") == 101 and beta.get("or_live") == 99, cards[0]["runners"])
    check("us form: a runner the live feed does not know gets no keys",
          "form_live" not in epsilon and "or_live" not in epsilon and filled == 2,
          epsilon)
    check("us results: every starter gets a finishing position",
          len(positions) == 4 and positions[("parx racing", "13:00", "delta")]["pos"] == 7,
          positions)
    check("us results: the payout table is extended to the full field",
          merged[0]["finish"] == {"Alpha": 1, "Beta": 2, "Gamma": 3, "Delta": 7}
          and merged[0]["places"] == ["Alpha", "Beta", "Gamma"]
          and merged[0]["field"] == 7, merged)
    check("us form shadow: a race with a runner missing form is not compared",
          not us_form_day.complete(cards[0]), cards[0])
    epsilon["form_live"] = "2"
    check("us form shadow: a fully filled race is comparable",
          us_form_day.complete(cards[0]), cards[0])

print()
print("market-heavy shadow arm")
check("arm: the heavier weight decides the pick",
      market_arm_day.rank([{"market": 10, "form": 90},
                            {"market": 90, "form": 10}]) == 1)
check("arm: a factor a runner lacks is not counted for or against him",
      market_arm_day.rank([{"market": 100}, {"form": 100}],
                          {"market": 1.0, "form": 1.0}) == 0)
check("arm: zero-weight factors cannot decide",
      market_arm_day.rank([{"market": 10}, {"market": 90}],
                          {"market": 0.0, "going": 50.0}) is None)
check("arm: nothing usable means no pick",
      market_arm_day.rank([{}, {"market": None}]) is None)
check("arm: an empty race has no pick",
      market_arm_day.pick({"runners": []}) == "")
check("arm: the arm is not the published weight vector",
      market_arm_day.WEIGHTS["market"] != 18 and market_arm_day.WEIGHTS["market"] > 18,
      market_arm_day.WEIGHTS)
arm_dir = tempfile.mkdtemp(prefix="mktarm_")
old_root = market_arm_day.ROOT
try:
    market_arm_day.ROOT = arm_dir
    os.makedirs(os.path.join(arm_dir, "data", market_arm_day.OUT_DIR))
    snap = os.path.join(arm_dir, "data", market_arm_day.OUT_DIR, "2026-11-18.json")
    with open(snap, "w") as f:
        f.write('{"date": "2026-11-18", "rows": []}')
    before = open(snap).read()
    rc = market_arm_day.main("2026-11-18")
    after = open(snap).read()
finally:
    market_arm_day.ROOT = old_root
check("arm: a snapshot is never rewritten", rc == 0 and before == after)
arm_block = refresh.ab_block_html(toy, "2026-11-18", title="Market-heavy shadow",
                                  subject="market-heavy weighting",
                                  start="MKT_ARM_SL_START", end="MKT_ARM_SL_END")
check("arm block: own markers only",
      arm_block.count("MKT_ARM_SL_START") == 1 and "AB_SL_START" not in arm_block)
check("arm block: names the subject",
      "market-heavy weighting" in text_of(arm_block))

print()
print("share calibration")
cal = calibrate_share.report("toy", [{"y": np.array([1.0, 0.0, 0.0, 0.0])}],
                             [np.array([0.5, 0.5, 0.0, 0.0])])
check("calibration: a correct 50/50 pick scores log 2",
      abs(cal["nll"] - math.log(2)) < 1e-9, cal["nll"])
check("calibration: brier counts every runner",
      abs(cal["brier"] - 0.5) < 1e-9, cal["brier"])
check("calibration: a true 50/50 has no error", abs(cal["ece"]) < 1e-9, cal["ece"])
bad = calibrate_share.report("toy", [{"y": np.array([0.0, 1.0])}],
                             [np.array([0.9, 0.1])])
check("calibration: confident and wrong is caught", abs(bad["ece"] - 90.0) < 1e-6, bad["ece"])
check("calibration: a race too lightly priced has no market probability",
      calibrate_share.market_probs({"odds": [2.0, None, None, None]}) is None)
lo, hi = calibrate_share.wilson(25, 100)
check("calibration: a wide interval for a small sample",
      17.0 < lo < 18.5 and 33.5 < hi < 35.0, (lo, hi))
check("calibration: no races has no interval", calibrate_share.wilson(0, 0) == (0.0, 0.0))
fair = calibrate_share.market_probs({"odds": [2.0, 4.0, 4.0, None]})
check("calibration: the market book is normalised and keeps the blank",
      fair is not None and abs(float(fair.sum()) - 1.0) < 1e-9 and fair[3] == 0.0,
      list(fair) if fair is not None else None)

print()
print("model chance column")
page = (ROOT / "index.html").read_text(encoding="utf-8")
check("page: the card table prints a model chance", "<th>Model %</th>" in page)
check("page: the chance uses the held-out scale",
      "CALIBRATION_SCALE = 0.04" in page)
check("page: the chance is a softmax, not a raw share",
      "x.share = x.total / sum" not in page and
      "x.share = Math.exp(CALIBRATION_SCALE * (x.total - peak)) / mass" in page)
check("page: the footer no longer calls the percentages raw shares",
      "percentages are shares of the field's total rating" not in page)
check("page: the Results tab carries a chance audit block",
      "<!-- CAL_SL_START -->" in page and "<!-- CAL_SL_END -->" in page)

print()
print("model chance audit")
import chance_audit
_tmp = tempfile.mkdtemp()
try:
    with open(os.path.join(_tmp, "2026-11-17.json"), "w", encoding="utf-8") as f:
        json.dump({"races": [
            {"region": "GB", "pick": "A", "chance": 30.0, "hit": True},
            {"region": "GB", "pick": "B", "chance": 20.0, "hit": False},
            {"region": "US", "pick": "C", "chance": 40.0, "hit": True},
            {"region": "GB", "pick": "D", "hit": False},
        ]}, f)
    audit = chance_audit.collect(_tmp)
    check("audit: counts GB/IRE races carrying a chance",
          audit["races"] == 2, audit.get("races"))
    check("audit: averages the stated chance",
          abs(audit["stated"] - 25.0) < 1e-9, audit.get("stated"))
    check("audit: counts the wins", audit["wins"] == 1, audit.get("wins"))
    check("audit: a two-race sample cannot settle the scale",
          audit["more_decisive"] > 0)
    check("audit: the interval widens for a tiny sample",
          audit["half"] > 20.0, audit.get("half"))
    check("audit: verdict accepts an interval that contains the figure",
          chance_audit.verdict({"races": 1, "inside": True}) == "inside")
    check("audit: verdict catches a figure above the interval",
          chance_audit.verdict({"races": 1, "inside": False,
                                "stated": 30.0, "lo": 20.0}) == "high")
    block = refresh.chance_block_html(audit)
    check("audit block: reports the sample", "2</b> settled" in block)
    check("audit block: waits for a decisive sample",
          "Not yet informative" in block, block[:120])
    big = {"races": 400, "wins": 104, "rate": 0.26, "stated": 25.8,
           "lo": 21.9, "hi": 30.4, "half": 4.25, "first": "2026-11-12",
           "last": "2027-02-01", "inside": True, "more_early": 0,
           "more_decisive": 1200}
    block2 = refresh.chance_block_html(big)
    check("audit block: informative is not decisive",
          "Informative but not decisive" in block2)
    check("audit block: says the interval contains the figure",
          "contains the stated figure" in block2)
finally:
    shutil.rmtree(_tmp)

ledger = refresh._results_inner("2026-01-01", [
    {"course": "Carlisle", "race": "12:20 Carlisle", "winner": "Bossman Jack",
     "places": ["Bossman Jack", "Kettledrum", "Hurlyburly"],
     "pick": "Bossman Jack", "hit": True, "pick_finish": "1st", "chance": 26.4},
], "UK/IRE")
check("ledger: the pick carries its stated chance", "26.4%" in ledger, ledger[:90])
check("ledger: the chance is labelled", "pickchance" in ledger)
plain = refresh._results_inner("2026-01-01", [
    {"course": "Carlisle", "race": "12:20 Carlisle", "winner": "Bossman Jack",
     "places": ["Bossman Jack"], "pick": "Bossman Jack", "hit": True,
     "pick_finish": "1st"},
], "UK/IRE")
check("ledger: no stored chance, no printed number", "pickchance" not in plain)

with tempfile.TemporaryDirectory(prefix="rated-ledger-") as rd:
    import pathlib as _pl
    (_pl.Path(rd) / "data" / "results_rated").mkdir(parents=True)
    (_pl.Path(rd) / "data" / "results_rated" / "2026-01-02.json").write_text(json.dumps({
        "date": "2026-01-02", "model": "six-input", "races": [
            {"course": "Carlisle", "time": "12:20", "winner": "Bossman Jack",
             "state": "rated", "pick": "Rated Pick", "chance": 31.5, "finish": "2nd",
             "hit": False, "placed": True, "place_places": 3, "missing": []},
            {"course": "Carlisle", "time": "12:50", "winner": "Other",
             "state": "incomplete", "pick": "", "chance": None, "finish": "",
             "hit": False, "placed": None, "place_places": None,
             "missing": [{"what": "price", "n": 8, "of": 18},
                         {"what": "form", "n": 18, "of": 18}]},
            {"course": "Carlisle", "time": "13:20", "winner": "Third",
             "state": "no-card", "pick": "", "chance": None, "finish": "",
             "hit": False, "placed": None, "place_places": None,
             "missing": [{"what": "no archived card", "n": 0}]},
        ]}), encoding="utf-8")
    _old_root = refresh.ROOT
    refresh.ROOT = rd
    try:
        _rated = refresh._results_inner("2026-01-02", [
            {"course": "Carlisle", "race": "12:20 Carlisle", "winner": "Bossman Jack",
             "places": ["Bossman Jack"], "pick": "Stored Pick", "hit": False},
            {"course": "Carlisle", "race": "12:50 Carlisle", "winner": "Other",
             "places": ["Other"], "pick": "Stored Pick", "hit": True},
            {"course": "Carlisle", "race": "13:20 Carlisle", "winner": "Third",
             "places": ["Third"], "pick": "Stored Pick", "hit": True},
        ], "UK/IRE")
    finally:
        refresh.ROOT = _old_root
check("ledger: the six-input re-score replaces the stored pick",
      "Rated Pick" in _rated and "Stored Pick" not in _rated, _rated[:200])
check("ledger: an incomplete card is marked not rated with the gap named",
      "not rated" in _rated and "price" in _rated, _rated[:200])
check("ledger: a partial gap names the runners that lack the input",
      "8 of 18 runners have no price" in _rated, _rated[:400])
check("ledger: a field-wide gap says all runners, not n of n",
      "no form for all 18 runners" in _rated and "18 of 18" not in _rated,
      _rated[:400])
check("ledger: a race with no card is not called not rated",
      "card not kept" in _rated and "not rated no archived card" not in _rated,
      _rated[:400])
check("ledger: no-card races are outside the rating denominator",
      "1 of 2</b> races with an archived card carries" in _rated, _rated[:400])
check("ledger: rated rows carry their own chance and finish",
      "31.5%" in _rated and "(2nd)" in _rated, _rated[:200])
check("ledger: the day says it is a re-score, not the stored record",
      "re-scored from the archived card" in _rated and "Rated pick" in _rated)

snap = settle.snapshot_of({"course": "C", "time": "13:00", "pick": "A", "chance": 26.4,
    "runners": [{"horse": "A", "odds": "2/1", "total": 64.2, "share": 0.26351,
                 "parts": {"market": 80}},
                {"horse": "B", "total": 11.0}]})
check("snapshot: keeps the printed chance", snap["chance"] == 26.4)
check("snapshot: keeps total and share",
      snap["runners"][0]["total"] == 64.2
      and abs(snap["runners"][0]["share"] - 0.26351) < 1e-9)
check("snapshot: keeps the factor scores",
      snap["runners"][0]["parts"] == {"market": 80})
check("snapshot: a blank share stays blank", snap["runners"][1]["share"] is None)

_stmp = tempfile.mkdtemp()
try:
    _sr = Path(_stmp)
    (_sr / "data" / "cards" / "by_date").mkdir(parents=True)
    (_sr / "data" / "results").mkdir(parents=True)
    (_sr / "data" / "cards" / "by_date" / "2026-11-16.json").write_text(json.dumps(
        {"GB": [{"course": "C", "time": "13:00", "runners": [{"horse": "A"}, {"horse": "B"}]},
                {"course": "D", "time": "14:00", "runners": [{"horse": "X"}]}]}),
        encoding="utf-8")
    (_sr / "data" / "results" / "2026-11-16.json").write_text(json.dumps([
        {"course": "C", "time": "13:00", "winner": "B", "region": "GB"},
        {"course": "D", "time": "14:00", "winner": "X", "region": "GB", "pick": "X"}]),
        encoding="utf-8")
    _old_root3, _old_score3 = refresh.ROOT, settle.score_cards
    refresh.ROOT = _stmp
    settle.score_cards = lambda cards: [
        {"course": c["course"], "time": c["time"],
         "pick": "B" if c["course"] == "C" else "X", "chance": 22.5,
         "runners": [{"horse": "A", "odds": "2/1", "total": 60.0, "share": 0.225,
                      "parts": {"market": 70}},
                     {"horse": "B", "odds": "", "total": 58.0, "share": 0.19,
                      "parts": {"market": 60}}]}
        for c in cards]
    try:
        refresh.settle_day("2026-11-16")
    finally:
        settle.score_cards = _old_score3
        refresh.ROOT = _old_root3
    with open(os.path.join(_stmp, "data", "results", "2026-11-16.json"), encoding="utf-8") as f:
        _saved = json.load(f)
    _new, _old = _saved[0], _saved[1]
    check("settle: the new pick carries its chance",
          _new.get("pick") == "B" and _new.get("chance") == 22.5, _new.get("chance"))
    check("settle: the new pick freezes the card it came from",
          abs((_new.get("card") or {}).get("runners", [{}])[0].get("share", 0) - 0.225) < 1e-9,
          (_new.get("card") or {}).get("runners", [{}])[0].get("share"))
    check("settle: a chance attached to an old pick stores no snapshot",
          _old.get("chance") == 22.5 and "card" not in _old, _old.get("chance"))
finally:
    shutil.rmtree(_stmp)

print()
print("disagreement with the market")
import disagreement_audit as da
check("gap bands cover the line",
      da.band_index(-10.01) == 0 and da.band_index(-10.0) == 1
      and da.band_index(0.0) == 3 and da.band_index(9.99) == 4
      and da.band_index(10.0) == 5)
_dtmp = tempfile.mkdtemp()
try:
    with open(os.path.join(_dtmp, "runners.csv"), "w", newline="") as f:
        f.write("date,course,time,race_id,horse,total,odds,win\n")
        f.write("2026-01-01,C,13:00,R1,A,90,2.0,1\n")
        f.write("2026-01-01,C,13:00,R1,B,50,4.0,0\n")
        f.write("2026-01-01,C,14:00,R2,C,50,1.5,0\n")
        f.write("2026-01-01,C,14:00,R2,D,50,3.0,1\n")
    _da = da.audit(os.path.join(_dtmp, "runners.csv"))
finally:
    shutil.rmtree(_dtmp)
check("disagreement: counts races and priced runners",
      _da["races"] == 2 and _da["overall"]["n"] == 4, _da["races"])
check("disagreement: above +10 won both and returned +150%",
      _da["bands"][5]["n"] == 2 and _da["bands"][5]["wins"] == 2
      and abs(da._stats(_da["bands"][5])["roi"] - 150.0) < 1e-9,
      da._stats(_da["bands"][5]))
check("disagreement: below -10 won none and returned -100%",
      _da["bands"][0]["n"] == 2 and _da["bands"][0]["wins"] == 0
      and abs(da._stats(_da["bands"][0])["roi"] + 100.0) < 1e-9)
check("disagreement: above +10 spans 66.7% and 33.3% market chances",
      abs(_da["bands"][5]["mkt"] / 2 - 50.0) < 0.1, _da["bands"][5]["mkt"] / 2)
_pairs = [(30, 5, 1), (30, 5, 0), (30, -5, 0), (30, -5, 0),
          (60, 5, 1), (60, 5, 1), (60, -5, 0), (60, -5, 1)]
_st = da.stratified(_pairs, bins=2, minimum=1)
check("stratified: splits by price, then compares the two sides",
      _st["bins"] == 2 and abs(_st["diff"] - 50.0) < 1e-9, _st)

print()
print("per-factor audit at a fixed price")
import factor_audit as fa
check("factor: score bands cover the edges",
      fa.band(fa.SCORE_EDGES, 0.0) == 0 and fa.band(fa.SCORE_EDGES, 100.0) == 4
      and fa.band(fa.SCORE_EDGES, None) is None)
_cells = [[[200, 20], [0, 0], [0, 0], [0, 0], [200, 40]],
          [[200, 60], [0, 0], [0, 0], [0, 0], [200, 80]]]
_fc = fa.compare(_cells)
check("factor: pools the bands by inverse variance",
      _fc and _fc["bands"] == 2 and abs(_fc["diff"] - 10.0) < 1e-9, _fc)
check("factor: reports no comparison without extremes",
      fa.compare([[[10, 1], [0, 0], [0, 0], [0, 0], [10, 2]]]) is None)

print()
print("whole-field calibration")
import field_audit
_aud = field_audit.audit([(np.array([0.5, 0.5]), 0), (np.array([0.2, 0.8]), 1)])
check("field: counts races and runners",
      _aud["races"] == 2 and _aud["runners"] == 4, _aud.get("runners"))
check("field: counts top-1 hits", _aud["hits"] == 2, _aud.get("hits"))
check("field: log loss per race", abs(_aud["nll"] - 0.4581) < 1e-3, _aud["nll"])
check("field: brier per runner", abs(_aud["brier"] - 0.145) < 1e-9, _aud["brier"])
check("field: ECE weights the bands", abs(_aud["ece"] - 10.0) < 1e-9, _aud["ece"])
check("field: every band carries its own interval",
      len(_aud["bands"]) == 2
      and all(b["lo"] <= b["won"] <= b["hi"] for b in _aud["bands"]),
      _aud["bands"])
_ftmp = tempfile.mkdtemp()
try:
    with open(os.path.join(_ftmp, "2026-11-17.json"), "w", encoding="utf-8") as f:
        json.dump({"races": [
            {"region": "GB", "winner": "A",
             "card": {"runners": [{"horse": "A", "share": 0.6},
                                   {"horse": "B", "share": 0.4}]}},
            {"region": "US", "winner": "A",
             "card": {"runners": [{"horse": "A", "share": 0.9}]}},
            {"region": "GB", "winner": "C"},
        ]}, f)
    _frows = field_audit.from_snapshots(_ftmp)
    check("field: snapshots skip US and cards without a share",
          len(_frows) == 1 and abs(float(_frows[0][0][0]) - 0.6) < 1e-9)
    check("field: no summary before the minimum sample",
          chance_audit.field_summary(_ftmp) is None)
finally:
    shutil.rmtree(_ftmp)
_block = refresh.chance_block_html({
    "races": 60, "wins": 17, "rate": 17 / 60, "stated": 18.0,
    "lo": 11.0, "hi": 25.0, "half": 7.0, "first": "2026-11-18",
    "last": "2026-12-30", "inside": True, "more_early": 0,
    "more_decisive": 1580,
    "field": {"races": 60, "runners": 600, "hits": 17, "nll": 2.1,
              "brier": 0.077, "ece": 0.9, "stated": 18.0, "rate": 17 / 60,
              "bands": [{"label": "5-15%", "runners": 300, "stated": 9.0,
                         "won": 8.0, "lo": 5.0, "hi": 12.0}]}})
check("audit block: whole-field line when snapshots exist",
      "Whole field" in _block, _block[:150])
check("audit block: whole-field band table",
      "<th>Printed</th>" in _block and "5-15%" in _block
      and "300" in _block and "9.0%" in _block, _block[-400:])

print()
print("Hong Kong settles before the page is written")
_hk_st = (ROOT / "tooling" / "settle_today.py").read_text(encoding="utf-8")
check("HK: the results pass settles Hong Kong exactly once",
      _hk_st.count("settle_hk.main(iso)") == 1)
check("HK: settlement runs before the page is read back",
      _hk_st.index("settle_hk.main(iso)") < _hk_st.index("open(refresh.INDEX"))
check("HK: settlement is not swallowed before the page is written",
      "HK results:  skipped" not in _hk_st)
_hk_wf = (ROOT / ".github" / "workflows" / "results.yml").read_text(encoding="utf-8")
check("HK: the workflow settles Hong Kong in its own step",
      "tooling/settle_hk.py" in _hk_wf)
check("HK: that step runs before the page-writing pass",
      _hk_wf.index("tooling/settle_hk.py") < _hk_wf.index("tooling/settle_today.py"))

print()
print("Hong Kong pre-race odds capture")
import datetime as dt
import snapshot_hk as shk
_now = dt.datetime(2026, 11, 18, 11, 31, tzinfo=dt.timezone.utc)
_hk_card = {"id": "hk-happy-valley-1118-r3", "course": "Happy Valley",
            "race": "Ngong Ping Handicap", "time": "11:40",
            "iso": "2026-11-18T11:40:00.000Z",
            "runners": [{"horse": "Matzden"}, {"horse": "Never Settle"}]}
_hk_payload = {"data": {"raceMeetings": [{"races": [
    {"no": 3, "status": "DECLARED", "runners": [
        {"name_en": "MATZDEN", "winOdds": "3.1", "status": ""},
        {"name_en": "NEVER SETTLE", "winOdds": "4.5", "status": ""}]}]}]}}
_hk_snaps = shk.collect([_hk_card], _hk_payload, _now)
check("HK odds: a race inside the window is captured", len(_hk_snaps) == 1, _hk_snaps)
check("HK odds: the capture records the field and its prices",
      _hk_snaps and [r["horse"] for r in _hk_snaps[0]["runners"]]
      == ["MATZDEN", "NEVER SETTLE"]
      and _hk_snaps[0]["runners"][0]["odds"] == "3.1", _hk_snaps)
check("HK odds: the lead time at capture is recorded",
      _hk_snaps and _hk_snaps[0]["lead_seconds"] == 540, _hk_snaps)
check("HK odds: a race already off is never captured",
      shk.collect([_hk_card], _hk_payload,
                  dt.datetime(2026, 11, 18, 11, 40, 30,
                              tzinfo=dt.timezone.utc)) == [])
check("HK odds: a resulted race is never captured",
      shk.collect([_hk_card], {"data": {"raceMeetings": [{"races": [
          {"no": 3, "status": "RESULT", "runners": [
              {"name_en": "MATZDEN", "winOdds": "2.8"},
              {"name_en": "NEVER SETTLE", "winOdds": "3.9"}]}]}]}}, _now) == [])
check("HK odds: a field that is not the card's is refused",
      shk.collect([_hk_card], {"data": {"raceMeetings": [{"races": [
          {"no": 3, "status": "DECLARED", "runners": [
              {"name_en": "MATZDEN", "winOdds": "3.1"}]}]}]}}, _now) == [])
check("HK odds: a race with no published price is refused",
      shk.collect([_hk_card], {"data": {"raceMeetings": [{"races": [
          {"no": 3, "status": "DECLARED", "runners": [
              {"name_en": "MATZDEN", "winOdds": ""},
              {"name_en": "NEVER SETTLE", "winOdds": ""}]}]}]}}, _now) == [])
with tempfile.TemporaryDirectory(prefix="hk-odds-") as _hod:
    _hk_old = shk.OUT_DIR
    shk.OUT_DIR = _hod
    try:
        _hk_path = shk.save("2026-11-18", _hk_snaps[0])
        check("HK odds: the snapshot is filed once per race",
              bool(_hk_path) and os.path.basename(_hk_path) == "r3.json")
        check("HK odds: a filed snapshot is never overwritten",
              shk.save("2026-11-18", dict(_hk_snaps[0], captured="later")) is None)
    finally:
        shk.OUT_DIR = _hk_old
_hk_odds_wf = (ROOT / ".github" / "workflows" / "hk-odds.yml").read_text(
    encoding="utf-8")
check("HK odds: the capture has its own workflow",
      "tooling/snapshot_hk.py" in _hk_odds_wf)
check("HK odds: the workflow commits what it files",
      "data/hk_preoff" in _hk_odds_wf and "git push" in _hk_odds_wf)

_hk_due_card = {"id": "hk-happy-valley-1118-r3", "course": "Happy Valley",
                "race": "Ngong Ping Handicap", "time": "11:40",
                "iso": "2026-11-18T11:40:00.000Z",
                "runners": [{"horse": "Matzden"}, {"horse": "Never Settle"}]}
_hk_early = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.timezone.utc)
_hk_old_now, _hk_old_cards = shk._now, shk._cards
_hk_old_fetch = shk.scrape_hk.fetch


def _hk_dead_fetch(date):
    raise shk.feed.FeedError("HKJC meeting: no response after 3 attempts")


try:
    shk._now, shk._cards = (lambda: _now), (lambda date_iso: [_hk_due_card])
    shk.scrape_hk.fetch = _hk_dead_fetch
    _hk_dead_out = io.StringIO()
    with contextlib.redirect_stdout(_hk_dead_out):
        _hk_dead_rc = shk.main([])
    _hk_dead_log = _hk_dead_out.getvalue()
    check("HK odds: a dead feed inside the capture window ends the run red",
          _hk_dead_rc != 0, _hk_dead_rc)
    check("HK odds: the failed capture is annotated, not a quiet zero",
          "::error::HK pre-race odds feed unavailable" in _hk_dead_log,
          _hk_dead_log)
    check("HK odds: the failure is not reported as nothing due",
          "nothing due" not in _hk_dead_log, _hk_dead_log)

    def _hk_must_not_fetch(date):
        raise AssertionError("feed read with nothing due")

    shk._now = lambda: _hk_early
    shk.scrape_hk.fetch = _hk_must_not_fetch
    with contextlib.redirect_stdout(io.StringIO()):
        _hk_early_rc = shk.main([])
    check("HK odds: nothing due is a green run and no feed request",
          _hk_early_rc == 0, _hk_early_rc)

    shk._now = lambda: _now
    shk.scrape_hk.fetch = lambda date: _hk_payload
    _hk_ok_out = io.StringIO()
    with contextlib.redirect_stdout(_hk_ok_out):
        _hk_ok_rc = shk.main(["--print"])
    check("HK odds: a readable feed still exits green", _hk_ok_rc == 0, _hk_ok_rc)
    check("HK odds: the green run carries the capture",
          "MATZDEN" in _hk_ok_out.getvalue(), _hk_ok_out.getvalue()[:120])
finally:
    shk._now, shk._cards = _hk_old_now, _hk_old_cards
    shk.scrape_hk.fetch = _hk_old_fetch

print()
print("Hong Kong factor audit")
import audit_hk as ahk


def _hk_card(no, horses):
    return {"id": "hk-happy-valley-1118-r%d" % no, "label": "Race %d" % no,
            "course": "Happy Valley", "time": "1%d:40" % no, "day": "2026-11-18",
            "region": "HK", "iso": "2026-11-18T1%d:40:00.000Z" % no,
            "race": "Ngong Ping Handicap", "going": "Good",
            "klass": "1200M Class 4", "runners": [dict(r, odds="99") for r in horses]}


def _hk_runner(name, no, form, orr, weight, draw):
    return {"horse": name, "no": str(no), "form": form, "or": str(orr), "rpr": "",
            "ts": "", "weight": str(weight), "days": "", "course": "N",
            "distance": "N", "going": "?", "draw": str(draw), "jp": "", "tp": "",
            "tips": "", "naps": ""}


_hk3 = _hk_card(3, [_hk_runner("Alpha", 1, "111111", 60, 133, 2),
                    _hk_runner("Bravo", 2, "234567", 55, 128, 5),
                    _hk_runner("Charlie", 3, "000000", 50, 123, 8),
                    _hk_runner("Delta", 4, "345678", 58, 130, 11)])
_hk4 = _hk_card(4, [_hk_runner("Echo", 1, "123123", 57, 131, 1),
                    _hk_runner("Foxtrot", 2, "222222", 52, 126, 4),
                    _hk_runner("Golf", 3, "000111", 49, 121, 9),
                    _hk_runner("Hotel", 4, "333333", 61, 135, 12)])
with tempfile.TemporaryDirectory(prefix="hk-audit-") as _aroot:
    os.makedirs(os.path.join(_aroot, "data", "cards"))
    os.makedirs(os.path.join(_aroot, "data", "results_hk"))
    os.makedirs(os.path.join(_aroot, "data", "hk_preoff", "2026-11-18"))
    _j = lambda rel, obj: (open(os.path.join(_aroot, rel), "w", encoding="utf-8")
                           .write(json.dumps(obj)))
    _j(os.path.join("data", "cards", "2026-11-18.json"), {"HK": [_hk3, _hk4]})
    _j(os.path.join("data", "hk_preoff", "2026-11-18", "r3.json"), {
        "date": "2026-11-18", "race_no": 3, "lead_seconds": 600,
        "runners": [{"horse": "ALPHA", "odds": "2.0"},
                    {"horse": "BRAVO", "odds": "4.0"},
                    {"horse": "CHARLIE", "odds": "8.0"},
                    {"horse": "DELTA", "odds": "12.0"}]})
    # Filed after the off: a post-race price, so it must never be scored.
    _j(os.path.join("data", "hk_preoff", "2026-11-18", "r4.json"), {
        "date": "2026-11-18", "race_no": 4, "lead_seconds": -30,
        "runners": [{"horse": "ECHO", "odds": "1.8"}]})
    _j(os.path.join("data", "results_hk", "2026-11-18.json"), {"races": [
        {"race_no": 3, "winner": "Alpha",
         "finish": {"Alpha": 1, "Bravo": 2, "Charlie": 3, "Delta": 4}},
        {"race_no": 4, "winner": "Echo",
         "finish": {"Echo": 1, "Foxtrot": 2, "Golf": 3, "Hotel": 4}}]})
    _hk_meets, _hk_skip = ahk.load(_aroot)
    _hk_ready = ahk.readiness(_aroot)
    check("HK audit: readiness counts the archive without scoring it",
          _hk_ready["races"] == 1 and _hk_ready["meetings"] == 1
          and _hk_ready["holdout_races"] == 0 and _hk_ready["last"] == "2026-11-18",
          _hk_ready)
    check("HK audit: a snapshot filed after the off is skipped",
          _hk_skip["late"] == 1, _hk_skip)
    check("HK audit: only races with a pre-off snapshot enter",
          len(_hk_meets) == 1 and len(_hk_meets[0]["races"]) == 1, _hk_meets)
    check("HK audit: the archive's post-race odds are replaced by the snapshot",
          _hk_meets[0]["races"][0]["card"]["runners"][0]["odds"] == "2.0",
          _hk_meets[0]["races"][0]["card"]["runners"][0])
    check("HK audit: the stored result block is not scored",
          "result" not in _hk_meets[0]["races"][0]["card"])
    _hk_out = io.StringIO()
    with contextlib.redirect_stdout(_hk_out):
        _hk_rc = ahk.main(["--root", _aroot, "--page", str(ROOT / "index.html")])
    _hk_txt = _hk_out.getvalue()
    check("HK audit: one meeting cannot make a holdout",
          _hk_rc == 0 and "Not enough meetings" in _hk_txt, _hk_txt)

_hk_parts = {"A": {"parts": {"form": 80}}, "B": {"parts": {"form": 80}},
             "C": {"parts": {"form": 10}}}
check("HK audit: ties break on the lower saddlecloth, not the price",
      ahk.rank(_hk_parts, {"A": 7, "B": 2, "C": 5}, "form") == ["B", "A", "C"])
check("HK audit: a factor that is blank for the field ranks nothing",
      ahk.rank({"A": {"parts": {"form": None}}}, {"A": 1}, "form") is None)
_hk_head = ahk.paired([{"picks": {"form": ["A", "B"], "price": ["B", "A"]},
                        "winner": "A"}], "form")
check("HK audit: the head-to-head counts only races where the picks differ",
      _hk_head["for"] == 1 and _hk_head["against"] == 0
      and abs(_hk_head["p"] - 0.5) < 1e-12, _hk_head)

# A holdout big enough to earn a verdict: form picked the winner every time,
# the price never did, so the sign test must say so.
_hk_fake = []
for _d in range(10):
    _date = "2026-11-%02d" % (_d + 1)
    for _rn in range(1, 10):
        _hk_fake.append({"date": _date, "race_no": _rn, "winner": "A", "field": 4,
                         "picks": {"price": ["B", "A", "C", "D"],
                                   "form": ["A", "B", "C", "D"],
                                   "rating": ["B", "A", "C", "D"],
                                   "weight": ["B", "A", "C", "D"],
                                   "draw": ["C", "A", "D", "B"]}})
_hk_early, _hk_late = ahk.split(_hk_fake, 0.5)
check("HK audit: the split is chronological and keeps the latest meetings back",
      len(_hk_early) == 45 and len(_hk_late) == 45
      and max(r["date"] for r in _hk_early) < min(r["date"] for r in _hk_late))
_hk_out2 = io.StringIO()
with contextlib.redirect_stdout(_hk_out2):
    ahk.report(_hk_fake, {"late": 0, "unpriced": 0, "no_card": 0, "no_result": 0})
_hk_txt2 = _hk_out2.getvalue()
check("HK audit: a factor that beats the favourite at p<0.05 says so",
      "form: beats the favourite" in _hk_txt2, _hk_txt2)
check("HK audit: a factor with no head-to-head gets no verdict",
      "rating:" not in _hk_txt2 and "weight:" not in _hk_txt2, _hk_txt2)
check("HK audit: the barrier draw is scored alongside the other four",
      "barrier draw" in _hk_txt2
      and [n for n, _ in ahk.SIGNALS] == ["price", "form", "rating", "weight", "draw"],
      _hk_txt2)
_hk_draw = {"A": {"parts": {"draw": 90}}, "B": {"parts": {"draw": 90}},
            "C": {"parts": {"draw": 10}}}
check("HK audit: draw ranks like any factor and ties on the saddlecloth",
      ahk.rank(_hk_draw, {"A": 9, "B": 1, "C": 4}, "draw") == ["B", "A", "C"])
_hk_empty = refresh.hk_audit_block_html({"races": 0})
check("HK audit: the Method block says plainly when no price is archived",
      "no Hong Kong market factor can be tested" in _hk_empty)
_hk_block = refresh.hk_audit_block_html({"races": 45, "meetings": 5,
                                         "holdout_races": 18, "holdout_meetings": 2,
                                         "last": "2026-12-02"})
check("HK audit: the Method block states the archive and the held-back sample",
      "45 races at 5 meetings" in _hk_block
      and "the latest on 2026-12-02" in _hk_block
      and "18 of those races are held back" in _hk_block
      and "stays out of the model" in _hk_block, _hk_block)
_hk_met = refresh.hk_audit_block_html({"races": 90, "meetings": 10,
                                       "holdout_races": 45, "holdout_meetings": 5,
                                       "last": "2027-01-10"})
check("HK audit: the Method block changes only when the floor is reached",
      "floor is now behind it" in _hk_met and "stays out of the model" not in _hk_met,
      _hk_met)
_hk_page = (ROOT / "index.html").read_text(encoding="utf-8")
check("HK audit: the Method tab carries the block in both copies",
      "<!-- HK_AUDIT_SL_START -->" in _hk_page
      and _hk_page == (ROOT / "dist" / "public" / "index.html").read_text(encoding="utf-8"))
_hk_st_src = (ROOT / "tooling" / "settle_today.py").read_text(encoding="utf-8")
check("HK audit: the scheduled page build injects the block too",
      "HK_AUDIT_SL" in _hk_st_src and "hk_audit_block_html" in _hk_st_src)
check("HK audit: the block sits in the Method panel",
      _hk_page.index('data-panel="method"') < _hk_page.index("HK_AUDIT_SL_START") < _hk_page.index("</footer>"))
_hk_tab = refresh.hk_audit_block_html({"races": 45, "meetings": 5,
                                       "holdout_races": 18, "holdout_meetings": 2,
                                       "last": "2026-12-02"},
                                      marker="HK_AUDIT_TAB", css=' class="method"')
check("HK audit: the same line can be written under its own marker",
      "<!-- HK_AUDIT_TAB_START -->" in _hk_tab and "<!-- HK_AUDIT_TAB_END -->" in _hk_tab
      and '<p class="method">' in _hk_tab and "45 races at 5 meetings" in _hk_tab, _hk_tab)
check("HK audit: the Hong Kong tab carries the line beside the pick",
      _hk_page.index('data-panel="hk"') < _hk_page.index("HK_AUDIT_TAB_START")
      < _hk_page.index('id="hkTable"')
      and "no Hong Kong market factor can be tested" in _hk_page)
_hk_st_tab = (ROOT / "tooling" / "settle_today.py").read_text(encoding="utf-8")
check("HK audit: the scheduled build writes the Hong Kong tab line too",
      "HK_AUDIT_TAB" in _hk_st_tab)
check("HK tab: the intro no longer implies the pick is scored on prices",
      "Prices appear once HKJC betting opens" not in _hk_page
      and "feed the evidence, not the score" in _hk_page)
_hk_small = _hk_fake[:27]
_hk_out3 = io.StringIO()
with contextlib.redirect_stdout(_hk_out3):
    ahk.report(_hk_small, {"late": 0, "unpriced": 0, "no_card": 0, "no_result": 0})
check("HK audit: a holdout below the floor returns no verdict",
      "No verdict yet" in _hk_out3.getvalue(), _hk_out3.getvalue())
_hk_out4 = io.StringIO()
with contextlib.redirect_stdout(_hk_out4):
    _hk_rc4 = ahk.main([])
check("HK audit: with no snapshots filed it says so and exits clean",
      _hk_rc4 == 0 and "no pre-off snapshots filed yet" in _hk_out4.getvalue(),
      _hk_out4.getvalue())
with tempfile.TemporaryDirectory(prefix="hk-report-") as _hk_rd:
    _hk_path = os.path.join(_hk_rd, "latest.txt")
    _hk_buf = io.StringIO()
    with contextlib.redirect_stdout(_hk_buf):
        _hk_rc5 = ahk.main(["--out", _hk_path])
    check("HK audit: --out records exactly the report it prints",
          _hk_rc5 == 0 and os.path.exists(_hk_path)
          and open(_hk_path, encoding="utf-8").read() == _hk_buf.getvalue(),
          _hk_buf.getvalue())
_res_wf = (ROOT / ".github" / "workflows" / "results.yml").read_text(encoding="utf-8")
check("HK audit: the results job records the report on every run",
      "tooling/audit_hk.py --out data/hk_audit/latest.txt" in _res_wf)
check("HK audit: the report is committed with the results",
      "data/hk_audit" in _res_wf.split("git add", 1)[1].split("\n", 1)[0])
check("HK audit: the audit runs after Hong Kong is settled and the page written",
      _res_wf.index("Settle Hong Kong results") < _res_wf.index("tooling/audit_hk.py")
      and _res_wf.index("Add completed races") < _res_wf.index("tooling/audit_hk.py"))

print()
print("Results tabs by region")
check("results: the course decides the region",
      refresh._result_region({"course": "Dundalk"}) == "IRE"
      and refresh._result_region({"course": "Dundalk (AW)"}) == "IRE"
      and refresh._result_region({"course": "Ffos Las"}) == "GB"
      and refresh._result_region({"course": "Charles Town", "region": "US"}) == "US")
_rows3 = [{"course": "Dundalk", "time": "14:30", "race": "A",
           "winner": "Hastily", "places": ["Hastily"]},
          {"course": "Hexham", "time": "13:00", "race": "B",
           "winner": "Aubisque", "places": ["Aubisque"]},
          {"course": "Charles Town", "time": "23:00", "race": "C",
           "winner": "Nova", "places": ["Nova"], "region": "US"}]
_uk = refresh.results_block("2026-11-18", _rows3)
_ie = refresh.results_block_ire("2026-11-18", _rows3)
check("results: the UK tab drops Irish and US rows",
      "Hexham" in _uk and "Dundalk" not in _uk and "Charles Town" not in _uk, _uk)
check("results: the Irish tab holds only Irish rows",
      "Dundalk" in _ie and "Hexham" not in _ie and "Charles Town" not in _ie, _ie)
check("results: each region has its own tab",
      "'Irish results'" in _hk_page and "'US results'" in _hk_page
      and "us: ['flags', 'angles']" in _hk_page
      and "usresults: ['usresults']" in _hk_page
      and 'data-panel="ireresults"' in _hk_page)
check("results: the Irish ledger is built into both copies",
      "IRE_RESULTS_SL_START" in _hk_page
      and _hk_page == (ROOT / "dist" / "public" / "index.html").read_text(encoding="utf-8"))
check("results: the scheduled build writes the Irish ledger",
      "IRE_RESULTS_SL" in (ROOT / "tooling" / "settle_today.py").read_text(encoding="utf-8"))

print()
print("US results feed")
import fetch_results_us as fru
import feed as feedmod
_real_get = fru.scrape_us.get
_real_sleep = feedmod.time.sleep
try:
    feedmod.time.sleep = lambda _s: None
    _calls = []

    def _dead(url):
        _calls.append(url)
        return ""

    fru.scrape_us.get = _dead
    try:
        fru.get_page("http://example.invalid", tries=3, backoff=(0, 0, 0))
        _raised = False
    except fru.FeedError:
        _raised = True
    check("us feed: a dead page raises only after every attempt",
          _raised and len(_calls) == 3, _calls)

    _seq = iter(["", "", "<html>ok</html>"])
    fru.scrape_us.get = lambda url: next(_seq)
    check("us feed: a retry that comes good is used",
          fru.get_page("http://example.invalid", tries=3, backoff=(0, 0, 0))
          == "<html>ok</html>")

    fru.scrape_us.get = lambda url: "<html>no meetings today</html>"
    check("us feed: a genuinely empty day is not an error",
          fru.fetch_day("2026-11-10") == [])

    fru.scrape_us.get = lambda url: ""
    try:
        fru.fetch_day("2026-11-10")
        _raised2 = False
    except fru.FeedError:
        _raised2 = True
    check("us feed: an unreachable index raises instead of returning nothing",
          _raised2)
finally:
    fru.scrape_us.get = _real_get
    feedmod.time.sleep = _real_sleep

# The UK/IRE and Hong Kong feeds answer an empty string on failure too.
import fetch_results_sl as slmod
import fetch_results_ir as irmod
import settle_hk as hkmod

_ir_rows = irmod.parse_meeting(
    '<div class="racename">15.40 The &#x27;Boy&#x27; Handicap</div>'
    '<td>1st</td><td>Don&#x27;t Hurry Love (IRE)</td><td>(5)</td><td>4/1</td>'
    '<td>J Smith</td><td>T Trainer</td>'
    '<td>2nd</td><td>Raging Raj (GB)</td><td>(3)</td><td>9/2</td>'
    '<td>J Doe</td><td>U Trainer</td>', 'Navan')
check("parse: irishracing names are stored unescaped",
      len(_ir_rows) == 1 and _ir_rows[0]["winner"] == "Don't Hurry Love"
      and _ir_rows[0]["race"].endswith("The 'Boy' Handicap")
      and "&#x27;" not in json.dumps(_ir_rows), _ir_rows)

# A scraped day has to write the off the page's title holds. Before the title's
# clock was read, every row was filed with time "" and matching fell back to the
# race label. A page whose title carries no clock still stores no time.
_sl_index = ('<a href="/racing/results/2026-11-20/dundalk/1">x</a>'
             '<a href="/racing/results/2026-11-20/dundalk/2">y</a>')
_sl_clocked = ('<div data-testid="racecard-title">14:30 Dundalk</div>'
               '<div data-test-id="position-no"><span class="Ordinal__OrdinalWrapper-x">'
               '1<sup>st</sup></span><div data-testid="saddle-cloth-no">4</div>'
               '<a href="/racing/profiles/horse/9">Hastily</a></div>')
_sl_untimed = ('<div data-testid="racecard-title">Handicap Chase</div>'
               '<div data-test-id="position-no"><span class="Ordinal__OrdinalWrapper-x">'
               '1<sup>st</sup></span><div data-testid="saddle-cloth-no">7</div>'
               '<a href="/racing/profiles/horse/10">Raging Raj</a></div>')
_old_fetch_get = slmod.get
slmod.get = lambda url, *a, **k: (
    _sl_index if url.endswith("/2026-11-20")
    else _sl_clocked if url.endswith("/dundalk/1") else _sl_untimed)
try:
    _sl_rows = slmod.fetch_day("2026-11-20")
finally:
    slmod.get = _old_fetch_get
check("fetch: a scraped day writes each race's off from the page",
      [(r["time"], r["course"], r["winner"]) for r in _sl_rows]
      == [("14:30", "Dundalk", "Hastily"), ("", "Dundalk", "Raging Raj")],
      _sl_rows)


def _dead_get(url, tries=3):
    raise feedmod.FeedError("dead feed")


_old_sl_get = slmod.get
_old_ir_get = irmod.get
_old_hk_fetch = hkmod.scrape_hk.fetch
_old_hk_cards = hkmod._cards
try:
    slmod.get = _dead_get
    try:
        slmod.fetch_day("2026-11-10")
        _uk_raised = False
    except feedmod.FeedError:
        _uk_raised = True
    irmod.get = _dead_get
    try:
        irmod.fetch_day("2026-11-10")
        _ir_raised = False
    except feedmod.FeedError:
        _ir_raised = True
    # A card must exist for the current UTC day, otherwise main exits before
    # the feed is touched and the outage assertion proves nothing.
    hkmod._cards = lambda date_iso: [{}]
    hkmod.scrape_hk.fetch = _dead_get
    try:
        hkmod.main([])
        _hk_raised = False
    except feedmod.FeedError:
        _hk_raised = True
finally:
    slmod.get = _old_sl_get
    irmod.get = _old_ir_get
    hkmod.scrape_hk.fetch = _old_hk_fetch
    hkmod._cards = _old_hk_cards
check("uk/ire feed: an unreachable index raises", _uk_raised)
check("irish feed: an unreachable index raises", _ir_raised)
check("hk feed: an unreachable meeting raises, not a quiet zero", _hk_raised)
_st_us = (ROOT / "tooling" / "settle_today.py").read_text(encoding="utf-8")
check("results feeds: a failed fetch ends the run red",
      "::error::results feed unavailable" in _st_us)
check("hk settlement: the feed failure is not swallowed",
      "raise" in (ROOT / "tooling" / "settle_hk.py").read_text(encoding="utf-8")
      and "feed unavailable" not in
      (ROOT / "tooling" / "settle_hk.py").read_text(encoding="utf-8"))
_wf_res = (ROOT / ".github" / "workflows" / "results.yml").read_text(encoding="utf-8")
check("us feed: a red run still commits the day's results",
      "if: always()" in _wf_res.split("name: Commit results", 1)[1][:220])

print()
print("Results trigger")
_res_yml = (ROOT / ".github" / "workflows" / "results.yml").read_text(encoding="utf-8")
check("results: a data commit starts a run instead of waiting on the cron",
      "\n  push:" in _res_yml and '"data/cards/**"' in _res_yml
      and '"data/results/**"' in _res_yml and "branches: [main]" in _res_yml)
check("results: a push run skips the job's own commit",
      "github.event_name != 'push'" in _res_yml
      and "!contains(github.event.head_commit.message, 'Live results')" in _res_yml)
check("results: the guard names the message the job actually writes",
      'git commit -m "Live results' in _res_yml)
check("results: the cron covers the late evening meetings",
      "*/20 0-3,12-23 * * *" in _res_yml)
check("results: the run summary reports the UK and Irish ledger times",
      "tooling/ledger_summary.py" in _res_yml
      and _res_yml.index("ledger_summary.py") < _res_yml.index("Commit results"))

print()
print("France (PMU)")
import france as frmod
import audit_coverage as covmod

check("France: musique is converted, never read as pulled up",
      frmod.fr_form("1p2p0p4p0p1p2p4p(25)0p") == "401240",
      frmod.fr_form("1p2p0p4p0p1p2p4p(25)0p"))
check("France: a failed run keeps its letter",
      frmod.fr_form("Da3a2a") == "D32", frmod.fr_form("Da3a2a"))
check("France: hippodrome prefix and country suffix are stripped",
      frmod.course_name("HIPPODROME DE SAINT-CLOUD") == "Saint-Cloud"
      and frmod.course_name("HIPPODROME DE KEMPTON PARK  GB") == "Kempton Park"
      and frmod.course_name("HIPPODROME DE VALPARAISO CHILI") == "Valparaiso")

_prog = {"programme": {"reunions": [
    {"pays": {"libelle": "FRANCE"},
     "hippodrome": {"libelleLong": "HIPPODROME DE PAU"},
     "courses": [
         {"numReunion": 2, "numOrdre": 1, "libelle": "PRIX DE BILHERES",
          "discipline": "PLAT", "distance": 2000,
          "heureDepart": 1795004700000, "nombreDeclaresPartants": 3,
          "statut": "PROGRAMMEE"},
         {"numReunion": 2, "numOrdre": 2, "libelle": "SHORT FIELD",
          "discipline": "PLAT", "distance": 2000,
          "heureDepart": 1795004700000, "nombreDeclaresPartants": 4,
          "statut": "PROGRAMMEE"},
     ]},
    {"pays": {"libelle": "ARGENTINE"},
     "hippodrome": {"libelleLong": "HIPPODROME DE SAN ISIDRO"},
     "courses": [{"numReunion": 8, "numOrdre": 1, "libelle": "X",
                  "discipline": "PLAT", "distance": 1600,
                  "heureDepart": 1795004700000, "nombreDeclaresPartants": 1,
                  "statut": "PROGRAMMEE"}]},
    {"pays": {"libelle": "CHILI"},
     "hippodrome": {"libelleLong": "HIPPODROME DE VALPARAISO CHILI"},
     "courses": [{"numReunion": 9, "numOrdre": 1, "libelle": "PREMIO X",
                  "discipline": "PLAT", "distance": 1600,
                  "heureDepart": 1795004700000, "nombreDeclaresPartants": 2,
                  "statut": "PROGRAMMEE"}]},
]}}
_parts = {
    "R2C1": [
        {"numPmu": 1, "nom": "SARMAD", "statut": "PARTANT", "musique": "2p3p1p",
         "handicapValeur": 38.0, "handicapPoids": 570, "placeCorde": 4,
         "driver": "A.WERLE", "entraineur": "CH.GOURDAIN",
         "dernierRapportDirect": {"rapport": 5.3}},
        {"numPmu": 2, "nom": "DRONGO", "statut": "PARTANT", "musique": "2p3p1p",
         "handicapValeur": 38.0, "handicapPoids": 570, "placeCorde": 7,
         "driver": "I.MENDIZABAL", "entraineur": "R.AVIAL",
         "dernierRapportDirect": {"rapport": 2.4}},
        {"numPmu": 3, "nom": "OUT", "statut": "NON_PARTANT", "musique": ""},
    ],
    "R2C2": [{"numPmu": 1, "nom": "ONLY ONE", "statut": "PARTANT", "musique": ""}],
    "R9C1": [
        {"numPmu": 1, "nom": "OTRA VEZ PITUCO", "statut": "PARTANT",
         "musique": "6p1p1p5p3p", "handicapValeur": 42.0, "handicapPoids": 560,
         "driver": "J. BAEZA", "entraineur": "JUAN PABLO BAEZA"},
        {"numPmu": 2, "nom": "MAKE IT COUNT", "statut": "PARTANT",
         "musique": "3p1p1p1p6p", "handicapValeur": 41.0, "handicapPoids": 560,
         "driver": "C. BERNAL", "entraineur": "CLAUDIO BERNAL"},
    ],
}
_old_prog, _old_json = frmod.programme, frmod._json
try:
    frmod.programme = lambda iso: _prog
    frmod._json = lambda url, label: {
        "participants": next(_parts[k] for k in _parts if label.endswith(k))}
    _now = frmod.dt.datetime(2026, 11, 18, tzinfo=frmod.dt.timezone.utc)
    _fr_cards = frmod.fetch_cards("2026-11-19", now=_now)
    _cl_cards = frmod.fetch_cards("2026-11-19", "CL", now=_now)
finally:
    frmod.programme, frmod._json = _old_prog, _old_json
check("France: only the French meeting is carded",
      len(_fr_cards) == 1 and _fr_cards[0]["course"] == "Pau", _fr_cards)
check("France: a short declared field is skipped, not scored",
      all(c["race_no"] != "R2C2" for c in _fr_cards))
_one = _fr_cards[0]["runners"] if _fr_cards else []
check("France: non-runners are not part of the field",
      len(_one) == 2 and all(r["horse"] != "OUT" for r in _one), _one)
check("France: value, weight, draw, dividend and form reach the card",
      bool(_one) and _one[0]["or"] == "38" and _one[0]["weight"] == "57"
      and _one[0]["draw"] == "4" and _one[0]["odds"] == "5.30"
      and _one[0]["form"] == "231" and _one[0]["jp"] == "A.WERLE", _one[:1])
check("Chile: the same feed builds a Valparaiso card with its own tag",
      len(_cl_cards) == 1 and _cl_cards[0]["region"] == "CL"
      and _cl_cards[0]["course"] == "Valparaiso"
      and _cl_cards[0]["id"].startswith("cl-"), _cl_cards)
check("Chile: a card without prices keeps the price blank",
      bool(_cl_cards) and all(r["odds"] == "" for r in _cl_cards[0]["runners"]),
      _cl_cards[:1])

with tempfile.TemporaryDirectory(prefix="fr-settle-") as fd:
    _old_out = frmod.OUT_DIRS["FR"]
    _old_arch, _old_res = frmod.archived_cards, frmod.results_for
    _old_scored = settle.scored
    try:
        frmod.OUT_DIRS["FR"] = os.path.join(fd, "results_fr")
        _card = _fr_cards[0]
        frmod.archived_cards = lambda iso, region="FR": [_card]
        frmod.results_for = lambda iso, region="FR": [{
            "course": "Pau", "time": _card["time"], "race": _card["race"],
            "race_no": "R2C1", "winner": "DRONGO",
            "places": ["DRONGO", "SARMAD", "ONLY ONE"],
            "order": ["DRONGO", "SARMAD", "ONLY ONE"], "region": "FR"}]
        settle.scored = lambda card: {"pick": "SARMAD", "chance": 41.2}
        _rows = frmod.settle("2026-11-19")
        check("France: a settled pick carries its official position",
              _rows and _rows[0]["pick"] == "SARMAD"
              and _rows[0]["pick_finish"] == "2nd" and _rows[0]["hit"] is False,
              _rows)
        settle.scored = lambda card: {"pick": "DRONGO", "chance": 55.0}
        _rows2 = frmod.settle("2026-11-19")
        check("France: a stored pick is never rewritten",
              _rows2 and _rows2[0]["pick"] == "SARMAD", _rows2)
        _card.pop("result", None)
        check("France: the page card carries the official outcome",
              frmod.attach([_card], _rows2) == 1
              and _card["result"]["finish"]["DRONGO"] == 1
              and _card["result"]["pick_finish"] == "2nd")
    finally:
        frmod.OUT_DIRS["FR"] = _old_out
        frmod.archived_cards, frmod.results_for = _old_arch, _old_res
        settle.scored = _old_scored

with tempfile.TemporaryDirectory(prefix="cl-stamp-") as fd:
    _old_out = frmod.OUT_DIRS["CL"]
    _old_arch, _old_res = frmod.archived_cards, frmod.results_for
    _old_now = frmod._utcnow
    try:
        frmod.OUT_DIRS["CL"] = os.path.join(fd, "results_cl")
        _cl_card = _cl_cards[0]
        _cl_row = {"course": _cl_card["course"], "time": _cl_card["time"],
                   "race": _cl_card["race"], "race_no": _cl_card["race_no"],
                   "winner": "MAKE IT COUNT",
                   "places": ["MAKE IT COUNT", "OTRA VEZ PITUCO"],
                   "order": ["MAKE IT COUNT", "OTRA VEZ PITUCO"],
                   "region": "CL"}
        frmod.archived_cards = lambda iso, region="CL": [_cl_card]
        frmod.results_for = lambda iso, region="CL": [dict(_cl_row)]
        frmod._utcnow = lambda: "2026-11-18T21:20:00Z"
        frmod.settle("2026-11-18", "CL")
        _path = os.path.join(frmod.OUT_DIRS["CL"], "2026-11-18.json")
        _doc = json.load(open(_path, encoding="utf-8"))
        check("Chile: a new settlement records when the ledger changed",
              _doc.get("updated") == "2026-11-18T21:20:00Z",
              _doc.get("updated"))
        frmod._utcnow = lambda: "2026-11-18T21:40:00Z"
        frmod.settle("2026-11-18", "CL")
        _doc = json.load(open(_path, encoding="utf-8"))
        check("Chile: a sweep with no new arrival keeps the timestamp",
              _doc.get("updated") == "2026-11-18T21:20:00Z",
              _doc.get("updated"))
        frmod.results_for = lambda iso, region="CL": [dict(_cl_row), {
            "course": _cl_card["course"], "time": "22:00", "race": "R9C3",
            "race_no": "R9C3", "winner": "HIJO DEL REY",
            "places": ["HIJO DEL REY"], "order": ["HIJO DEL REY"],
            "region": "CL"}]
        frmod.settle("2026-11-18", "CL")
        _doc = json.load(open(_path, encoding="utf-8"))
        check("Chile: a new arrival moves the last-changed time",
              _doc.get("updated") == "2026-11-18T21:40:00Z",
              _doc.get("updated"))
        _blk = frmod.results_block("CL")
        check("Chile: the results tab states the last-changed UTC time",
              'id="clLedger"' in _blk
              and "18 November 2026, 21:40 UTC" in _blk, _blk[:220])
    finally:
        frmod.OUT_DIRS["CL"] = _old_out
        frmod.archived_cards, frmod.results_for = _old_arch, _old_res
        frmod._utcnow = _old_now

_old_clock = refresh._utcnow
try:
    refresh._utcnow = lambda: "2026-11-18T20:00:00Z"
    _old_us = [{"course": "Churchill Downs", "time": "18:00",
                "winner": "Flaming Verdict", "pick": "Flaming Verdict",
                "settled_at": "2026-11-17T23:00:00Z"}]
    _new_us = [{"course": "Churchill Downs", "time": "18:00",
                "winner": "Flaming Verdict"}]
    refresh.carry_picks(_old_us, _new_us)
    check("US ledger: a re-scraped race keeps its settlement time",
          _new_us[0].get("settled_at") == "2026-11-17T23:00:00Z", _new_us[0])
    _brand_new = [{"course": "Parx Racing", "time": "19:00", "winner": "Canuto"}]
    refresh.carry_picks([], _brand_new)
    check("US ledger: a newly filed race records the settlement time",
          _brand_new[0].get("settled_at") == "2026-11-18T20:00:00Z",
          _brand_new[0])
    _legacy = [{"course": "Parx Racing", "time": "19:00", "winner": "Canuto"}]
    _same = [{"course": "Parx Racing", "time": "19:00", "winner": "Canuto"}]
    refresh.carry_picks(_legacy, _same)
    check("US ledger: a legacy row is not given a made-up time",
          "settled_at" not in _same[0], _same[0])
finally:
    refresh._utcnow = _old_clock
_us_block = refresh.results_block_us("2026-11-18", [{
    "region": "US", "course": "Churchill Downs", "time": "18:00",
    "race": "5F", "winner": "Flaming Verdict",
    "places": ["Flaming Verdict"], "settled_at": "2026-11-18T20:00:00Z"}])
check("US ledger: the results tab shows the last-changed UTC time",
      'id="usLedger"' in _us_block and "18 November 2026, 20:00 UTC" in _us_block,
      _us_block[:200])
_kept_rows = [{"course": "Hereford", "winner": "A", "region": None}]
_kept_us = refresh.merge_foreign_results(_kept_rows, [
    {"course": "Churchill Downs", "winner": "Flaming Verdict", "region": "US",
     "pick": "Flaming Verdict"},
    {"course": "Hereford", "winner": "A", "region": None},
])
check("US ledger: a UK results refresh keeps the US rows",
      _kept_us == 1 and len(_kept_rows) == 2
      and _kept_rows[1].get("region") == "US", _kept_rows)
check("US ledger: an already-present US race is not duplicated",
      refresh.merge_foreign_results(
          [{"course": "Churchill Downs", "winner": "Flaming Verdict", "region": "US"}],
          [{"course": "Churchill Downs", "winner": "Flaming Verdict", "region": "US"}]) == 0)
_uk_block = refresh.results_block("2026-11-18", [{
    "course": "Hereford", "time": "13:00", "race": "X", "winner": "A",
    "places": ["A"], "pick": "A", "settled_at": "2026-11-18T20:00:00Z"}])
check("UK ledger: the results tab shows the last-changed UTC time",
      'id="ukLedger"' in _uk_block and "18 November 2026, 20:00 UTC" in _uk_block,
      _uk_block[:200])
_ire_block = refresh.results_block_ire("2026-11-18", [{
    "course": "Dundalk", "time": "14:00", "race": "Y", "winner": "B",
    "places": ["B"], "pick": "B", "settled_at": "2026-11-18T20:00:00Z"}])
check("Irish ledger: the results tab shows the last-changed UTC time",
      'id="ireLedger"' in _ire_block and "18 November 2026, 20:00 UTC" in _ire_block,
      _ire_block[:200])

with tempfile.TemporaryDirectory(prefix="ledger-switch-") as fd:
    _old_root, _old_clock = refresh.ROOT, refresh._utcnow
    try:
        refresh.ROOT = fd
        os.makedirs(os.path.join(fd, "data", "results"))
        _legacy = [
            {"course": "Hereford", "time": "13:00", "race": "Old UK",
             "winner": "A", "places": ["A"], "pick": "A"},
            {"course": "Dundalk", "time": "14:00", "race": "Old IRE",
             "winner": "B", "places": ["B"], "pick": "B"},
        ]
        with open(os.path.join(fd, "data", "results", "2026-11-18.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_legacy, f)
        _before_uk = refresh.results_block_all()
        _before_ire = refresh.results_block_all_ire()
        check("ledger switch: a legacy UK day shows the fallback",
              "Latest result" in _before_uk and "Ledger last changed" not in _before_uk,
              _before_uk[:180])
        check("ledger switch: a legacy Irish day shows the fallback",
              "Latest result" in _before_ire and "Ledger last changed" not in _before_ire,
              _before_ire[:180])
        refresh._utcnow = lambda: "2026-11-19T13:05:00Z"
        _fresh = [dict(r) for r in _legacy] + [
            {"course": "Hereford", "time": "14:00", "race": "New UK",
             "winner": "C", "places": ["C"], "pick": "C"},
            {"course": "Dundalk", "time": "15:00", "race": "New IRE",
             "winner": "D", "places": ["D"], "pick": "D"},
        ]
        refresh.carry_picks(_legacy, _fresh)
        with open(os.path.join(fd, "data", "results", "2026-11-19.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_fresh, f)
        _after_uk = refresh.results_block_all()
        _after_ire = refresh.results_block_all_ire()
        check("ledger switch: the first new UK result shows an exact UTC time",
              "Ledger last changed" in _after_uk
              and "19 November 2026, 13:05 UTC" in _after_uk
              and "Latest result" not in _after_uk, _after_uk[:200])
        check("ledger switch: the first new Irish result shows an exact UTC time",
              "Ledger last changed" in _after_ire
              and "19 November 2026, 13:05 UTC" in _after_ire
              and "Latest result" not in _after_ire, _after_ire[:200])
    finally:
        refresh.ROOT, refresh._utcnow = _old_root, _old_clock

with tempfile.TemporaryDirectory(prefix="ledger-summary-") as fd:
    _old_root = refresh.ROOT
    try:
        refresh.ROOT = fd
        os.makedirs(os.path.join(fd, "data", "results"))
        _legacy = [
            {"course": "Hereford", "time": "13:00", "race": "Old UK",
             "winner": "A", "places": ["A"], "pick": "A"},
            {"course": "Dundalk", "time": "14:00", "race": "Old IRE",
             "winner": "B", "places": ["B"], "pick": "B"},
        ]
        with open(os.path.join(fd, "data", "results", "2026-11-18.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_legacy, f)
        _txt = lsum.render()
        check("summary: the UK and Irish ledgers are both named",
              "**UK:**" in _txt and "**Irish:**" in _txt, _txt)
        check("summary: legacy rows show the latest day, not a made-up time",
              "latest result **18 November 2026**" in _txt
              and "last changed" not in _txt, _txt)
        _fresh = [dict(r) for r in _legacy] + [
            {"course": "Hereford", "time": "14:00", "race": "New UK",
             "winner": "C", "places": ["C"], "pick": "C",
             "settled_at": "2026-11-19T13:05:00Z"},
            {"course": "Dundalk", "time": "15:00", "race": "New IRE",
             "winner": "D", "places": ["D"], "pick": "D",
             "settled_at": "2026-11-19T13:05:00Z"},
        ]
        with open(os.path.join(fd, "data", "results", "2026-11-19.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_fresh, f)
        _txt = lsum.render()
        check("summary: a stamped ledger shows the exact UTC time",
              "last changed **19 November 2026, 13:05 UTC**" in _txt, _txt)
    finally:
        refresh.ROOT = _old_root

_hk_dir, _hk_picks, _hk_clock = hkmod.OUT_DIR, hkmod._picks, hkmod._utcnow
try:
    with tempfile.TemporaryDirectory(prefix="hk-stamp-") as fd:
        hkmod.OUT_DIR = os.path.join(fd, "results_hk")
        hkmod._picks = lambda cards: {3: "Alpha"}
        hkmod._utcnow = lambda: "2026-11-18T20:00:00Z"
        _hk_card = {"id": "hk-2026-11-18-r3", "race_no": 3,
                    "course": "Happy Valley", "time": "11:00", "race": "Test"}
        _hk_off = {3: {"winner": "Alpha", "places": ["Alpha", "Bravo"],
                       "finish": {"Alpha": 1, "Bravo": 2}, "field": 2}}
        _hk_rows, _hk_added = hkmod.build("2026-11-18", [_hk_card], _hk_off, keep={})
        check("HK ledger: a new settlement records the time",
              _hk_added == 1
              and _hk_rows[0].get("settled_at") == "2026-11-18T20:00:00Z",
              _hk_rows)
        hkmod.save("2026-11-18", _hk_rows)
        _hk_blk = hkmod.results_block()
        check("HK ledger: the results tab shows the last-changed UTC time",
              'id="hkLedger"' in _hk_blk
              and "18 November 2026, 20:00 UTC" in _hk_blk, _hk_blk[:200])
finally:
    hkmod.OUT_DIR, hkmod._picks, hkmod._utcnow = _hk_dir, _hk_picks, _hk_clock

_report = {"measured": "2026-11-18T19:00:00+00:00", "rows": [
    {"country": "France", "code": "FR", "source": "PMU", "cards": True,
     "results": True, "prices": True, "verdict": "live",
     "note": "9 declared runners, 8 named arrivals, forecast dividends"},
    {"country": "Australia & New Zealand", "code": "AU/NZ", "source": "Punters",
     "cards": False, "results": False, "prices": False,
     "verdict": "blocked", "note": "403 from this network"},
]}
_block = covmod.block_html(_report)
check("coverage: the audit table is generated for the Method tab",
      "COVERAGE_SL_START" in _block and "France" in _block
      and "blocked" in _block and "forecast dividends" in _block)
check("coverage: no result is claimed without a field",
      covmod._status(False, True, False, "arrival numbers only")["verdict"]
      == "arrival numbers only")

_fr_page = (ROOT / "index.html").read_text(encoding="utf-8")
check("France: the page has a racing tab and a results tab",
      "['france', 'France'" in _fr_page and "['frresults', 'France results'" in _fr_page
      and 'data-panel="france"' in _fr_page and 'data-panel="frresults"' in _fr_page)
check("France: the card block and result marker are present",
      "var CARDS_FR = [" in _fr_page and "FR_RESULTS_SL_START" in _fr_page
      and "function renderFrance" in _fr_page)
check("Chile: the page has a racing tab and a results tab",
      "['chile', 'Chile'" in _fr_page and "['clresults', 'Chile results'" in _fr_page
      and 'data-panel="chile"' in _fr_page and 'data-panel="clresults"' in _fr_page)
check("Chile: the card block, result marker and renderer are present",
      "var CARDS_CL = [" in _fr_page and "CL_RESULTS_SL_START" in _fr_page
      and "function renderChile" in _fr_page)
check("France and Chile: the results tabs show when the ledger last changed",
      'id="frLedger"' in _fr_page and 'id="clLedger"' in _fr_page)
check("US and HK: the results tabs show when the ledger last changed",
      'id="usLedger"' in _fr_page and 'id="hkLedger"' in _fr_page)
check("UK and Irish: the results tabs show when the ledger last changed",
      'id="ukLedger"' in _fr_page and 'id="ireLedger"' in _fr_page)
check("France: the serve copy matches the page",
      _fr_page == (ROOT / "dist" / "public" / "index.html").read_text(encoding="utf-8"))
_res_yml = (ROOT / ".github" / "workflows" / "results.yml").read_text(encoding="utf-8")
_ref_yml = (ROOT / ".github" / "workflows" / "refresh.yml").read_text(encoding="utf-8")
check("France: results are settled before the page is written",
      "tooling/settle_france.py" in _res_yml
      and _res_yml.index("settle_france.py") < _res_yml.index("Add completed races"))
check("France: the results job commits the French ledger",
      "data/results_fr" in _res_yml)
check("Chile: results are settled before the page is written",
      "tooling/settle_chile.py" in _res_yml
      and _res_yml.index("settle_chile.py") < _res_yml.index("Add completed races")
      and "data/results_cl" in _res_yml)
check("coverage: the audit runs from the build runner",
      "audit_coverage.py --inject" in _ref_yml)

print()
print("Refresh copy")
_page_now = (ROOT / "index.html").read_text(encoding="utf-8")
check("results tab: the refresh copy matches how runs are triggered now",
      "Auto pull still runs every 20 minutes" not in _page_now
      and "trigger a pull as soon as they are committed" in _page_now
      and "often runs that hours late" in _page_now)
check("results tab: the copy still says manual entries stay local",
      "Manual entries are saved in this browser only" in _page_now)

print()
print("Strict model")
_strict = re.search(r"var RATED_FACTORS = \[(.*?)\n  \];", _page_now, re.S)
_strict_src = _strict.group(1) if _strict else ""
check("model: a pick is scored on the six complete inputs only",
      all(k in _strict_src for k in ["'market'", "'form'", "'rating'",
                                     "'weight'", "'fitness'", "'connections'"])
      and _strict_src.count("['") == 6)
check("model: partial feeds are not model inputs",
      not any(k in _strict_src for k in ["rpr", "speed", "tips", "naps", "draw", "cd", "going"]))
check("model: every runner must carry every input or the race is unrated",
      "function cardCoverage" in _page_now and "ok: rs.length >= 4 && missing.length === 0" in _page_now)
# One field shared, the rest varying: the shared weight must not decide the race.
_shared_weight_runners = [
    {"horse": "Horse 1", "odds": "2.0", "form": "111",
     "or": "110", "weight": "11-4", "days": "14", "jp": "20", "tp": "16"},
    {"horse": "Horse 2", "odds": "4.0", "form": "2",
     "or": "105", "weight": "11-4", "days": "28", "jp": "12", "tp": "9"},
    {"horse": "Horse 3", "odds": "7.0", "form": "35",
     "or": "99", "weight": "11-4", "days": "42", "jp": "8", "tp": "5"},
    {"horse": "Horse 4", "odds": "11.0", "form": "6",
     "or": "94", "weight": "11-4", "days": "70", "jp": "4", "tp": "2"}]
check("model: a factor every runner shares rates the race instead of blocking it",
      (lambda got: bool(got) and got[0].get("rated") is True
       and got[0].get("pick") == "Horse 1")(
          rescore_results.score([{
              "course": "Test", "time": "13:00", "race": "Test Maiden Hurdle",
              "runners": _shared_weight_runners}])))
check("model: the archived 12-factor view still drops a shared factor",
      (lambda got: bool(got) and bool(got[0].get("pick"))
       and all(r["parts"].get("weight") is None for r in got[0].get("runners", []))
       and any(r["parts"].get("market") is not None
               for r in got[0].get("runners", [])))(
          settle.score_cards([{
              "course": "Test", "time": "13:00", "race": "Test Maiden Hurdle",
              "runners": _shared_weight_runners}])))
check("model: the missing field is named on the page",
      "for all ' + s.cov.runners + ' runners" in _page_now
      and "'has' : 'have') + ' no ' + m.what" in _page_now)
check("model: the paragraph cites only filed values and says so",
      "function ratedEvidence" in _page_now
      and "No input outside those six is used" in _page_now)
check("model: the place chance is the Harville sum of the win chances",
      "function topPlaceChance" in _page_now and "Harville" in _page_now)
check("model: the picks list is built from rated cards only",
      "function ratedPicks" in _page_now
      and "if (!cov.ok) {" in _page_now
      and "renderLeaderboard" in _page_now)

print()
print("Price recovery")
check("prices: only a value the page's decimalOdds reads counts as a price",
      rescore_results.price_text("7/2") == "7/2"
      and rescore_results.price_text("evs") == "evs"
      and rescore_results.price_text("5.50") == "5.5"
      and all(rescore_results.price_text(v) is None
              for v in ("", None, "0", "1", "9999999", "SP", "--")))
_fill = {"course": "Test", "runners": [
    {"horse": "Priced", "odds": "3.2"},
    {"horse": "Empty", "odds": ""},
    {"horse": "Gone", "odds": "", "scratched": True},
    {"horse": "Unknown", "odds": ""}]}
_key = lambda h: (rescore_results.norm("Test"), rescore_results.norm(h))
_copy, _used = rescore_results.priced(_fill, {
    _key("Empty"): ("5", "eachway_prices.jsonl 2026-01-02T13:00:00"),
    _key("Gone"): ("9", "eachway_prices.jsonl 2026-01-02T13:00:00")})
_got = {r["horse"]: r["odds"] for r in _copy["runners"]}
check("prices: an empty slot is filled, a stored price is never overwritten",
      _got == {"Priced": "3.2", "Empty": "5", "Gone": "", "Unknown": ""}
      and _used == {"eachway_prices.jsonl 2026-01-02T13:00:00"})
check("prices: the original card is left untouched",
      [r["odds"] for r in _fill["runners"]] == ["3.2", "", "", ""])
check("gaps: the scorer's own failure is never counted as a single field",
      rescore_results.blocker_of({"state": "incomplete", "missing": [
          {"what": "a factor did not score", "n": 6, "of": 6}]})
      == ("a factor did not score", False)
      and rescore_results.blocker_of({"state": "incomplete", "missing": [
          {"what": "price", "n": 2, "of": 8}]}) == ("price", True))

_tmp_dir = tempfile.mkdtemp()
try:
    _ew = os.path.join(_tmp_dir, "eachway.jsonl")
    with open(_ew, "w", encoding="utf-8") as f:
        for rec in (
            {"date": "2026-01-02", "course": "Test", "horse": "Keeper",
             "time": "14:00", "region": "GB", "win_odds": 4.0,
             "snapshot_utc": "2026-01-02T13:00:00+00:00"},
            {"date": "2026-01-02", "course": "Test", "horse": "Late",
             "time": "14:00", "region": "GB", "win_odds": 4.0,
             "snapshot_utc": "2026-01-02T14:00:01+00:00"},
            {"date": "2026-01-02", "course": "Test", "horse": "Untimed",
             "time": "", "region": "GB", "win_odds": 4.0,
             "snapshot_utc": "2026-01-02T10:00:00+00:00"},
            {"date": "2026-01-02", "course": "Test", "horse": "Overnight",
             "time": "", "region": "GB", "win_odds": 6.0,
             "snapshot_utc": "2026-01-01T20:00:00+00:00"},
            {"date": "2026-06-01", "course": "Summer", "horse": "BeforeOff",
             "time": "16:20", "region": "GB", "win_odds": 4.0,
             "snapshot_utc": "2026-06-01T14:30:00+00:00"},
            {"date": "2026-06-01", "course": "Summer", "horse": "AfterOff",
             "time": "16:20", "region": "GB", "win_odds": 4.0,
             "snapshot_utc": "2026-06-01T15:30:00+00:00"},
            {"date": "2026-06-01", "course": "Summer", "horse": "OffsetStamp",
             "time": "16:20", "region": "GB", "win_odds": 4.0,
             "snapshot_utc": "2026-06-01T15:00:00+01:00"},
            {"date": "2026-06-01", "course": "Elsewhere", "horse": "UnknownClock",
             "time": "16:20", "region": "US", "win_odds": 4.0,
             "snapshot_utc": "2026-06-01T15:30:00+00:00"}):
            f.write(json.dumps(rec) + "\n")
    _was = (rescore_results.EACHWAY, rescore_results.CARDS)
    rescore_results.EACHWAY = _ew
    rescore_results.CARDS = os.path.join(_tmp_dir, "cards")
    try:
        _idx = rescore_results.price_index("2026-01-02")
        _idx_s = rescore_results.price_index("2026-06-01")
    finally:
        rescore_results.EACHWAY, rescore_results.CARDS = _was
    _summer = lambda h: (rescore_results.norm("Summer"), rescore_results.norm(h))
    check("prices: a snapshot taken before the off is used",
          _idx.get(_key("Keeper")) is not None)
    check("prices: a snapshot at or after the off is refused",
          _key("Late") not in _idx)
    check("prices: a same-day snapshot with no off time cannot prove it is pre-race",
          _key("Untimed") not in _idx)
    check("prices: a snapshot from before the race day is used without an off time",
          _idx.get(_key("Overnight")) is not None)
    check("prices: a summer snapshot is read on the course's clock, not as UTC",
          _summer("BeforeOff") in _idx_s and _summer("AfterOff") not in _idx_s)
    check("prices: an offset stamp is converted to UTC before the off check",
          _summer("OffsetStamp") in _idx_s)
    check("prices: a venue with no single clock cannot prove a same-day snapshot",
          (rescore_results.norm("Elsewhere"),
           rescore_results.norm("UnknownClock")) not in _idx_s)
finally:
    shutil.rmtree(_tmp_dir, ignore_errors=True)

print()
print("Screens")


def _screen_runner(horse, total, share, odds, dec, chance):
    return {"horse": horse, "total": total, "share": share,
            "odds": odds, "dec": dec, "chance": chance}


_field = [
    _screen_runner("Favourite", 80.0, 0.40, "2.5", 2.5, 38.0),
    _screen_runner("Second", 70.0, 0.20, "5", 5.0, 20.0),
    _screen_runner("Long", 60.0, 0.10, "9", 9.0, 12.0),
    _screen_runner("Fourth", 50.0, 0.05, "20", 20.0, 5.0),
    _screen_runner("Last", 40.0, 0.02, "50", 50.0, 2.0),
]
_screens = screen_results.screen_race({"rated": True, "pick": "Favourite",
                                       "runners": _field})
check("screens: a 7/1+ runner in the top four with a chance above the price is a longshot",
      [r["horse"] for r in _screens["longshots"]] == ["Long"],
      [r["horse"] for r in _screens["longshots"]])
check("screens: the model's last-rated runner is the lay",
      (_screens["lay"] or {}).get("horse") == "Last", _screens["lay"])
check("screens: a favourite the model rates first and near the price is not doubted",
      _screens["overhyped"] == [], _screens["overhyped"])
check("screens: the model's top pick, also the favourite, in a field of five is a banker",
      [r["horse"] for r in _screens["placers"]] == ["Favourite"],
      _screens["placers"])

_doubt = screen_results.screen_race({"rated": True, "pick": "Top", "runners": [
    _screen_runner("Top", 90.0, 0.20, "5", 5.0, 20.0),
    _screen_runner("Mid", 80.0, 0.22, "4.5", 4.5, 22.0),
    _screen_runner("Fav", 70.0, 0.40, "2.2", 2.2, 30.0),
    _screen_runner("Tail", 60.0, 0.05, "25", 25.0, 3.0),
    _screen_runner("End", 50.0, 0.02, "66", 66.0, 1.0),
]})
check("screens: a favourite the model ranks third is doubted",
      [r["horse"] for r in _doubt["overhyped"]] == ["Fav"], _doubt["overhyped"])

_unpriced = screen_results.screen_race({"rated": True, "pick": "A", "runners": [
    _screen_runner("A", 90.0, 0.5, "", None, 50.0),
    _screen_runner("B", 80.0, 0.5, "", None, 50.0)]})
check("screens: without a card price nothing is laid",
      _unpriced["lay"] is None, _unpriced)

_base = {"date": "2026-01-02", "course": "Test", "time": "13:30",
         "race": "", "winner": "Favourite", "region": "GB"}
_result = {"course": "Test", "time": "13:30", "winner": "Favourite",
           "places": ["Favourite", "Second"], "place_places": 2}
_lay = screen_results._settle(_base, _field[-1], _result)
check("screens: a lay settles as won when its horse did not win",
      _lay is not None and _lay["won"] is False, _lay)
check("screens: a beaten runner outside the stored places has no placing",
      _lay is not None and screen_results._finish_text(_lay) == "&mdash;", _lay)
_second = screen_results._settle(
    _base, _screen_runner("Second", 70.0, 0.20, "5", 5.0, 20.0), _result)
check("screens: a beaten runner in the stored places prints its placing",
      _second is not None and screen_results._finish_text(_second) == "2nd", _second)
_nr = screen_results._settle(_base, _field[-1],
                             {"winner": "Favourite", "finish": {"Last": "NR"}})
check("screens: a non-runner is left out of the record", _nr is None, _nr)

_money = screen_results.lay_money([
    {"dec": 5.0, "won": False},    # the lay won: one unit back
    {"dec": 31.0, "won": True},    # the horse won: thirty units lost
])
check("screens: the lay record is weighed by liability, not by strike rate",
      _money["net"] == -29.0 and _money["risked"] == 34.0
      and abs(_money["pct"] + 85.2941) < 0.01, _money)
check("screens: the largest single lay is reported beside the net",
      _money["worst"] == 30.0, _money)

_lay_html = screen_results.lay_block([
    {"date": "2026-01-02", "course": "Test", "time": "13:30", "region": "GB",
     "horse": "A", "price": "5", "dec": 5.0, "chance": 10.0, "rank": 2,
     "finish": "", "won": False, "placed": None},
    {"date": "2026-01-02", "course": "Test", "time": "14:00", "region": "GB",
     "horse": "B", "price": "31", "dec": 31.0, "chance": 2.0, "rank": 6,
     "finish": "1st", "won": True, "placed": None},
])
check("screens: the lay panel prints the net against the liability it risked",
      "34.0" in _lay_html and "-85.3%" in _lay_html, _lay_html[:300])


def _panel_row(**kw):
    row = {"date": "2026-01-02", "course": "Test", "time": "13:30",
           "region": "GB", "horse": "Runner", "price": "9", "dec": 9.0,
           "chance": 12.0, "rank": 3, "finish": "", "won": False,
           "placed": None}
    row.update(kw)
    return row


_long_html = screen_results.longshot_block([
    _panel_row(horse="Long Win", won=True, finish="1st", placed=True),
    _panel_row(horse="Long Beaten", time="14:00"),
])
check("screens: the longshot panel prints the strike rate, the frame and a dash "
      "where no placing is on file",
      "1</b> won (50.0%)" in _long_html and "Long Beaten" in _long_html
      and "were in the frame" in _long_html and "&mdash;</td>" in _long_html,
      _long_html[:300])

_over_html = screen_results.overhyped_block([
    _panel_row(horse="Doubted", finish="2nd", placed=True),
    _panel_row(horse="Held", time="14:00", won=True, finish="1st", placed=True),
])
check("screens: the overhyped panel prints the doubt verdict for each favourite",
      "were beaten (50.0%)" in _over_html and "Doubted" in _over_html
      and ">yes</td>" in _over_html and ">no</td>" in _over_html,
      _over_html[:300])

_place_html = screen_results.placer_block([
    _panel_row(horse="Placed Banker", finish="2nd", placed=True),
    _panel_row(horse="Missing Banker", time="14:00"),
])
check("screens: the place banker panel separates placed from not on file",
      "1</b> placed under the standard terms" in _place_html
      and "1 are not named in the places" in _place_html
      and "placed yes" in _place_html and "placed unknown" in _place_html,
      _place_html[:300])

check("screens: an empty screen says what it needs and prints no table",
      "<table" not in screen_results.longshot_block([])
      and "settled result" in screen_results.longshot_block([]),
      screen_results.longshot_block([])[:200])

_empty_all = {name: [] for name in screen_results.SCREENS}
_empty_page = "\n".join(screen_results.BUILDERS[name]([])
                         for name in screen_results.SCREENS)
check("screens: a panel that matches its fresh build is not reported stale",
      screen_results.stale_panels(_empty_page, _empty_all) == [],
      screen_results.stale_panels(_empty_page, _empty_all))
check("screens: a panel that drifted from its fresh build is reported stale",
      screen_results.stale_panels(_empty_page.replace("No longshot has been",
                                                      "No longshot was"),
                                  _empty_all) == ["LONG_RESULTS_SL"],
      screen_results.stale_panels(_empty_page.replace("No longshot has been",
                                                      "No longshot was"),
                                  _empty_all))

print()
print("Coverage figures")

# The README's coverage paragraph is generated from the stored re-score, so the
# counts and the wording they produce are fixtures of their own.
_COVER_DAY = {
    "date": "2026-01-02",
    "races": [
        {"state": "rated", "hit": True, "region": "GB"},
        {"state": "rated", "hit": False, "region": "GB",
         "price_source": "eachway"},
        {"state": "incomplete", "region": "US", "blocker": "days since a run"},
    ],
}
_COVER_FIGS = coverage_figures.collect([_COVER_DAY, _COVER_DAY])
check("coverage: settled, rated, winners, restored prices and the rated regions "
      "are counted from the stored re-score",
      _COVER_FIGS == {"settled": 6, "rated": 4, "winners": 2, "restored": 2,
                      "regions": ["GB"]}, _COVER_FIGS)

_COVER_NONE = coverage_figures.sentence({"settled": 3, "rated": 0,
                                         "winners": 0, "restored": 0,
                                         "regions": []})
check("coverage: with nothing rated the sentence says so instead of printing a "
      "zero strike rate",
      _COVER_NONE == "Coverage at this build is 0 of 3 settled races: no "
                     "settled race carries a six-input pick yet.", _COVER_NONE)

_COVER_ONE = coverage_figures.sentence({"settled": 10, "rated": 3,
                                        "winners": 1, "restored": 0,
                                        "regions": ["GB"]})
check("coverage: one winner stays singular and a British-only record says "
      "British, not British or Irish",
      _COVER_ONE == "Coverage at this build is 3 of 10 settled races (1 "
                    "winner), all on British cards.", _COVER_ONE)

_COVER_TWO = coverage_figures.sentence({"settled": 10, "rated": 4,
                                        "winners": 0, "restored": 2,
                                        "regions": ["GB", "IRE"]})
check("coverage: two regions read as British or Irish and the restored races "
      "are named as the reason they rate",
      "(0 winners)" in _COVER_TWO and "British or Irish cards" in _COVER_TWO
      and "2 of those were held out by price alone" in _COVER_TWO, _COVER_TWO)

_COVER_MARKED = ("intro\n\n  " + coverage_figures.START + "\n"
                 "  typed figures from an older build\n  "
                 + coverage_figures.END + "\n\nafter\n")
_COVER_WRITTEN = coverage_figures.replace_block(
    _COVER_MARKED, {"settled": 10, "rated": 3, "winners": 1, "restored": 0,
                    "regions": ["GB"]})
check("coverage: the marked paragraph is rewritten in place, keeps the bullet's "
      "indent and leaves the rest of the README alone",
      "typed figures" not in _COVER_WRITTEN
      and "  Coverage at this build is 3 of 10" in _COVER_WRITTEN
      and _COVER_WRITTEN.startswith("intro\n\n  " + coverage_figures.START)
      and _COVER_WRITTEN.endswith(coverage_figures.END + "\n\nafter\n"),
      _COVER_WRITTEN[:240])

_COVER_FIGS_ONE = {"settled": 10, "rated": 3, "winners": 1, "restored": 0,
                   "regions": ["GB"]}
check("coverage: a paragraph that matches a fresh build is current, a nudged "
      "one is stale",
      not coverage_figures.stale(_COVER_WRITTEN, _COVER_FIGS_ONE)
      and coverage_figures.stale(_COVER_WRITTEN, dict(_COVER_FIGS_ONE,
                                                      settled=11)),
      _COVER_WRITTEN[:120])

try:
    coverage_figures.replace_block("a README with no markers")
    _cover_refused = False
except ValueError:
    _cover_refused = True
check("coverage: replace_block refuses a README with no markers rather than "
      "returning it untouched", _cover_refused, "no error raised")

_COVER_TMP = os.path.join(tempfile.mkdtemp(), "README.md")
with open(_COVER_TMP, "w", encoding="utf-8") as f:
    f.write(_COVER_MARKED)
_cover_first = coverage_figures.write(_COVER_TMP, _COVER_FIGS_ONE)
_cover_second = coverage_figures.write(_COVER_TMP, _COVER_FIGS_ONE)
check("coverage: writing is idempotent, so a render that changes nothing writes "
      "no file",
      _cover_first and not _cover_second, (_cover_first, _cover_second))

print()
print("Caveat figures")

# The Known caveats bullets are pinned to the 12 November card, so the counts
# and the wording they produce are fixtures of their own.
_CAVEAT_PICKS = [
    {"course": "Hereford", "time": "12:30", "winner": "Alpha", "pick": "Alpha"},
    {"course": "Hereford", "time": "13:05", "winner": "Bravo", "pick": "Charlie"},
    {"course": "Thurles", "time": "14:38", "winner": "Foxtrot", "pick": "Foxtrot"},
    {"course": "Thurles", "time": "15:15", "winner": "Hotel", "pick": "India"},
]
_CAVEAT_PRICES = [
    {"course": "Hereford", "time": "12:30", "horse": "Alpha", "dec": "2.5"},
    {"course": "Hereford", "time": "12:30", "horse": "Bravo", "dec": "4.0"},
    {"course": "Hereford", "time": "13:05", "horse": "Bravo", "dec": "3.0"},
    {"course": "Hereford", "time": "13:05", "horse": "Charlie", "dec": "3.0"},
    {"course": "Thurles", "time": "14:38", "horse": "Delta",
     "dec": "4.3333333333333"},
    {"course": "Thurles", "time": "14:38", "horse": "Foxtrot", "dec": "6.0"},
    {"course": "Thurles", "time": "15:15", "horse": "Golf", "dec": "4.0"},
    {"course": "Thurles", "time": "15:15", "horse": "Hotel", "dec": "7.0"},
]
_CAVEAT_FIGS = caveat_figures.collect(_CAVEAT_PICKS, _CAVEAT_PRICES)
check("caveat: favourites, the pick's record on them and on the rest are counted "
      "from the stored picks and prices, and a joint favourite is counted once",
      _CAVEAT_FIGS == {"races": 4, "favourites": 2, "favourites_found": 1,
                       "others": 2, "others_found": 1}, _CAVEAT_FIGS)

check("caveat: the two sentences read as the caveat's bullets do",
      caveat_figures.sentence(_CAVEAT_FIGS)
      == "The model is a favourite-ranker. On this card it found 1 of the 2 "
         "winning favourites and 1 of the 2 winners that weren't the favourite."
      and caveat_figures.sample(_CAVEAT_FIGS)
      == "4 races is a tiny sample. The weights were tuned in-sample; treat any "
         "improvement of one or two winners as noise.")

_CAVEAT_GAP = None
try:
    caveat_figures.collect([_CAVEAT_PICKS[0]], [])
except ValueError as err:
    _CAVEAT_GAP = str(err)
check("caveat: a race with no stored starting prices is named, not dropped "
      "from the count",
      bool(_CAVEAT_GAP) and "Hereford 12:30" in _CAVEAT_GAP, _CAVEAT_GAP)

_CAVEAT_MARKED = ("intro\n\n" + caveat_figures.START + "\n"
                  "* typed figures from an older card\n"
                  + caveat_figures.END + "\n\nafter\n")
_CAVEAT_WRITTEN = caveat_figures.replace_block(_CAVEAT_MARKED, _CAVEAT_FIGS)
check("caveat: the marked bullets are rewritten in place and the surrounding "
      "caveats are untouched",
      "typed figures" not in _CAVEAT_WRITTEN
      and _CAVEAT_WRITTEN.startswith("intro\n\n" + caveat_figures.START)
      and _CAVEAT_WRITTEN.endswith(caveat_figures.END + "\n\nafter\n"),
      _CAVEAT_WRITTEN[:200])

check("caveat: a block that matches a fresh count is current, a nudged one is "
      "stale",
      not caveat_figures.stale(_CAVEAT_WRITTEN, _CAVEAT_FIGS)
      and caveat_figures.stale(_CAVEAT_WRITTEN, dict(_CAVEAT_FIGS, races=6)),
      _CAVEAT_WRITTEN[:120])

try:
    caveat_figures.replace_block("a README with no markers")
    _caveat_refused = False
except ValueError:
    _caveat_refused = True
check("caveat: replace_block refuses a README with no markers rather than "
      "returning it untouched", _caveat_refused, "no error raised")

_CAVEAT_TMP = os.path.join(tempfile.mkdtemp(), "README.md")
Path(_CAVEAT_TMP).write_text(_CAVEAT_MARKED, encoding="utf-8")
check("caveat: writing is idempotent, so a render that changes nothing writes "
      "no file",
      caveat_figures.write(_CAVEAT_TMP, _CAVEAT_FIGS)
      and not caveat_figures.write(_CAVEAT_TMP, _CAVEAT_FIGS))

print()
print("Data integrity")

# Any entity reference left in a stored row means a scraper unescaped its text
# late or not at all. Derived re-scores are skipped: they carry the same text
# as the results they were built from, which this scan already covers.
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#\d{1,7}|#[xX][0-9a-fA-F]{1,6});")


def _row_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _row_texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _row_texts(item)


_entity_rows = []
for _path in sorted((ROOT / "data").glob("results*/*.json")):
    if _path.parent.name == "results_rated":
        continue
    for _text in _row_texts(json.loads(_path.read_text(encoding="utf-8"))):
        if _ENTITY.search(_text):
            _entity_rows.append("%s: %s" % (_path.relative_to(ROOT), _text))
check("integrity: no stored results row carries an HTML entity",
      not _entity_rows, _entity_rows[:3])

# One re-score per settled day, shared by the drift check and the screens.
_rated_paths = sorted((ROOT / "data" / "results_rated").glob("*.json"))
_days = sorted({_path.stem for _path in _rated_paths}
               | {_path.stem for _path in (ROOT / "data" / "results").glob("*.json")})
_rated = {_iso: rescore_results.rate_day(_iso) for _iso in _days}

_drifted = [_path.stem for _path in _rated_paths
            if json.loads(_path.read_text(encoding="utf-8"))
            != rescore_results.day(_path.stem, _rated[_path.stem])]
check("integrity: every stored re-score matches a fresh run",
      bool(_rated_paths) and not _drifted, _drifted)

# The source page and the built copy both have to carry this build's record.
_everything = screen_results.collect(_rated)
for _label, _path in (("index.html", ROOT / "index.html"),
                      ("dist/public/index.html",
                       ROOT / "dist" / "public" / "index.html")):
    _text = _path.read_text(encoding="utf-8") if _path.exists() else ""
    _stale = screen_results.stale_panels(_text, _everything)
    check("integrity: the screen panels in %s match a fresh build from the "
          "stored re-score" % _label, not _stale, _stale)

# The coverage paragraph is generated, so a settled day the README has not
# caught up with is a stale document, not a rounding difference.
_readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
_stale_readme = coverage_figures.stale(_readme_text)
check("integrity: the README coverage paragraph matches the stored re-score",
      not _stale_readme, coverage_figures.sentence(coverage_figures.collect()))

# The caveat block is pinned to one card, so a stored pick or starting price
# that no longer produces the committed figures is a stale document too.
check("integrity: the README caveat block matches the 12 November card",
      not caveat_figures.stale(_readme_text),
      caveat_figures.sentence(caveat_figures.collect()))

print()
print("passed %d, failed %d%s" % (
    len(PASSED), len(FAILED), (", skipped %d" % len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
