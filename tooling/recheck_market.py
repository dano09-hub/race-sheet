#!/usr/bin/env python3
"""Re-score a saved set of cards once Sporting Life publishes prices.

The first pass over a meeting runs without the market factor, because Racing TV
has no pre-race prices and Sporting Life only posts cards on the day. This tool
takes the cards saved at that first pass, drops in the prices when they appear,
re-runs the model and reports which picks survived contact with the market.

usage: python3 tooling/recheck_market.py 2026-11-14 data/cards/2026-11-14_two_meetings.json
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import settle          # noqa: E402
import build_cards_sl  # noqa: E402


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def run(date, cards_path, sl_cards=None):
    """Do the re-check and return a result dict (never raises, never writes cards)."""
    out = {"date": date, "ok": False, "reason": "", "priced": 0, "runners": 0,
           "rows": [], "kept": 0, "changed": 0}
    if not os.path.exists(cards_path):
        out["reason"] = f"no saved cards at {cards_path}"
        return out
    cards = json.load(open(cards_path, encoding="utf-8"))
    sl = sl_cards if sl_cards is not None else build_cards_sl.build(date)
    if not sl:
        out["reason"] = (f"Sporting Life has no cards for {date} yet - "
                         "no prices to test with")
        return out

    by_key = {}
    for c in sl:
        by_key.setdefault((norm(c.get("course")), str(c.get("time"))[:5]), c)

    matched = set()
    for c in cards:
        key = (norm(c["course"]), str(c["time"])[:5])
        src = by_key.get(key)
        if not src:
            continue
        matched.add(key)
        prices = {norm(r.get("horse")): r.get("odds", "") for r in src.get("runners", [])}
        for r in c["runners"]:
            out["runners"] += 1
            p = prices.get(norm(r["horse"]), "")
            if p:
                r["odds"] = p
                out["priced"] += 1
    if not out["priced"]:
        out["reason"] = f"cards exist for {date} but no runner matched a price"
        return out

    before_path = os.path.join(ROOT, "data", "picks_ab", f"test_{date}.json")
    before = {}
    if os.path.exists(before_path):
        snap = json.load(open(before_path, encoding="utf-8"))
        for r in snap.get("rows", []):
            before[(r["course"], r["time"])] = r.get("pick")

    for a in settle.score_cards(cards):
        key = (a["course"], a["time"])
        if (norm(a["course"]), str(a["time"])[:5]) not in matched:
            continue        # no prices here, so the market never entered this pick
        prev = before.get(key, "")
        now = a["pick"] or ""
        survives = (prev == now) if prev else None
        out["rows"].append({"course": a["course"], "time": a["time"],
                            "before": prev, "after": now, "survives": survives})
        if survives is True:
            out["kept"] += 1
        elif survives is False:
            out["changed"] += 1
    out["ok"] = True
    return out


def banner(res):
    width = 62
    line = "=" * width
    bits = [line,
            f" MARKET RE-CHECK - {res['date']}",
            f" {len(res['rows'])} races re-scored with prices "
            f"({res['priced']}/{res['runners']} runners priced)"]
    if res["rows"] and res["rows"][0]["before"]:
        bits.append(f" {res['kept']} of {len(res['rows'])} picks SURVIVED the market; "
                    f"{res['changed']} changed")
        moved = [r for r in res["rows"] if r["survives"] is False]
        if moved:
            bits.append("")
            for r in moved:
                bits.append(f"   changed: {r['course']} {r['time']}: "
                            f"{r['before']} -> {r['after']}")
        else:
            bits.append("   every pick held")
    else:
        bits.append(" no earlier picks saved, so nothing to compare against")
    bits.append(line)
    return "\n".join(bits)


def main(date, cards_path=None, sl_cards=None, quiet=False):
    cards_path = cards_path or os.path.join(ROOT, "data", "cards",
                                            f"{date}_two_meetings.json")
    res = run(date, cards_path, sl_cards)
    if not res["ok"]:
        if not quiet:
            print(res["reason"])
            print("Run this again once the day's cards are up.")
        return 2
    if not quiet:
        print(banner(res))
    try:
        note = os.path.join(ROOT, "data", "picks_ab", f"market_check_{date}.txt")
        with open(note, "w", encoding="utf-8") as f:
            f.write(banner(res) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "2026-11-14",
                  sys.argv[2] if len(sys.argv) > 2 else None))
