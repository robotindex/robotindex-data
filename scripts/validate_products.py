#!/usr/bin/env python3
"""
Validate data/directory.json and every data/products/<id>.json record.

Unlike validate_data.py (builds-mods.json / models-data.json, which the live
site serves as a single file each), the product catalog is one file per
product plus one directory file tying them together. This script checks:

  1. directory.json validates against schema/directory.schema.json.
  2. Every data/products/<id>.json validates against schema/product.schema.json.
  3. Every productId referenced in directory.json has a matching product
     record, and every product record is referenced from directory.json
     (no orphans in either direction).
  4. No duplicate ids among the product records.
  5. Each record's own "id" matches its filename.

Usage:
    python scripts/validate_products.py
Run from the repo root (paths below are relative to it).
"""
import glob
import json
import sys

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("Missing dependency: pip install jsonschema", file=sys.stderr)
    sys.exit(2)


def main():
    errors = []

    try:
        directory_schema = json.load(open("schema/directory.schema.json", encoding="utf-8"))
        directory_data = json.load(open("data/directory.json", encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not load directory.json or its schema: {e}", file=sys.stderr)
        return 2

    dir_validator = Draft202012Validator(directory_schema)
    for err in dir_validator.iter_errors(directory_data):
        path = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"data/directory.json: {path}: {err.message}")

    try:
        product_schema = json.load(open("schema/product.schema.json", encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not load product.schema.json: {e}", file=sys.stderr)
        return 2

    prod_validator = Draft202012Validator(product_schema)

    product_files = sorted(glob.glob("data/products/*.json"))
    seen_ids = {}
    for path in product_files:
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{path}: could not parse JSON ({e})")
            continue

        for err in prod_validator.iter_errors(rec):
            p = "/".join(str(x) for x in err.path) or "(root)"
            errors.append(f"{path}: {p}: {err.message}")

        rec_id = rec.get("id")
        expected_filename = f"data/products/{rec_id}.json"
        if rec_id and path != expected_filename:
            errors.append(f"{path}: record id '{rec_id}' doesn't match its filename")

        if rec_id:
            if rec_id in seen_ids:
                errors.append(f"{path}: duplicate id '{rec_id}' (also in {seen_ids[rec_id]})")
            else:
                seen_ids[rec_id] = path

    dir_ids = set()
    for cat in directory_data.get("categories", []):
        for sub in cat.get("subcategories", []):
            for item in sub.get("items", []):
                dir_ids.add(item.get("productId"))

    record_ids = set(seen_ids.keys())
    missing_records = dir_ids - record_ids
    orphan_records = record_ids - dir_ids

    for pid in sorted(missing_records):
        errors.append(f"data/directory.json references productId '{pid}' with no matching data/products/{pid}.json")
    for pid in sorted(orphan_records):
        errors.append(f"data/products/{pid}.json exists but isn't referenced by any directory.json entry")

    if errors:
        print("Validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"OK  data/directory.json: {len(dir_ids)} catalog entries, all linked")
    print(f"OK  data/products/: {len(product_files)} records, all valid, 0 duplicates, 0 orphans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
