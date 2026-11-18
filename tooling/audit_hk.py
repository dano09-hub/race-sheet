#!/usr/bin/env python3
"""Score Hong Kong factors on prices that existed before the off.

The Hong Kong card is filed before betting opens, so the pick has been made on
form, official rating, draw and weight alone. This audit asks the only question
worth asking before any weight changes: with a price that was actually on the
board twenty minutes before the off, do form, rating or weight rank the winner
better than the crowd already does?

    python3 tooling/audit_hk.py                # all filed snapshots
    python3 tooling/audit_hk.py --dev 0.6      # a different chronological cut
    python3 tooling/audit_hk.py --csv out.csv  # one row per race

No look-ahead is possible by construction: a race enters the audit only when a
pre-off snapshot was filed for it, the archived card's own odds are discarded,
and the price column is read from that snapshot. A race whose snapshot was
taken after the off, or whose market was not fully published, is skipped and
counted - never filled in from the archive, which holds post-race odds.

The verdict comes from the latest meetings only, because a split chosen after
looking at the whole history is not a holdout. Scores come from the page's own
scorer (tooling/score_cards.js), so the factor values here are the ones the
sheet publishes, not a second copy that can drift.
"""
import argparse
import contextlib
import glob
import io
import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import snapshot_hk as shk                                  # noqa: E402

# name -> the page's factor key. Price is the crowd's own ranking.
SIGNALS = (("price", "market"), ("form", "form"),
           ("rating", "rating"), ("weight", "weight"))
LABEL = {"price": "price", "form": "form",
         "rating": "official rating", "weight": "weight carried"}

# A verdict needs a holdout that can actually resolve a difference. This is a
# floor, not a licence to read a small edge as a signal.
MIN_HOLDOUT_MEETINGS = 5
MIN_HOLDOUT_RACES = 40


def _result(root, date_iso, race_no, cache):
    """The official finish for one race, normalised by horse name."""
    if date_iso not in cache:
        path = os.path.join(root, "data", "results_hk", f"{date_iso}.json")
        try:
            with open(path, encoding="utf-8") as f:
                cache[date_iso] = json.load(f)
        except (OSError, ValueError):
            cache[date_iso] = {}
    for race in (cache[date_iso].get("races") or []):
        if int(race.get("race_no") or 0) != race_no:
            continue
        finish = {}
        for horse, place in (race.get("finish") or {}).items():
            try:
                finish[shk._key(horse)] = int(place)
            except (TypeError, ValueError):
                continue
        if not finish:
            return None
        winner = shk._key(race.get("winner") or "")
        if not winner or winner not in finish:
            winner = min(finish, key=lambda h: finish[h])
        return {"finish": finish, "winner": winner}
    return None


def load(root=ROOT):
    """Meetings with a pre-off snapshot and an official finish, oldest first."""
    meetings, skipped = {}, {"late": 0, "unpriced": 0, "no_card": 0, "no_result": 0}
    results = {}
    for path in sorted(glob.glob(os.path.join(root, "data", "hk_preoff", "*", "r*.json"))):
        date_iso = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, ValueError):
            continue
        # A snapshot filed after the off is not a price anyone could have bet.
        try:
            lead = int(snap.get("lead_seconds"))
        except (TypeError, ValueError):
            lead = 0
        if lead <= 0:
            skipped["late"] += 1
            continue
        race_no = int(snap.get("race_no") or 0)
        card = next((c for c in shk._cards(date_iso, root)
                     if shk._race_no(c) == race_no), None)
        if not card:
            skipped["no_card"] += 1
            continue
        result = _result(root, date_iso, race_no, results)
        if not result:
            skipped["no_result"] += 1
            continue
        prices = {shk._key(r.get("horse")): str(r.get("odds") or "").strip()
                  for r in snap.get("runners") or []}
        runners = []
        for runner in card.get("runners") or []:
            row = dict(runner)
            # The archive's odds are post-race. Only the snapshot may be used.
            row["odds"] = prices.get(shk._key(runner.get("horse")), "")
            runners.append(row)
        if not runners or any(not r["odds"] for r in runners):
            skipped["unpriced"] += 1
            continue
        merged = {k: v for k, v in card.items() if k != "result"}
        merged["runners"] = runners
        meetings.setdefault(date_iso, {"date": date_iso, "races": []})["races"].append({
            "date": date_iso, "race_no": race_no, "card": merged,
            "finish": result["finish"], "winner": result["winner"],
        })
    out = [meetings[d] for d in sorted(meetings)]
    for meeting in out:
        meeting["races"].sort(key=lambda r: r["race_no"])
    return out, skipped


def score(meetings, page):
    """Run the page's own scorer over the merged cards."""
    cards = [race["card"] for meeting in meetings for race in meeting["races"]]
    if not cards:
        return {}
    with tempfile.TemporaryDirectory(prefix="hk-audit-") as tmp:
        cards_in = os.path.join(tmp, "cards.json")
        cards_out = os.path.join(tmp, "scores.json")
        with open(cards_in, "w", encoding="utf-8") as f:
            json.dump(cards, f, ensure_ascii=False)
        run = subprocess.run(
            ["node", os.path.join(HERE, "score_cards.js"), cards_in, cards_out, page],
            capture_output=True, text=True)
        if run.returncode != 0:
            raise RuntimeError((run.stderr or run.stdout).strip()[:500])
        with open(cards_out, encoding="utf-8") as f:
            scored = json.load(f)
    return {card.get("id"): card for card in scored}


def rank(parts, numbers, key):
    """Horses best first under one factor; ties go to the lower saddlecloth.

    Ties have to be broken by something known before the race, so the saddlecloth
    (a draw for the card's positions, not a racing opinion) is the only fair key.
    """
    value = {}
    for horse, row in parts.items():
        v = (row.get("parts") or {}).get(key)
        if v is None:
            return None
        value[horse] = float(v)
    return sorted(value, key=lambda h: (-value[h], numbers.get(h, 99)))


def table(meetings, scored):
    """One record per race, with every signal's full ranking."""
    races = []
    for meeting in meetings:
        for race in meeting["races"]:
            card = scored.get(race["card"].get("id"))
            if not card:
                continue
            parts = {shk._key(r.get("horse")): r for r in card.get("runners") or []}
            numbers = {shk._key(r.get("horse")): int(r.get("no") or 0)
                       for r in race["card"]["runners"]}
            if race["winner"] not in parts:
                continue
            picks = {}
            for name, key in SIGNALS:
                order = rank(parts, numbers, key)
                if order:
                    picks[name] = order
            if "price" not in picks:
                continue
            races.append({"date": meeting["date"], "race_no": race["race_no"],
                          "winner": race["winner"], "picks": picks,
                          "field": len(picks["price"])})
    return races


def split(races, dev=0.5):
    """The earliest meetings for development, the latest for the verdict."""
    dates = sorted({r["date"] for r in races})
    if len(dates) < 2:
        return races, []
    cut = min(len(dates) - 1, max(1, int(round(len(dates) * dev))))
    early = set(dates[:cut])
    return ([r for r in races if r["date"] in early],
            [r for r in races if r["date"] not in early])


def stats(races, name):
    rows = [r for r in races if name in r["picks"]]
    if not rows:
        return None
    ranks = [r["picks"][name].index(r["winner"]) + 1 for r in rows]
    return {"races": len(rows),
            "wins": sum(1 for x in ranks if x == 1),
            "rank": sum(ranks) / len(ranks),
            "top3": sum(1 for x in ranks if x <= 3),
            "field": sum(r["field"] for r in rows) / len(rows)}


def paired(races, name, base="price"):
    """Head-to-head with the favourite, on races where the picks differ."""
    for_it = against = neither = 0
    for race in races:
        if name not in race["picks"] or base not in race["picks"]:
            continue
        mine, fav = race["picks"][name][0], race["picks"][base][0]
        if mine == fav:
            continue
        if mine == race["winner"]:
            for_it += 1
        elif fav == race["winner"]:
            against += 1
        else:
            neither += 1
    n = for_it + against
    if not n:
        return None
    # One-sided exact sign test: how often this would happen by chance.
    p = sum(math.comb(n, k) for k in range(for_it, n + 1)) / (2 ** n)
    return {"for": for_it, "against": against, "neither": neither, "n": n, "p": p}


def _p(value):
    return "< 0.0001" if value < 0.0001 else "%.4f" % value


def _dates(races):
    return sorted({r["date"] for r in races})


def _emit(text, out=None):
    """Print the report and, when asked, record the same text for the repo."""
    sys.stdout.write(text)
    if not out:
        return
    folder = os.path.dirname(os.path.abspath(out))
    if folder:
        os.makedirs(folder, exist_ok=True)
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            if f.read() == text:
                return                      # unchanged: leave the file alone
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)


def report(races, skipped, dev=0.5):
    if not races:
        print("Hong Kong factor audit: nothing scorable yet - no race has both a "
              "pre-off snapshot and an official finish")
        return
    early, late = split(races, dev)
    meetings_n = {"early": len({r["date"] for r in early}),
                  "late": len({r["date"] for r in late})}
    print("Hong Kong factor audit - prices filed before the off")
    print(f"  snapshots: {len(races)} races at {len(_dates(races))} meetings, "
          f"{_dates(races)[0]}..{_dates(races)[-1]}")
    if any(skipped.values()):
        print(f"  skipped:   {skipped['late']} filed after the off, "
              f"{skipped['unpriced']} without a full market, "
              f"{skipped['no_card']} without a card, "
              f"{skipped['no_result']} without an official finish")
    print(f"  split:     development {meetings_n['early']} meetings "
          f"({len(early)} races), holdout {meetings_n['late']} meetings "
          f"({len(late)} races)")
    if not late:
        print("\n  Not enough meetings to hold any back yet. Nothing is scored "
              "from the archive's post-race odds, so the audit waits for more "
              "pre-off snapshots.")
        return

    print(f"\n  holdout only ({meetings_n['late']} meetings, {len(late)} races)")
    print(f"  {'signal':>16} {'races':>6} {'won':>5} {'win%':>6} "
          f"{'winner rank':>12} {'top-3%':>7} {'field':>6}")
    for name, _ in SIGNALS:
        s = stats(late, name)
        if not s:
            print(f"  {LABEL[name]:>16} {'-':>6} {'-':>5} {'-':>6} {'-':>12} "
                  f"{'-':>7} {'-':>6}")
            continue
        print(f"  {LABEL[name]:>16} {s['races']:>6} {s['wins']:>5} "
              f"{100.0 * s['wins'] / s['races']:>5.1f}% {s['rank']:>12.2f} "
              f"{100.0 * s['top3'] / s['races']:>6.1f}% {s['field']:>6.1f}")

    print(f"\n  holdout, head to head with the favourite (different picks only)")
    print(f"  {'signal':>16} {'tested':>6} {'signal':>7} {'fav':>5} "
          f"{'neither':>8} {'p (one-sided)':>14}")
    verdicts = []
    for name, _ in SIGNALS:
        if name == "price":
            continue
        head = paired(late, name)
        if not head:
            print(f"  {LABEL[name]:>16} {'0':>6} {'-':>7} {'-':>5} {'-':>8} {'-':>14}")
            continue
        print(f"  {LABEL[name]:>16} {head['n']:>6} {head['for']:>7} "
              f"{head['against']:>5} {head['neither']:>8} "
              f"{_p(head['p']):>14}")
        verdicts.append((name, head))

    enough = (meetings_n["late"] >= MIN_HOLDOUT_MEETINGS
              and len(late) >= MIN_HOLDOUT_RACES)
    print()
    if not enough:
        print(f"  No verdict yet: the holdout holds {len(late)} races at "
              f"{meetings_n['late']} meetings; {MIN_HOLDOUT_RACES} races at "
              f"{MIN_HOLDOUT_MEETINGS} meetings are the floor. The table above "
              "is a description of one small sample, not evidence.")
        return
    said = False
    for name, head in verdicts:
        if head["p"] < 0.05:
            side = ("beats the favourite" if head["for"] > head["against"]
                    else "loses to the favourite")
            p_txt = ("p < 0.0001" if head["p"] < 0.0001
                     else "p = %.4f" % head["p"])
            print(f"  {LABEL[name]}: {side} on the holdout, "
                  f"{head['for']}-{head['against']}, {p_txt}.")
            said = True
    if not said:
        print("  On this holdout no factor separates from the favourite "
              "(p >= 0.05 for every one). A weight change would be invention.")
    print("  Even a clean result here earns only a pre-registered shadow arm, "
          "never a weight edit on the live page.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dev", type=float, default=0.5,
                    help="share of meetings used for development (default 0.5)")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--page", default=None,
                    help="page whose scorer to use (default <root>/index.html)")
    ap.add_argument("--out", default=None,
                    help="also record the report at this path")
    args = ap.parse_args(argv)

    meetings, skipped = load(args.root)
    if not meetings:
        _emit("Hong Kong factor audit: no pre-off snapshots filed yet.\n"
              "  tooling/snapshot_hk.py files one per race; this audit fills in\n"
              "  as data/hk_preoff/ grows, and it never scores from the archive's\n"
              "  post-race odds.\n", args.out)
        return 0
    try:
        scored = score(meetings, args.page or os.path.join(args.root, "index.html"))
    except (OSError, RuntimeError) as e:
        print(f"could not score the cards: {e}", file=sys.stderr)
        return 1
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(table(meetings, scored), skipped, args.dev)
    _emit(buf.getvalue(), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
