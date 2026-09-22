#!/usr/bin/env python3
"""
Check whether the live site's directory.html or any product-*.html page
has changed since data/snapshot-manifest.json was last generated.

The data/directory.json and data/products/*.json files in this repo are
a manually extracted snapshot of the live site's HTML (see README) --
there's no live JSON endpoint for this data the way there is for
builds-mods.json/models-data.json, so a re-extraction can't be automated
end-to-end. What this script CAN do is tell you when the source HTML has
drifted from the snapshot, and exactly which pages changed, so a human
knows when it's time to ask for a re-extraction.

Note: this only detects EDITS to pages already listed in the manifest.
A brand-new product page added to the live site won't be flagged here,
since there's nothing in the manifest to compare it against -- mention
new products by hand when requesting a re-extraction.

Usage:
    python scripts/check_drift.py

Writes data/snapshot-manifest.json.new with freshly computed hashes.
Exit codes: 0 = no drift, 1 = drift detected, 2 = fetch/load error.
"""
import hashlib
import json
import sys
import urllib.request

BASE_URL = "https://robotindex.io"
MANIFEST_PATH = "data/snapshot-manifest.json"


def fetch_hash(path):
    url = f"{BASE_URL}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "robotindex-drift-check/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return hashlib.sha256(resp.read()).hexdigest()


def main():
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            old_manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not load {MANIFEST_PATH}: {e}", file=sys.stderr)
        return 2

    new_manifest = {}
    errors = []
    for path in old_manifest:
        try:
            new_manifest[path] = fetch_hash(path)
        except Exception as e:
            errors.append(f"{path}: {e}")

    if errors:
        print("Fetch errors (treated as failures, not silently skipped):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    changed = sorted(p for p in old_manifest if old_manifest.get(p) != new_manifest.get(p))

    with open(MANIFEST_PATH + ".new", "w", encoding="utf-8") as f:
        json.dump(new_manifest, f, indent=2, sort_keys=True)
        f.write("\n")

    if changed:
        print(f"DRIFT DETECTED: {len(changed)} page(s) changed since the last snapshot:")
        for p in changed:
            print(f"  - {p}")
        return 1

    print("No drift: all pages match the committed snapshot manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
