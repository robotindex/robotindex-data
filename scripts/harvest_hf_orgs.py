#!/usr/bin/env python3
"""
harvest_hf_orgs.py — list everything a watched publisher has put on Hugging
Face, regardless of downloads.

The main harvest sorts the robotics tag by downloads and walks down until it
hits the floor. That is structurally late: a dataset published last week has had
no time to accumulate 100 downloads, so it is invisible however significant it
is. Reading two years of robotics papers found 49 datasets the harvest had never
seen, 32 of them live, and 28 of those already above the floor.

Listing a publisher's datasets is one call with no floor and no sort, so
anything a watched account uploads shows up the same day. A few hundred calls,
unauthenticated, against an API that does not rate-limit public listing hard.

The watch list is derived from the data: any owner already in the index with two
or more datasets, or one with real traction. Run with --derive to regenerate it
from a harvest CSV; otherwise it reads data/watched-hf-orgs.json, which can be
hand-edited to add publishers we have not caught yet.

    python scripts/harvest_hf_orgs.py --out hf-orgs-new.csv
    python scripts/harvest_hf_orgs.py --derive data/history/hf-2026-10-02.csv

Output is only what is NOT already in data/history, so the result is a short
review list rather than another census.
"""

import argparse
import csv
import datetime
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://huggingface.co/api/datasets"
ORGS_FILE = os.path.join("data", "watched-hf-orgs.json")

# Same filter as the main harvest, so a watched publisher's test upload does not
# get through just because of who owns it.
JUNK_NAME = re.compile(
    r"(^|[_-])(test|tests|demo|tmp|temp|debug|sample|samples|example|examples|"
    r"trial|dummy|scratch|playground|my[_-]?\w+|untitled|new[_-]?dataset|"
    r"record[_-]?\d+|session[_-]?\d+|take[_-]?\d+|try\d*)([_-]|\d*$)", re.I)

# Numbered shards of one upload. A single publisher once put up 160 slices of
# Open X-Embodiment, each card naming every robot in the collection, which alone
# accounted for 162 of 165 Sawyer references in the index.
SHARD = re.compile(r"(^|[_-])(part|shard|chunk|split|slice|batch|seg)[_-]?\d+$|"
                   r"[_-]\d{3,}$", re.I)


def fetch(url, token=None, tries=3, timeout=30):
    headers = {"User-Agent": "robotindex-hf-orgs"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code == 429 and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 20)
                print(f"    rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if attempt == tries - 1:
                raise
        except Exception:
            if attempt == tries - 1:
                raise
        time.sleep(3 * (attempt + 1))
    return []


def derive_watchlist(csv_path, min_datasets=2, min_downloads=5000):
    """Owners already in the index with more than one dataset, or one with real
    traction. Both are evidence that the account publishes robotics data."""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    by = {}
    for r in rows:
        owner = r.get("owner") or r["id"].split("/")[0]
        by.setdefault(owner, []).append(r)
    out = []
    for owner, rs in by.items():
        dl = sum(int(x.get("downloads") or 0) for x in rs)
        if len(rs) >= min_datasets or dl >= min_downloads:
            out.append(owner)
    return sorted(out, key=str.lower)


def known():
    """Everything already in data/history, so we only report what is new."""
    seen = set()
    for path in glob.glob(os.path.join("data", "history", "hf-*.csv")):
        try:
            for r in csv.DictReader(open(path, encoding="utf-8")):
                seen.add(r["id"].lower())
        except Exception:
            continue
    return seen


def list_owner(owner, token, since):
    """Every public dataset an owner has, newest first."""
    out = []
    for page in range(4):
        url = (f"{API}?author={urllib.parse.quote(owner)}"
               f"&sort=createdAt&direction=-1&limit=100&skip={page * 100}&full=true")
        try:
            batch = fetch(url, token)
        except Exception as e:
            print(f"  {owner}: {str(e)[:60]}", file=sys.stderr)
            return out
        if not batch:
            return out
        stop = False
        for d in batch:
            created = (d.get("createdAt") or "")[:10]
            if since and created and created < since:
                stop = True      # sorted newest first
                break
            out.append(d)
        if stop or len(batch) < 100:
            return out
        time.sleep(0.3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="hf-orgs-new.csv")
    ap.add_argument("--since", default=None,
                    help="only datasets created on or after this date (default: 90 days ago)")
    ap.add_argument("--derive", metavar="CSV",
                    help="regenerate data/watched-hf-orgs.json from a harvest CSV and exit")
    ap.add_argument("--min-downloads", type=int, default=0,
                    help="download floor; 0 by default, because a dataset uploaded "
                         "today has none")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.derive:
        orgs = derive_watchlist(a.derive)
        os.makedirs("data", exist_ok=True)
        with open(ORGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"_comment": "Hugging Face publishers watched by "
                                   "harvest_hf_orgs.py. Derived from a harvest CSV; "
                                   "hand-edit to add accounts we have not caught yet.",
                       "orgs": orgs}, f, indent=2)
        print(f"Wrote {len(orgs)} publishers to {ORGS_FILE}")
        return 0

    try:
        orgs = json.load(open(ORGS_FILE, encoding="utf-8"))["orgs"]
    except Exception as e:
        print(f"FATAL: could not read {ORGS_FILE}: {e}\n"
              f"Run with --derive data/history/hf-YYYY-MM-DD.csv first.", file=sys.stderr)
        return 1

    since = a.since or (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    token = os.environ.get("HF_TOKEN")
    seen = known()
    print(f"{len(orgs)} publishers, datasets created since {since}, "
          f"{len(seen)} already known\n", file=sys.stderr)

    found = []
    dropped = {"already_known": 0, "junk_name": 0, "shard": 0, "below_floor": 0}
    for i, owner in enumerate(orgs, 1):
        for d in list_owner(owner, token, since):
            rid = d.get("id") or ""
            if not rid or rid.lower() in seen:
                dropped["already_known"] += 1
                continue
            base = rid.split("/", 1)[-1]
            if JUNK_NAME.search(base):
                dropped["junk_name"] += 1
                continue
            if SHARD.search(base):
                dropped["shard"] += 1
                continue
            dl = d.get("downloads", 0) or 0
            if dl < a.min_downloads:
                dropped["below_floor"] += 1
                continue
            card = d.get("cardData") or {}
            lic = card.get("license") or ""
            if isinstance(lic, list):
                lic = ", ".join(lic)
            tags = d.get("tags") or []
            if not lic:
                lic = next((t.split(":", 1)[1] for t in tags
                            if t.startswith("license:")), "")
            desc = (card.get("description") or d.get("description") or "").strip()
            found.append({
                "id": rid, "owner": owner,
                "downloads": dl, "likes": d.get("likes", 0),
                "license": lic,
                "created": (d.get("createdAt") or "")[:10],
                "modified": (d.get("lastModified") or "")[:10],
                "tags": " ".join(t for t in tags if not t.startswith(
                    ("license:", "region:", "size_categories:"))),
                "url": f"https://huggingface.co/datasets/{rid}",
                "description": re.sub(r"\s+", " ", desc)[:250],
            })
        if i % 25 == 0:
            print(f"  [{i}/{len(orgs)}] {len(found)} new so far", file=sys.stderr)
        time.sleep(0.15)

    found.sort(key=lambda r: r["created"], reverse=True)
    print(f"\n{len(found)} datasets not already in the index")
    for k, v in dropped.items():
        print(f"  -{v:<6} {k}")
    if found:
        over = sum(1 for r in found if r["downloads"] >= 100)
        print(f"\n  {over} are already above the 100-download floor the main harvest uses")
        print(f"\n{'created':<12} {'downloads':>9}  {'licence':<16} dataset")
        for r in found[:40]:
            print(f"{r['created']:<12} {r['downloads']:>9,}  "
                  f"{(r['license'] or '-')[:16]:<16} {r['id']}")
        if len(found) > 40:
            print(f"{'':<12} {'':>9}  ... and {len(found) - 40} more")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return 0

    if not found:
        print("\nnothing to write")
        return 0
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(found[0].keys()))
        w.writeheader()
        w.writerows(found)
    print(f"\nwritten to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
