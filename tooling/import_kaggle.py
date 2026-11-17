#!/usr/bin/env python3
"""Turn an external results history into the page's own factor scores.

The source is the Kaggle "Horse Racing results - UK/Ireland" raceform database:
one row per runner, with the fields the page's factors need (SP, OR, draw,
weight, going) but without the six fields the card supplies from the racecard
(form, days, course/distance record, won-last-time-out, jockey and trainer form).
Those six are derived here from each horse's earlier runs only, so nothing in a
row can see its own race.

The cards are then scored by the page's model (tooling/score_cards.js), exactly
like the live archive, and written as the same CSV that tooling/train_weights.py
reads. Nothing here touches index.html, a pick or a stored result.

    python3 tooling/import_kaggle.py --db data/kaggle/raceform/raceform.db \\
        --since 2023-01-01 --until 2026-11-16

Rows are read in date order and folded into one race at a time, so a 1.9m-row
database does not have to fit in memory. A CSV source is assumed to be in the
same chronological order as the database dump.

Two things in this source are not what a racecard would give the page:

* Tips are absent, so that factor is zero for every runner and the page drops it
  as "the same for everyone". A fit on this file says nothing about the tips
  weight: keep that one at its live value.
* RPR and Topspeed are the figures awarded for the run, not the pre-race ratings
  a card carries - the winner holds the top RPR in about seven races out of ten.
  They are left out unless --post-race-ratings is passed, because a model that
  can see them scores beautifully offline and is useless in front of a race.

The database is not GB/IRE-only, and the country suffix on a course name cannot
be trusted ((AW) is a surface, some meetings are written bare, and British Ascot
shares its name with an Australian track), so home meetings are recognised from
a whitelist and anything else is dropped and reported.
"""
import argparse
import csv
import datetime as dt
import glob
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
HOME_COURSES = {
    "Aintree", "Ascot", "Ayr", "Bangor-on-Dee", "Bath", "Beverley", "Brighton",
    "Carlisle", "Cartmel", "Catterick", "Chelmsford", "Chelmsford City",
    "Cheltenham", "Chepstow", "Chester", "Doncaster", "Epsom", "Epsom Downs",
    "Exeter", "Fakenham", "Ffos Las", "Fontwell", "Goodwood", "Hamilton",
    "Haydock", "Hereford", "Hexham", "Huntingdon", "Kelso", "Kempton",
    "Leicester", "Lingfield", "Ludlow", "Market Rasen", "Musselburgh",
    "Newbury", "Newcastle", "Newmarket", "Newton Abbot", "Nottingham", "Perth",
    "Plumpton", "Pontefract", "Redcar", "Ripon", "Royal Ascot", "Salisbury",
    "Sandown", "Sedgefield", "Southwell", "Stratford", "Taunton", "Thirsk",
    "Uttoxeter", "Warwick", "Wetherby", "Wincanton", "Windsor", "Wolverhampton",
    "Worcester", "Yarmouth", "York",
}
IRISH_COURSES = {
    "Ballinrobe", "Bellewstown", "Clonmel", "Cork", "Curragh", "Down Royal",
    "Downpatrick", "Dundalk", "Fairyhouse", "Galway", "Gowran Park", "Kilbeggan",
    "Killarney", "Laytown", "Leopardstown", "Limerick", "Listowel", "Naas",
    "Navan", "Punchestown", "Roscommon", "Sligo", "Thurles", "Tipperary",
    "Tramore", "Wexford",
}
SKIPPED = {}


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


def suffix_tokens(value):
    return [t.upper() for t in re.findall(r"\(([A-Za-z]{2,4})\)", str(value or ""))]


def course_base(value):
    return re.sub(r"\s*\([A-Za-z]{2,4}\)", "", str(value or "")).strip()


def split_course(value):
    """('Dundalk (AW) (IRE)', 'IRE') / ('Wolverhampton (AW)', 'GB') /
    ('Auteuil (FR)', 'FOREIGN')."""
    s = str(value or "").strip()
    base = course_base(s)
    if base in IRISH_COURSES:
        return s, "IRE"
    if base in HOME_COURSES:
        tokens = suffix_tokens(s)
        return s, "IRE" if ("IRE" in tokens or "IE" in tokens) else "GB"
    return s, "FOREIGN"


def clean_time(value):
    s = str(value or "").strip()
    m = re.match(r"^(\d{1,2})[:.](\d{2})", s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def blank_if_dash(value):
    s = str(value if value is not None else "").strip()
    return "" if s in ("", "-", "–", "—", "N/A", "nan", "None") else s


def clean_price(value):
    """'7/4F' is 7/4: the page cannot parse the favourite marker."""
    return re.sub(r"[A-Za-z]+$", "", blank_if_dash(value)).strip()


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

    @staticmethod
    def _push(store, key, value):
        bucket = store.setdefault(key, [])
        bucket.append(value)
        if len(bucket) > 400:                 # only recent runs can matter
            del bucket[:-200]

    def features(self, run, conn, today):
        past = self.horses.get(norm(run["horse"]), [])
        out = {"form": "".join(form_char(p["pos"]) for p in past[-6:]),
               "days": str((today - past[-1]["date"]).days) if past else ""}
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
        self._push(self.horses, norm(run["horse"]),
                   {"date": run["date"], "pos": run["pos"],
                    "course": run["course"], "dist": run["dist"]})
        won = run["pos"] == 1
        if conn["jockey"]:
            self._push(self.jockeys, conn["jockey"], (run["date"], won))
        if conn["trainer"]:
            self._push(self.trainers, conn["trainer"], (run["date"], won))


def read_db(path, since="", until=""):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    names = {r[1].lower(): r[1] for r in con.execute("PRAGMA table_info(data)")}
    if not names:
        raise SystemExit(f"{path} has no `data` table")
    missing = [c for c in COLS if c not in names]
    if missing:
        print(f"  note: source has no {', '.join(missing)}")
    want = ", ".join(f'"{names[c]}"' for c in COLS if c in names)
    where, args = [], []
    if since:
        where.append("date >= ?")
        args.append(since)
    if until:
        where.append("date <= ?")
        args.append(until)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT {want} FROM data{clause} ORDER BY date, race_id, num"
    for row in con.execute(sql, args):
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


def group_races(rows, since, until, max_races):
    """A stream of races, each with its runners, in chronological order."""
    current, key, emitted, dropped = None, None, 0, 0
    for raw in rows:
        date = parse_date(raw.get("date"))
        if not date or (since and date < since) or (until and date > until):
            continue
        course, region = split_course(raw.get("course"))
        if region == "FOREIGN":
            dropped += 1
            SKIPPED[course] = SKIPPED.get(course, 0) + 1
            continue
        pos = position(raw.get("pos"))
        if pos is None:
            continue
        rid = str(raw.get("race_id") or "")
        this = (date, rid, clean_time(raw.get("off")))
        if this != key:
            if max_races and emitted >= max_races:
                break
            if current:
                emitted += 1
                yield current
            key = this
            current = {"date": date, "course": course, "region": region,
                       "time": this[2], "race": str(raw.get("race_name") or ""),
                       "going": str(raw.get("going") or ""), "runners": []}
        current["runners"].append({
            "horse": str(raw.get("horse") or "").strip(),
            "pos": pos,
            "draw": blank_if_dash(raw.get("draw")),
            "weight": blank_if_dash(raw.get("wgt")),
            "odds": clean_price(raw.get("sp")),
            "or": figures(raw.get("or")),
            "rpr": figures(raw.get("rpr")),
            "ts": figures(raw.get("ts")),
            "dist": str(raw.get("dist") or "").strip(),
            "jockey": norm(raw.get("jockey")),
            "trainer": norm(raw.get("trainer")),
        })
    if current:
        yield current
    if dropped:
        print(f"  skipped {dropped} runners from meetings outside GB/IRE")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="raceform.db (sqlite)")
    ap.add_argument("--csv", help="glob of CSVs with the same columns, in date order")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "training", "kaggle_runners.csv"))
    ap.add_argument("--since", default="2023-01-01", help="first date to write")
    ap.add_argument("--until", default="", help="last date to write")
    ap.add_argument("--warmup-days", type=int, default=400,
                    help="history read before --since so form and records exist")
    ap.add_argument("--max-races", type=int, default=0, help="stop after this many races")
    ap.add_argument("--chunk", type=int, default=2000, help="races scored per node call")
    ap.add_argument("--post-race-ratings", action="store_true",
                    help="use rpr/ts anyway (they are awarded for the run, so they leak)")
    args = ap.parse_args(argv)
    if not (args.db or args.csv):
        ap.print_help()
        return 1

    since = parse_date(args.since) if args.since else None
    until = parse_date(args.until) if args.until else None
    warm = (since - dt.timedelta(days=args.warmup_days)) if since else None
    rows = (read_db(args.db, warm.isoformat() if warm else "", args.until)
            if args.db else read_csvs(args.csv))

    fields = ["date", "region", "course", "time", "race_id", "horse", "field",
              "pos", "win", "odds", "market_prob", "rank", "total"] + FACTOR_KEYS
    covered = {k: 0 for k in FACTOR_KEYS}
    stats = {"races": 0, "runners": 0, "priced": 0, "first": None, "last": None}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fh = open(args.out, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()

    def flush(batch):
        if not batch:
            return
        scored = settle.score_cards([item["card"] for item in batch])
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
                    "race_id": card["race_id"], "horse": name, "field": field,
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
                stats["runners"] += 1
                stats["priced"] += 1 if rec["market_prob"] else 0
                writer.writerow(rec)

    history, pending = History(), []
    for race in group_races(rows, warm, until, args.max_races):
        runners = []
        for r in race["runners"]:
            conn = {"jockey": r["jockey"], "trainer": r["trainer"]}
            run = {**r, "date": race["date"], "course": race["course"]}
            feats = history.features(run, conn, race["date"])
            runners.append({
                "horse": r["horse"], "pos": r["pos"], "draw": r["draw"],
                "odds": r["odds"], "form": feats["form"], "or": r["or"],
                "rpr": r["rpr"] if args.post_race_ratings else "",
                "ts": r["ts"] if args.post_race_ratings else "",
                "tips": "", "naps": "", "weight": r["weight"],
                "days": feats["days"], "course": feats["course"],
                "distance": feats["distance"], "going": feats["going"],
                "jp": feats["jp"], "tp": feats["tp"],
            })
            history.remember(run, conn)
        # Warm-up races only build the history; they are not written out.
        if since and race["date"] < since:
            continue
        if len(runners) < 3:
            continue
        stats["races"] += 1
        stats["first"] = stats["first"] or race["date"]
        stats["last"] = race["date"]
        seq = stats["races"] - 1
        card = {"id": f"kaggle-{seq}",
                "race_id": f"{race['date']}|{norm(race['course'])}|{race['time']}|{seq}",
                "course": race["course"], "time": race["time"], "race": race["race"],
                "day": str(race["date"]), "region": race["region"],
                "going": race["going"], "runners": runners}
        pending.append({"card": card})
        if len(pending) >= args.chunk:
            flush(pending)
            pending.clear()
            print(f"  {stats['races']} races, {stats['runners']} runners", flush=True)
    flush(pending)
    fh.close()

    if not stats["races"]:
        print("no races in the requested window")
        return 1
    print(f"\n{stats['races']} races ({stats['first']} .. {stats['last']}), "
          f"{stats['runners']} runners ({stats['priced']} priced, "
          f"{100 * stats['priced'] / max(stats['runners'], 1):.0f}%) "
          f"-> {os.path.relpath(args.out, ROOT)}")
    print("factor coverage:")
    for k in FACTOR_KEYS:
        print(f"  {k:12} {100 * covered[k] / max(stats['runners'], 1):5.1f}%")
    if SKIPPED:
        top = sorted(SKIPPED.items(), key=lambda kv: -kv[1])[:8]
        print("dropped as overseas: " + ", ".join(f"{k} ({v})" for k, v in top)
              + (" ..." if len(SKIPPED) > 8 else ""))
    if stats["races"] < 300:
        print("\nfewer than 300 races is a pipeline test, not evidence")
    print("tips is absent from this source, so the page drops it; keep that weight "
          "at its live value when reading a fit.")
    if not args.post_race_ratings:
        print("rpr and speed are left out: this database records the rating earned "
              "in each run, which the model cannot see before the race.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
