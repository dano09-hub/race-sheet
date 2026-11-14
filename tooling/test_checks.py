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
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLING = Path(__file__).resolve().parent
ROOT = TOOLING.parent
sys.path.insert(0, str(TOOLING))

import ab_tally        # noqa: E402
import collect_eachway_prices as cep  # noqa: E402
import eachway         # noqa: E402
import fetch_formfav as ff  # noqa: E402
import refresh         # noqa: E402
import recheck_market  # noqa: E402
import settle          # noqa: E402

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
    check("short sample verdict", t["verdict"].startswith("Not yet meaningful"), t["verdict"])
    check("shortfall maths 194/15", "194 more settled races and 15 more" in t["verdict"], t["verdict"])
    html = refresh.ab_block_html(t, "2026-11-11")
    check("panel has both days", "2026-11-10" in html and "2026-11-11" in html)
    check("panel headline 50.0%", "50.0%" in text_of(html), text_of(html)[:120])
    check("one block only", html.count("AB_SL_START") == 1)

print("4. 300 races, rating ahead -> says it is adding winners")
with project(days=[("2026-11-0%d" % (i + 1), 60, 27, 15, 8) for i in range(5)]):
    t = ab_tally.collect()
    check("300 races", t["races"] == 300, t["races"])
    check("enough flag", t["enough"] is True)
    check("verdict positive", t["verdict"].startswith("The rating's interval sits above"), t["verdict"])
    check("CI with clears CI without", t["ci_with"][0] > t["ci_without"][1],
          "%s vs %s" % (t["ci_with"], t["ci_without"]))

print("5. 300 races, rating behind -> says it is hurting")
with project(days=[("2026-11-0%d" % (i + 1), 60, 12, 24, 8) for i in range(5)]):
    t = ab_tally.collect()
    check("verdict negative", t["verdict"].startswith("The rating is hurting"), t["verdict"])

print("6. 300 races, dead heat -> no separation")
with project(days=[("2026-11-0%d" % (i + 1), 60, 21, 21, 8) for i in range(5)]):
    t = ab_tally.collect()
    check("verdict no separation", "no separation" in t["verdict"], t["verdict"])

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
    day13 = json.load(open(os.path.join(atmp, "data", "cards", "by_date", "2026-11-13.json"), encoding="utf-8"))
    day14 = json.load(open(os.path.join(atmp, "data", "cards", "by_date", "2026-11-14.json"), encoding="utf-8"))
    snap = json.load(open(os.path.join(atmp, "data", "cards", "2026-11-13.json"), encoding="utf-8"))
    check("later run keeps meetings an earlier run archived", "GB" in day13 and "US" in day13, list(day13))
    check("repeat run does not duplicate a race", len(day13.get("GB", [])) == 1, len(day13.get("GB", [])))
    check("repeat run refreshes the stored card", day13["GB"][0].get("note") == "prices added", day13["GB"][0].get("note"))
    check("tomorrow's GB card is filed under the day it runs",
          len(day14.get("GB", [])) == 1 and day14["GB"][0]["course"] == "Navan", day14.get("GB"))
    check("dated snapshot still written for the run", set(snap) == {"GB", "US"}, list(snap))
finally:
    refresh.ROOT = old_root
    shutil.rmtree(atmp, ignore_errors=True)

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
    _checker = _root / "tooling" / "check_factor_docs.py"
    shutil.copyfile(ROOT / "tooling" / "check_factor_docs.py", _checker)
    _ok = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                         capture_output=True, text=True)
    check("factor docs: baseline passes", _ok.returncode == 0,
          _ok.stdout + _ok.stderr)
    _readme = _root / "README.md"
    _readme_text = _readme.read_text(encoding="utf-8")
    _readme.write_text(
        _readme_text.replace("Twelve factors", "Eleven factors", 1),
        encoding="utf-8")
    _bad = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                          capture_output=True, text=True)
    _out = _bad.stdout + _bad.stderr
    check("factor docs: wrong documented count is rejected",
          _bad.returncode != 0 and "factor count" in _out, _out)
    _readme.write_text(
        _readme_text.replace("full array totals 104", "full array totals 100", 1),
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
    _method_text = _index_text.replace("<dt>Draw (4)</dt>", "<dt>Draw (5)</dt>", 1)
    if _method_text == _index_text:
        check("factor docs: Method-tab weight fixture found", False,
              "Draw (4) not found")
    _index.write_text(_method_text, encoding="utf-8")
    _method_bad = subprocess.run([sys.executable, str(_checker)], cwd=_root,
                                 capture_output=True, text=True)
    _method_out = _method_bad.stdout + _method_bad.stderr
    check("factor docs: wrong Method-tab weight is rejected",
          _method_bad.returncode != 0 and "Method tab weights" in _method_out,
          _method_out)

print()
print("passed %d, failed %d%s" % (
    len(PASSED), len(FAILED), (", skipped %d" % len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
