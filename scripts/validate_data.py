#!/usr/bin/env python3
"""
Validate a robotindex data file against its per-entry JSON Schema.

Both data/builds-mods.json and data/models-data.json share the same shape:
    { "$schema": "...", "entries": [ {...}, {...}, ... ] }
Each item in "entries" is validated individually against the matching
*-entry.schema.json (draft 2020-12), since the schema files describe one
entry, not the whole document.

Usage:
    python scripts/validate_data.py <data.json> <entry-schema.json> [<data.json> <entry-schema.json> ...]

Exits non-zero (and prints every failure found, not just the first) if any
file fails to parse, is missing the "entries" array, has duplicate ids, or
has an entry that doesn't validate against its schema. This is meant to run
in CI before a sync PR is opened, so a bad fetch from the live site never
gets merged silently.
"""
import json
import sys

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:
    print("Missing dependency: pip install jsonschema", file=sys.stderr)
    sys.exit(2)


def validate_pair(data_path, schema_path):
    errors = []

    try:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return [f"{data_path}: could not parse JSON ({e})"]

    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return [f"{schema_path}: could not parse JSON ({e})"]

    entries = data.get("entries")
    if not isinstance(entries, list):
        return [f"{data_path}: no top-level \"entries\" array found"]

    validator = Draft202012Validator(schema)

    seen_ids = {}
    for i, entry in enumerate(entries):
        label = entry.get("id", f"index {i}") if isinstance(entry, dict) else f"index {i}"

        for err in validator.iter_errors(entry):
            path = "/".join(str(p) for p in err.path) or "(root)"
            errors.append(f"{data_path}: entry '{label}' field '{path}': {err.message}")

        if isinstance(entry, dict) and "id" in entry:
            eid = entry["id"]
            if eid in seen_ids:
                errors.append(
                    f"{data_path}: duplicate id '{eid}' (entries {seen_ids[eid]} and {i})"
                )
            else:
                seen_ids[eid] = i

    if not errors:
        print(f"OK  {data_path}: {len(entries)} entries valid against {schema_path}")

    return errors


def main(argv):
    if len(argv) < 2 or len(argv) % 2 != 0:
        print(__doc__, file=sys.stderr)
        return 2

    all_errors = []
    for i in range(0, len(argv), 2):
        data_path, schema_path = argv[i], argv[i + 1]
        all_errors.extend(validate_pair(data_path, schema_path))

    if all_errors:
        print("\nValidation FAILED:", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("\nAll files valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
