#!/usr/bin/env python3
"""Turn the scraped daily result files into a modelling table with causal features.

Every feature is computed from results STRICTLY BEFORE the race being described,
so the output can be back-tested without look-ahead bias. State is updated only
after a whole race has been written out.

Output: data/dataset.csv.gz  (one row per runner)

Columns
-------
date, course, time, race, going, distance   race identity
horse, jockey, trainer                      identity
pos, pos_num, won, placed                   outcome (the target)
sp, dec, field, mkt_rank                    market
tips, tips_rank                             tipsters
form, runs, wins, days_since                horse history
course_runs, course_wins, dist_runs, dist_wins
j_30_runs, j_30_wr, t_30_runs, t_30_wr      jockey/trainer 30-day strike rate
"""

import collections
import csv
import datetime as dt
import glob
import gzip
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
OUT = os.path.join(DATA, "dataset.csv.gz")

POSNUM = re.compile(r"^(\d+)")


def pos_num(pos):
    m = POSNUM.match(pos or "")
    return int(m.group(1)) if m else None


def form_char(pos):
    n = pos_num(pos)
    if n:
        return str(n) if n < 10 else "0"
    p = (pos or "").strip().upper()
    return p[0] if p else "?"


def main():
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        with open(path, encoding="utf-8") as f:
            day = json.load(f)
        iso = os.path.basename(path)[:-5]
        date = dt.date.fromisoformat(iso)
        for meeting in day["meetings"]:
            for race in meeting["races"]:
                for r in race["runners"]:
                    rows.append({
                        "date": date,
                        "course": meeting["course"],
                        "time": race["time"],
                        "race": race["title"],
                        "going": race["going"],
                        "distance": race["distance"],
                        "pos": r["pos"],
                        "horse": r["horse"],
                        "jockey": r["jockey"],
                        "trainer": r["trainer"],
                        "sp": r["sp"],
                        "dec": r["dec"],
                        "tips": r["tips"],
                    })

    rows.sort(key=lambda x: (x["date"], x["time"], x["course"]))
    print(f"loaded {len(rows)} runner rows from {len(glob.glob(os.path.join(DATA, '*.json')))} days")

    horse_hist = collections.defaultdict(list)      # horse -> [(date, pos_num)]
    course_rec = collections.defaultdict(lambda: [0, 0])   # (horse, course) -> [runs, wins]
    dist_rec = collections.defaultdict(lambda: [0, 0])
    jq = collections.defaultdict(collections.deque)  # jockey -> deque[(date, won)]
    tq = collections.defaultdict(collections.deque)
    jtot = collections.defaultdict(lambda: [0, 0])   # jockey -> [runs, wins] within window
    ttot = collections.defaultdict(lambda: [0, 0])

    out = []
    i = 0
    n = len(rows)
    while i < n:
        # gather one race: same date, course, time
        j = i
        key = (rows[i]["date"], rows[i]["course"], rows[i]["time"])
        while j < n and (rows[j]["date"], rows[j]["course"], rows[j]["time"]) == key:
            j += 1
        race = rows[i:j]
        date = race[0]["date"]

        priced = sorted([r for r in race if r["dec"]], key=lambda r: r["dec"])
        mkt_rank = {id(r): k + 1 for k, r in enumerate(priced)}
        tipped = sorted([r for r in race if r["tips"]], key=lambda r: -r["tips"])
        tips_rank = {id(r): k + 1 for k, r in enumerate(tipped)}
        field = len(race)

        def wr(total, runs):
            return round(total / runs, 3) if runs else ""

        for r in race:
            h = r["horse"]
            hist = horse_hist[h]
            last6 = hist[-6:]
            feats = {
                "date": date.isoformat(), "course": r["course"], "time": r["time"],
                "race": r["race"], "going": r["going"], "distance": r["distance"],
                "horse": h, "jockey": r["jockey"], "trainer": r["trainer"],
                "pos": r["pos"], "pos_num": pos_num(r["pos"]) or "",
                "won": 1 if pos_num(r["pos"]) == 1 else 0,
                "placed": 1 if (pos_num(r["pos"]) or 99) <= 3 else 0,
                "sp": r["sp"], "dec": r["dec"] or "", "field": field,
                "mkt_rank": mkt_rank.get(id(r), ""),
                "tips": r["tips"], "tips_rank": tips_rank.get(id(r), ""),
                "form": "".join(form_char(p) for _, p in last6),
                "runs": len(hist),
                "wins": sum(1 for _, p in hist if pos_num(p) == 1),
                "days_since": (date - hist[-1][0]).days if hist else "",
                "course_runs": course_rec[(h, r["course"])][0],
                "course_wins": course_rec[(h, r["course"])][1],
                "dist_runs": dist_rec[(h, r["distance"])][0],
                "dist_wins": dist_rec[(h, r["distance"])][1],
                "j_30_runs": jtot[r["jockey"]][0], "j_30_wr": wr(jtot[r["jockey"]][1], jtot[r["jockey"]][0]),
                "t_30_runs": ttot[r["trainer"]][0], "t_30_wr": wr(ttot[r["trainer"]][1], ttot[r["trainer"]][0]),
            }
            out.append(feats)

        # --- update state only after the race has been emitted ---
        for r in race:
            h, d = r["horse"], race[0]["date"]
            won = 1 if pos_num(r["pos"]) == 1 else 0
            horse_hist[h].append((d, r["pos"]))
            course_rec[(h, r["course"])][0] += 1
            course_rec[(h, r["course"])][1] += won
            dist_rec[(h, r["distance"])][0] += 1
            dist_rec[(h, r["distance"])][1] += won
            jq[r["jockey"]].append((d, won))
            tq[r["trainer"]].append((d, won))
            jtot[r["jockey"]][0] += 1
            jtot[r["jockey"]][1] += won
            ttot[r["trainer"]][0] += 1
            ttot[r["trainer"]][1] += won

        # prune anything older than 30 days, for every jockey/trainer seen today
        cutoff = date - dt.timedelta(days=30)
        for r in race:
            for q, tot in ((jq[r["jockey"]], jtot[r["jockey"]]), (tq[r["trainer"]], ttot[r["trainer"]])):
                while q and q[0][0] < cutoff:
                    _, w = q.popleft()
                    tot[0] -= 1
                    tot[1] -= w

        i = j

    os.makedirs(DATA, exist_ok=True)
    with gzip.open(OUT, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    print(f"wrote {OUT}: {len(out)} rows, {out[0]['date']} -> {out[-1]['date']}")

    # --- sanity: is the table actually predictive? ---
    races = collections.defaultdict(list)
    for r in out:
        races[(r["date"], r["course"], r["time"])].append(r)
    fav = tot = 0
    tip = tiptot = 0
    for k, rs in races.items():
        p = [r for r in rs if r["dec"]]
        if p:
            tot += 1
            if min(p, key=lambda r: r["dec"])["won"]:
                fav += 1
        t = [r for r in rs if r["tips"]]
        if t:
            tiptot += 1
            if max(t, key=lambda r: r["tips"])["won"]:
                tip += 1
    print(f"races: {len(races)}")
    print(f"favourite won: {fav}/{tot} = {fav/tot*100:.1f}%  (baseline sanity ~30-35%)")
    print(f"most-tipped won: {tip}/{tiptot} = {tip/tiptot*100:.1f}%")
    cov = sum(1 for r in out if r["form"]) / len(out) * 100
    print(f"runners with a prior-run form string: {cov:.1f}%")


if __name__ == "__main__":
    main()
