#!/usr/bin/env python3
"""Find and download a Kaggle dataset with the project's credentials.

Kaggle's API uses HTTP Basic auth: the account name plus the API key from
kaggle.json (Kaggle -> Account -> Create New Token). Both are read from the
environment (KAGGLE_USERNAME / KAGGLE_KEY) or from ~/.kaggle/kaggle.json, and
are never printed. The key is the secret; the account name alone cannot log in.

    python3 tooling/fetch_kaggle.py --search "horse racing results"
    python3 tooling/fetch_kaggle.py --files owner/slug
    python3 tooling/fetch_kaggle.py --dataset owner/slug --out data/kaggle/slug

Downloads go through a redirect handler that drops the Authorization header
when Kaggle hands off to its storage host, so the key is only ever sent to
kaggle.com.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

API = "https://www.kaggle.com/api/v1"


def credentials():
    user = os.environ.get("KAGGLE_USERNAME")
    key = os.environ.get("KAGGLE_KEY")
    if not (user and key):
        path = os.path.expanduser("~/.kaggle/kaggle.json")
        if os.path.exists(path):
            try:
                data = json.load(open(path, encoding="utf-8"))
                user, key = data.get("username"), data.get("key")
            except (OSError, ValueError):
                pass
    if not (user and key):
        raise SystemExit(
            "No Kaggle credentials. Add KAGGLE_USERNAME and KAGGLE_KEY under\n"
            "Settings -> Project settings -> Secrets (the key is in kaggle.json,\n"
            "from Kaggle -> Account -> Create New Token). A username alone cannot log in.")
    return user, key


class SameHostAuth(urllib.request.HTTPRedirectHandler):
    """Keep the Basic auth header on kaggle.com, drop it on the storage host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old = urllib.parse.urlsplit(req.full_url).netloc
            if urllib.parse.urlsplit(newurl).netloc != old:
                new.remove_header("Authorization")
        return new


def opener(user, key):
    token = base64.b64encode(f"{user}:{key}".encode()).decode()
    req_opener = urllib.request.build_opener(SameHostAuth)
    req_opener.addheaders = [("Authorization", "Basic " + token),
                             ("User-Agent", "race-sheet-training/1.0")]
    return req_opener


def call(op, url):
    try:
        with op.open(url, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit(f"Kaggle rejected the credentials ({e.code}); check "
                             "KAGGLE_USERNAME and KAGGLE_KEY.")
        raise SystemExit(f"Kaggle request failed: {e.code} {e.reason}")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def search(op, query):
    data = call(op, f"{API}/datasets/list?search={urllib.parse.quote(query)}&page=1")
    for d in data[:15]:
        ref = d.get("ref") or d.get("datasetSlug", "")
        print(f"{ref:55} {human(d.get('totalBytes') or 0):>9}  "
              f"{d.get('downloadCount', 0):>7} downloads  {d.get('title', '')}")
    if not data:
        print("no datasets matched")


def files(op, dataset):
    meta = call(op, f"{API}/datasets/view/{dataset}")
    items = list(meta.get("datasetFiles") or [])
    for res in meta.get("resources") or []:
        items += res.get("files") or []
    total = sum(int(f.get("totalBytes") or f.get("size") or 0) for f in items)
    print(f"{meta.get('title')}  ({len(items)} files, {human(total)})")
    print(str(meta.get("description", ""))[:600].replace("\n", " "))
    for f in items:
        print(f"  {str(f.get('name')):55} {human(f.get('totalBytes') or f.get('size') or 0)}")


def download(op, dataset, out, only=None):
    url = f"{API}/datasets/download/{dataset}"
    if only:
        url += "/" + urllib.parse.quote(only)
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, (only or dataset.split("/")[-1]) + ".zip")
    print("downloading", url, "->", target)
    try:
        with op.open(url, timeout=600) as r, open(target, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit(f"Kaggle rejected the credentials ({e.code}).")
        raise SystemExit(f"download failed: {e.code} {e.reason}")
    with zipfile.ZipFile(target) as z:
        z.extractall(out)
    os.remove(target)
    for name in sorted(os.listdir(out)):
        p = os.path.join(out, name)
        print(f"  {name:50} {human(os.path.getsize(p)) if os.path.isfile(p) else 'dir'}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", help="search Kaggle datasets and print refs")
    ap.add_argument("--files", help="list the files in owner/slug")
    ap.add_argument("--dataset", help="download owner/slug")
    ap.add_argument("--file", help="download one file from the dataset")
    ap.add_argument("--out", default=os.path.join("data", "kaggle"))
    args = ap.parse_args(argv)
    if not any([args.search, args.files, args.dataset]):
        ap.print_help()
        return 1
    user, key = credentials()
    op = opener(user, key)
    if args.search:
        search(op, args.search)
    if args.files:
        files(op, args.files)
    if args.dataset:
        download(op, args.dataset, args.out, args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
