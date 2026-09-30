#!/usr/bin/env python3
"""
harvest_hf.py — pull the robotics datasets on Hugging Face that are worth indexing.

The robotics tag currently returns 80,198 datasets. Most are not datasets in any
useful sense: LeRobot writes a dataset card automatically, so every person who
runs the tutorial publishes one. Three of the entries on the first page of
results are `m1b/testdrive1`, `aractingi/test` and `DavesArmoury/not_a_robot`,
each carrying the identical 135-byte card "This dataset was created using
LeRobot."

So the work here is almost entirely filtering. Four gates, each cheap:

  1. downloads  — a floor, default 500/month. The single strongest signal.
  2. card size  — the LeRobot stub is ~135-145 bytes. Anything under ~400 bytes
                  has no description, no licence and no provenance.
  3. name       — test / demo / tmp / my_dataset / record-N and similar.
  4. dedup      — same base name under many owners is usually a re-upload.

Everything that survives is written out with its licence, download count, tags
and last-modified date, ready to merge into the index.

    python scripts/harvest_hf.py --min-downloads 500 --out hf-candidates.csv
    python scripts/harvest_hf.py --min-downloads 100 --pages 20 --dry-run

No auth needed for public data. HF_TOKEN is honoured if present and raises the
rate limit.

UNTESTED: huggingface.co is unreachable from the environment this was written
in. Written against the documented API shape. Run once with --dry-run first.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://huggingface.co/api/datasets"

# Names that indicate someone was trying the tooling, not publishing data.
JUNK_NAME = re.compile(
    r"(^|[_/-])(test|tests|testing|demo|tmp|temp|debug|sample|example|scratch|trial|"
    r"try|foo|bar|untitled|new_?dataset|my_?dataset|record_?\d*|eval_?\d*|"
    r"dataset_?\d*|run_?\d*|session_?\d*|so100_?\w*|so101_?\w*|koch_?\w*)"
    r"([_/-]|\d*$)", re.I)

# The LeRobot auto-card is ~135-145 bytes. Anything this short carries no
# description, no licence and no provenance — there is nothing to index.
MIN_CARD_BYTES = 400


def fetch(url, token=None, timeout=30):
    h = {"User-Agent": "robotindex-harvest"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout))


def harvest(min_downloads, pages, token):
    """Walk the robotics tag, most-downloaded first, until we drop below the floor."""
    seen, kept = {}, []
    dropped = {"downloads": 0, "stub_card": 0, "junk_name": 0, "duplicate": 0}
    basenames = {}

    for page in range(pages):
        url = (f"{API}?filter=robotics&sort=downloads&direction=-1"
               f"&limit=100&skip={page*100}&full=true")
        try:
            batch = fetch(url, token)
        except Exception as e:
            print(f"  page {page}: {e}", file=sys.stderr)
            break
        if not batch:
            break

        below_floor = 0
        for d in batch:
            rid = d.get("id") or ""
            if not rid or rid in seen:
                continue
            seen[rid] = True

            dl = d.get("downloads", 0) or 0
            if dl < min_downloads:
                below_floor += 1
                dropped["downloads"] += 1
                continue

            owner, _, base = rid.partition("/")
            if JUNK_NAME.search(base):
                dropped["junk_name"] += 1
                continue

            card = d.get("cardData") or {}
            # `full=true` returns the parsed card. An empty or near-empty one is
            # the LeRobot stub.
            desc = (card.get("description") or d.get("description") or "").strip()
            tags = d.get("tags") or []
            if len(json.dumps(card)) < MIN_CARD_BYTES and len(desc) < 80:
                dropped["stub_card"] += 1
                continue

            key = base.lower().replace("-", "_")
            if key in basenames:
                dropped["duplicate"] += 1
                continue
            basenames[key] = rid

            lic = card.get("license")
            if isinstance(lic, list):
                lic = ", ".join(lic)
            if not lic:
                lic = next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), "")

            kept.append({
                "id": rid,
                "owner": owner,
                "downloads": dl,
                "likes": d.get("likes", 0),
                "license": lic or "",
                "modified": (d.get("lastModified") or "")[:10],
                "created": (d.get("createdAt") or "")[:10],
                "is_lerobot": "LeRobot" in tags or "lerobot" in tags,
                "tags": " ".join(t for t in tags if not t.startswith(("license:", "region:", "size_categories:"))),
                "url": f"https://huggingface.co/datasets/{rid}",
                "description": re.sub(r"\s+", " ", desc)[:300],
            })

        # Sorted descending, so once a whole page is under the floor we are done.
        if below_floor == len(batch):
            print(f"  stopped at page {page}: entire page below {min_downloads} downloads")
            break
        time.sleep(0.4)

    return kept, dropped, len(seen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-downloads", type=int, default=500)
    ap.add_argument("--pages", type=int, default=30, help="100 per page")
    ap.add_argument("--out", default="hf-candidates.csv")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    kept, dropped, scanned = harvest(a.min_downloads, a.pages, os.environ.get("HF_TOKEN"))

    print(f"\nscanned {scanned} | kept {len(kept)}")
    for k, v in dropped.items():
        print(f"  −{v:<6} {k}")

    if kept:
        no_lic = sum(1 for k in kept if not k["license"])
        lerobot = sum(1 for k in kept if k["is_lerobot"])
        print(f"\n  no declared licence: {no_lic} of {len(kept)} ({no_lic/len(kept)*100:.0f}%)")
        print(f"  LeRobot-format:      {lerobot}")
        print(f"\n{'downloads':>10}  {'licence':<16} id")
        for k in kept[:30]:
            print(f"{k['downloads']:>10,}  {(k['license'] or '—')[:16]:<16} {k['id']}")
        if len(kept) > 30:
            print(f"{'':>10}  ... and {len(kept)-30} more")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(kept[0].keys()) if kept else ["id"])
        w.writeheader()
        w.writerows(kept)
    print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
