#!/usr/bin/env python3
"""Full UK & IRE results from irishracing.com — every meeting, not just the
feature cards Sporting Life carries.

    python3 tooling/fetch_results_ir.py 2026-11-13
"""
import datetime as dt, json, os, re, sys, urllib.request

BASE = "https://www.irishracing.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ORD = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5}


def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            continue
    return ""


def day_slug(iso):
    d = dt.date.fromisoformat(iso)
    return d.strftime("%a-") + str(d.day) + d.strftime("th-%b-%Y").replace("th", ordinal(d.day))


def ordinal(n):
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def text_of(html):
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "|", t)
    return re.sub(r"(\s*\|\s*)+", "|", t)


FIN = re.compile(r"\|(\d)(?:st|nd|rd|th)\|(?:[^|]*\|)*?([^|]+?)\|(?:&nbsp;)?\((\d+)\)\|([^|]*)\|")


def parse_meeting(html, course):
    meta = re.findall(r"t:'(\d{1,2}:\d{2})'[^}]*?r:'((?:[^'\\]|\\.)*)'", html)
    marks = [m.start() for m in re.finditer(r'class="[^"]*racename', html)]
    out = []
    for n, i in enumerate(marks):
        chunk = html[i:marks[n + 1] if n + 1 < len(marks) else len(html)]
        t = text_of(chunk)
        m = re.search(r"(\d{1,2}:\d{2})", t[:160])
        time = m.group(1) if m else ""
        name = t[m.end():m.end() + 90].strip("|").split("|")[0][:70] if m else ""
        hm = re.search(r'class="[^"]*racename[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        if hm:
            head_txt = re.sub(r'<[^>]+>', ' ', hm.group(1))
            head_txt = re.sub(r'\s+', ' ', head_txt).strip(' |')
            tm = re.search(r'(\d{1,2})\.(\d{2})', head_txt) or re.search(r'(\d{1,2}):(\d{2})', head_txt)
            if tm:
                hh, mm = int(tm.group(1)), tm.group(2)
                if hh < 12: hh += 12          # irishracing prints 3.40 for 15:40
                time = f"{hh:02d}:{mm}"
            name = re.sub(r'^\d{1,2}:\d{2}\s*[-\u2013]?\s*', '', head_txt)[:70] or name
        runners = []
        for f in FIN.finditer(t):
            pos, horse, draw, sp = f.groups()
            horse = re.sub(r"\s*\((?:IRE|GB|FR|USA|GER)\)", "", horse).strip()
            if not horse or len(horse) > 40:
                continue
            runners.append({"pos": int(pos), "horse": horse, "sp": sp.strip()})
        seen, uniq = set(), []
        for r in runners:
            if r["horse"] in seen:
                continue
            seen.add(r["horse"]); uniq.append(r)
        if len(uniq) >= 2:
            uniq.sort(key=lambda r: r["pos"])
            out.append({"course": course, "time": time, "race": name,
                        "winner": uniq[0]["horse"], "sp": uniq[0]["sp"],
                        "places": [r["horse"] for r in uniq[:3]]})
    return out


def fetch_day(iso):
    slug = day_slug(iso)
    idx = get(f"{BASE}/fast-results")
    meetings = sorted(set(re.findall(rf'href="(/fast-results/{slug}/[^"]+)"', idx)))
    print(f"{len(meetings)} meetings on {slug}")
    rows = []
    for href in meetings:
        course = href.rsplit("/", 1)[-1].replace("-", " ")
        rows += parse_meeting(get(BASE + href), course)
        print(f"  {course:20} {sum(1 for r in rows if r['course']==course)} races")
    return rows


if __name__ == "__main__":
    iso = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    rows = fetch_day(iso)
    os.makedirs(os.path.join(ROOT, "data", "results_ir"), exist_ok=True)
    path = os.path.join(ROOT, "data", "results_ir", f"{iso}.json")
    json.dump(rows, open(path, "w"), ensure_ascii=False)
    print(f"wrote {len(rows)} races -> data/results_ir/{iso}.json")
