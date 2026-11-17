#!/usr/bin/env python3
"""Turn an external results history into the page's own factor scores.

The source is the Kaggle "Horse Racing results - UK/Ireland" raceform database:
one row per runner, with the fields the page's factors need (SP, OR, RPR,
Topspeed, draw, weight, going) but without the six fields the card supplies from
the racecard (form, days, course/distance record, won-last-time-out, jockey and
trainer form). Those six are derived here from each horse's earlier runs only, so
nothing in a row can see its own race.

The cards are then scored by the page's model (tooling/score_cards.js), exactly
like the live archive, and written as the same CSV that tooling/train_weights.py
reads. Nothing here touches index.html, a pick or a stored result.

    python3 tooling/import_kaggle.py --db data/kaggle/raceform.db --since 2023-01-01
    python3 tooling/import_kaggle.py --csv 'data/kaggle/*.csv' --since 2024-01-01

Tips are absent from this source, so that factor is zero for every runner and
the page drops it as "the same for everyone". A fit on this file therefore says
nothing about the tips weight: keep that one at its live value.
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import settle  # noqa: E402
from export_training import FACTOR_KEYS, decimal_odds  # noqa: E402

COLS = ["date", "course", "race_id", "off", "race_name", "type", "going", "ran",
        "num", "pos", "draw", "horse", "wgt", "sp", "jockey", "trainer",
        "or", "rpr", "ts", "dist"]
GB = {"", "GB", "ENG", "SCO", "WAL", "NI"}
IRE = {"IRE", "IE"}


def parse_date(value):
    """ISO, d/m/Y or an Excel serial: the real file has used more than one."""
    s = str(value or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    try:
        n = float(s.replace(",", ""))
    except ValueError:
        return None
    if 19000101 <= n <= 21001231 and float(n).is_integer():
        n = int(n)
        return dt.date(n // 10000, (n // 100) % 100, n % 100)
    if 20000 < n < 80000:                     # Excel day count
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(n))
    return None


def split_course(value):
    """'Auteuil (FR)' -> ('Auteuil', 'FR'); plain names are British."""
    s = str(value or "").strip()
    m = re.match(r"^(.*?)\s*\(([A-Za-z]{2,3})\)\s*$", s)
    return (m.group(1).strip(), m.group(2).upper()) if m else (s, "")


def clean_time(value):
    s = str(value or "").strip()
    m = re.match(r"^(\d{1,2})[:.](\d{2})", s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def blank_if_dash(value):
    s = str(value if value is not None else "").strip()
    return "" if s in ("", "-", "–", "—", "N/A", "nan", "None") else s


def figures(value):
    """The page treats 0 and blank the same way: no figure, not a rating of 0."""
    s = blank_if_dash(value)
    if not s:
        return ""
    try:
        n = int(float(s))
    except ValueError:
        return ""
    return str(n) if n > 0 else ""


def position(value):
    """Finishing position, or the letter a non-finisher is recorded with."""
    s = str(value if value is not None else "").strip().upper()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n if n > 0 else None
    if s[:1] in ("P", "F", "U", "R", "D", "B", "S"):
        return s[:1]
    return None


def form_char(pos):
    if isinstance(pos, int):
        return str(pos) if pos <= 9 else "0"
    return pos or ""


def norm(value):
    s = re.sub(r"\(.*?\)", "", str(value or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


class History:
    """Everything a horse, jockey or trainer did before the race being priced."""

    def __init__(self):
        self.horses = {}
        self.jockeys = {}
        self.trainers = {}

    @staticmethod
    def _rate(store, key, today, days=60, minimum=3):
        runs = [x for x in store.get(key, []) if (today - x[0]).days <= days]
        if len(runs) < minimum:
            return ""
        wins = sum(1 for _, won in runs if won)
        return str(int(round(100.0 * wins / len(runs))))

    def features(self, run, conn, today):
        key = norm(run["horse"])
        past = self.horses.get(key, [])
        out = {}
        out["form"] = "".join(form_char(p["pos"]) for p in past[-6:])
        out["days"] = str((today - past[-1]["date"]).days) if past else ""
        # A first-timer is 'N' on both records (that is how the cards arrive),
        # but has no last run, so won-last-time-out stays unknown.
        out["course"] = "N"
        out["distance"] = "N"
        out["going"] = ""
        if past:
            out["course"] = "Y" if any(
                p["pos"] == 1 and p["course"] == run["course"] for p in past) else "N"
            out["distance"] = "Y" if any(
                p["pos"] == 1 and p["dist"] == run["dist"] for p in past) else "N"
            out["going"] = "Y" if past[-1]["pos"] == 1 else "N"
        out["jp"] = self._rate(self.jockeys, conn["jockey"], today)
        out["tp"] = self._rate(self.trainers, conn["trainer"], today)
        return out

    def remember(self, run, conn):
        rec = {"date": run["date"], "pos": run["pos"],
               "course": run["course"], "dist": run["dist"]}
        self.horses.setdefault(norm(run["horse"]), []).append(rec)
        won = run["pos"] == 1
        if conn["jockey"]:
            self.jockeys.setdefault(conn["jockey"], []).append((run["date"], won))
        if conn["trainer"]:
            self.trainers.setdefault(conn["trainer"], []).append((run["date"], won))


def read_db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    names = {r[1].lower(): r[1] for r in con.execute("PRAGMA table_info(data)")}
    if not names:
        raise SystemExit(f"{path} has no `data` table")
    want = ", ".join(f'"{names[c]}"' for c in COLS if c in names)
    missing = [c for c in COLS if c not in names]
    if missing:
        print(f"  note: source has no {', '.join(missing)}")
    for row in con.execute(f"SELECT {want} FROM data"):
        yield {k: row[k] for k in row.keys()}
    con.close()


def read_csvs(pattern):
    for path in sorted(glob.glob(pattern)):
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            heads = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
            if not {"horse", "date", "course"} <= heads:
                continue
            print(f"  reading {os.path.basename(path)}")
            for row in reader:
                yield {(k or "").strip().lower(): v for k, v in row.items()}


def collect(rows, since, until, max_races):
    """Rows grouped into races, in chronological order."""
    races, order = {}, []
    skipped_country = 0
    full = False
    for raw in rows:
        date = parse_date(raw.get("date"))
        if not date or (since and date < since) or (until and date > until):
            continue
        course, country = split_course(raw.get("course"))
        if country not in GB and country not in IRE:
            skipped_country += 1
            continue
        pos = position(raw.get("pos"))
        if pos is None:
            continue
        key = (str(raw.get("race_id") or ""), date, course,
               clean_time(raw.get("off")))
        if full:
            continue
        if key not in races:
            if max_races and len(races) >= max_races:
                full = True          # finish nothing halfway: stop at a race edge
                continue
            races[key] = {"date": date, "course": course,
                          "region": "IRE" if country in IRE else "GB",
                          "time": key[3], "race": str(raw.get("race_name") or ""),
                          "going": str(raw.get("going") or ""), "runners": []}
            order.append(key)
        races[key]["runners"].append({
            "horse": str(raw.get("horse") or "").strip(),
            "pos": pos,
            "draw": blank_if_dash(raw.get("draw")),
            "weight": blank_if_dash(raw.get("wgt")),
            "odds": blank_if_dash(raw.get("sp")),
            "or": figures(raw.get("or")),
            "rpr": figures(raw.get("rpr")),
            "ts": figures(raw.get("ts")),
            "dist": str(raw.get("dist") or "").strip(),
            "jockey": norm(raw.get("jockey")),
            "trainer": norm(raw.get("trainer")),
        })
    # A race number is unreliable across sources: use the parsed date and the
    # race name to keep the history strictly chronological.
    order.sort(key=lambda k: (races[k]["date"], races[k]["race"], races[k]["time"], k[0]))
    if skipped_country:
        print(f"  skipped {skipped_country} rows from outside GB/IRE")
    return [races[k] for k in order]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="raceform.db (sqlite)")
    ap.add_argument("--csv", help="glob of CSVs with the same columns")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "training", "kaggle_runners.csv"))
    ap.add_argument("--since", default="2023-01-01", help="first date to import")
    ap.add_argument("--until", default="", help="last date to import")
    ap.add_argument("--max-races", type=int, default=0, help="stop after this many races")
    ap.add_argument("--chunk", type=int, default=2000, help="races scored per node call")
    args = ap.parse_args(argv)
    if not (args.db or args.csv):
        ap.print_help()
        return 1

    since = parse_date(args.since) if args.since else None
    until = parse_date(args.until) if args.until else None
    rows = read_db(args.db) if args.db else read_csvs(args.csv)
    races = collect(rows, since, until, args.max_races)
    if not races:
        print("no GB/IRE races in that window")
        return 1
    print(f"{len(races)} races, {sum(len(r['runners']) for r in races)} runners, "
          f"{races[0]['date']} .. {races[-1]['date']}")
    if len(races) < 300:
        print("  warning: fewer than 300 races is a pipeline test, not evidence")

    history, written, pending = History(), 0, []
    covered = {k: 0 for k in FACTOR_KEYS}
    total = priced = 0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fields = ["date", "region", "course", "time", "race_id", "horse", "field",
              "pos", "win", "odds", "market_prob", "rank", "total"] + FACTOR_KEYS
    fh = open(args.out, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()

    def flush(batch):
        nonlocal written, total, priced
        if not batch:
            return
        scored = settle.score_cards([c["card"] for c in batch])
        for info, item in zip(scored, batch):
            card = item["card"]
            field = len(card["runners"])
            winner = next((r["horse"] for r in card["runners"] if r["pos"] == 1), "")
            for runner in info.get("runners") or []:
                name = runner.get("horse", "")
                src = next((r for r in card["runners"] if r["horse"] == name), {})
                odds = decimal_odds(src.get("odds", ""))
                parts = runner.get("parts") or {}
                rec = {k: "" for k in fields}
                rec.update({
                    "date": str(card["day"]), "region": card["region"],
                    "course": card["course"], "time": card["time"],
                    "race_id": card["race_id"],
                    "horse": name, "field": field,
                    "pos": src.get("pos", ""), "win": 1 if name == winner else 0,
                    "odds": f"{odds:.2f}" if odds else "",
                    "market_prob": f"{1 / odds:.4f}" if odds else "",
                    "rank": runner.get("rank", ""), "total": runner.get("total", ""),
                })
                for k in FACTOR_KEYS:
                    v = parts.get(k)
                    rec[k] = "" if v is None else v
                    if rec[k] != "":
                        covered[k] += 1
                total += 1
                priced += 1 if rec["market_prob"] else 0
                writer.writerow(rec)
        written += len(batch)

    for race in races:
        runners = []
        for r in race["runners"]:
            conn = {"jockey": r["jockey"], "trainer": r["trainer"]}
            run = {**r, "date": race["date"], "course": race["course"]}
            feats = history.features(run, conn, race["date"])
            runners.append({
                "horse": r["horse"], "pos": r["pos"], "draw": r["draw"],
                "odds": r["odds"], "form": feats["form"],
                "or": r["or"], "rpr": r["rpr"], "ts": r["ts"], "tips": "",
                "naps": "", "weight": r["weight"], "days": feats["days"],
                "course": feats["course"], "distance": feats["distance"],
                "going": feats["going"], "jp": feats["jp"], "tp": feats["tp"],
            })
            history.remember(run, conn)
        if len(runners) < 3:
            continue
        seq = written + len(pending)
        card = {"id": f"kaggle-{seq}",
                "race_id": f"{race['date']}|{norm(race['course'])}|{race['time']}|{seq}",
                "course": race["course"],
                "time": race["time"], "race": race["race"], "day": str(race["date"]),
                "region": race["region"], "going": race["going"], "runners": runners}
        pending.append({"card": card})
        if len(pending) >= args.chunk:
            flush(pending)
            pending.clear()
            print(f"  {written} races, {total} runners", flush=True)
    flush(pending)
    fh.close()

    print(f"\n{written} races, {total} runners ({priced} priced, "
          f"{100 * priced / max(total, 1):.0f}%) -> {os.path.relpath(args.out, ROOT)}")
    print("factor coverage:")
    for k in FACTOR_KEYS:
        print(f"  {k:12} {100 * covered[k] / max(total, 1):5.1f}%")
    print("\ntips is absent from this source, so the page drops it; keep that weight "
          "at its live value when reading a fit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
