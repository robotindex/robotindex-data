#!/usr/bin/env python3
"""
Validate the product <-> builds-mods.json link graph.

validate_products.py covers schema, duplicate product ids, and
directory.json <-> products/ orphans, but nothing about whether a
product record's firmwareCommunity array actually points at real,
distinct builds-mods.json entries. This script checks:

  1. Every "buildsModsId" referenced from any data/products/<id>.json
     record's firmwareCommunity array exists in data/builds-mods.json.
     (Catches a dangling reference -- e.g. a builds-mods.json entry
     renamed or removed without updating the products that cite it.)
  2. No product record's firmwareCommunity array references the same
     buildsModsId more than once. (sync_products.py dedupes this itself
     now, but this is a second, independent check -- it also catches a
     duplicate introduced by a manual hand-edit to a product record,
     which sync_products.py never sees.)

Usage:
    python scripts/validate_crosslinks.py
Run from the repo root (paths below are relative to it).
"""
import glob
import json
import sys


def main():
    errors = []

    try:
        with open("data/builds-mods.json", encoding="utf-8") as f:
            builds_mods = json.load(f)
        builds_mods_ids = {e["id"] for e in builds_mods["entries"]}
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"Could not load data/builds-mods.json: {e}", file=sys.stderr)
        return 2

    product_files = sorted(glob.glob("data/products/*.json"))
    if not product_files:
        print("No files matched data/products/*.json -- run this from the repo root.", file=sys.stderr)
        return 2

    checked = 0
    for path in product_files:
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{path}: could not parse JSON ({e})")
            continue

        seen_here = set()
        for entry in rec.get("firmwareCommunity", []):
            bm_id = entry.get("buildsModsId")
            if bm_id is None:
                continue
            checked += 1
            if bm_id not in builds_mods_ids:
                errors.append(
                    f"{path}: firmwareCommunity references buildsModsId "
                    f"'{bm_id}', which doesn't exist in data/builds-mods.json"
                )
            if bm_id in seen_here:
                errors.append(
                    f"{path}: firmwareCommunity references buildsModsId "
                    f"'{bm_id}' more than once"
                )
            seen_here.add(bm_id)

    if errors:
        print("Validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"OK  {checked} buildsModsId reference(s) across {len(product_files)} product records: all resolve, 0 duplicates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
