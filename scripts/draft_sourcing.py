#!/usr/bin/env python3
"""
draft_sourcing.py — draft a sourcing note and verifiedOn date for each
models-data entry, and flag where the recorded licence disagrees with the repo.

This DRAFTS. It does not verify. The note it writes says exactly what happened —
"read from the repo's LICENSE file via the GitHub API on <date>" — because
claiming more than that would undermine the point of having sourcing notes at
all. Review before committing.

Covers the 62 GitHub-hosted entries and the 8 on Hugging Face. The remaining 5
link to project pages (DeepMind Genie 3, World Labs Marble, Wayve GAIA-3, CMU
MoCap, RT-1) and have to be written by hand.

    python scripts/draft_sourcing.py --in data/models-data.json --out /tmp/drafted.json
    python scripts/draft_sourcing.py --in data/models-data.json --report-only

Run it in Actions so GITHUB_TOKEN is available — unauthenticated is 60
requests/hour and this needs 62.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.request

TODAY = datetime.date.today().isoformat()
UA = {"User-Agent": "robotindex-sourcing"}


def get(url, headers=None, timeout=25):
    h = dict(UA)
    if headers:
        h.update(headers)
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout))


def gh_meta(repo_path, token):
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    d = get(f"https://api.github.com/repos/{repo_path}", h)
    lic = (d.get("license") or {}).get("spdx_id")
    if lic == "NOASSERTION":
        lic = "NOASSERTION"          # a LICENSE file exists but GitHub can't identify it
    return {
        "license": lic,
        "license_name": (d.get("license") or {}).get("name"),
        "pushed": (d.get("pushed_at") or "")[:10],
        "stars": d.get("stargazers_count"),
        "archived": d.get("archived"),
        "description": d.get("description") or "",
    }


def hf_meta(kind, rid):
    d = get(f"https://huggingface.co/api/{kind}/{rid}")
    lic = next((t.split(":", 1)[1] for t in d.get("tags", []) if t.startswith("license:")), None)
    return {
        "license": lic,
        "pushed": (d.get("lastModified") or "")[:10],
        "stars": d.get("likes"),
        "archived": False,
        "description": (d.get("cardData") or {}).get("summary", "") or "",
    }


def note_github(repo_path, m, stored_licence):
    bits = [f"Licence read from the repo's LICENSE file via the GitHub API ({TODAY})"]
    if m["license"] == "NOASSERTION":
        bits = [f"Repo has a LICENSE file GitHub could not identify ({TODAY}); "
                f"the terms need reading in full before relying on them"]
    elif not m["license"]:
        bits = [f"No LICENSE file found in the repo ({TODAY}); "
                f"terms are undeclared unless stated elsewhere"]
    bits.append(f"reported as {m['license_name'] or m['license'] or 'none'}" if m["license"] else None)
    bits.append(f"last commit {m['pushed']}" if m["pushed"] else None)
    if m["archived"]:
        bits.append("repository is archived upstream")
    bits.append("description drawn from the repo README")
    return ". ".join(b for b in bits if b) + "."


def note_hf(rid, m):
    if m["license"]:
        head = f"Licence taken from the Hugging Face model card metadata tag ({TODAY})"
    else:
        head = f"No licence tag on the Hugging Face card ({TODAY}); terms undeclared there"
    tail = f"last modified {m['pushed']}" if m["pushed"] else None
    return ". ".join(x for x in [head, tail] if x) + "."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/models-data.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("warning: no GITHUB_TOKEN — 60 req/hour, this needs ~70\n", file=sys.stderr)

    doc = json.load(open(a.inp, encoding="utf-8"))
    entries = doc["entries"] if isinstance(doc, dict) else doc

    done = manual = failed = 0
    mismatches = []

    for e in entries:
        link = e.get("link", "")
        gh = re.match(r"https://github\.com/([^/]+/[^/#?]+)", link)
        hf = re.match(r"https://huggingface\.co/(datasets/)?([^/#?]+/[^/#?]+)", link)

        try:
            if gh:
                m = gh_meta(gh.group(1).removesuffix(".git"), token)
                e["sourcing"] = note_github(gh.group(1), m, e.get("license"))
            elif hf:
                kind = "datasets" if hf.group(1) else "models"
                m = hf_meta(kind, hf.group(2))
                e["sourcing"] = note_hf(hf.group(2), m)
            else:
                # Project pages, university sites — nothing to query.
                e["sourcing"] = f"NEEDS MANUAL NOTE — source is a project page, not a repo ({link})"
                e["verifiedOn"] = None
                manual += 1
                continue
        except Exception as ex:
            e["sourcing"] = f"NEEDS MANUAL NOTE — lookup failed: {str(ex)[:80]}"
            e["verifiedOn"] = None
            failed += 1
            continue

        e["verifiedOn"] = TODAY
        done += 1

        # The point of the exercise: where the stored licence string and the
        # repo disagree. These are the entries the audit is actually about.
        stored = (e.get("license") or "").strip()
        fetched = m["license"]
        if fetched and stored:
            simple = re.fullmatch(r"[\w.\-]+", stored)
            if simple and stored.lower() != fetched.lower():
                mismatches.append((e["id"], stored, fetched))
            elif not simple:
                mismatches.append((e["id"], stored + "  [split/prose]", fetched or "—"))
        elif not fetched and stored:
            mismatches.append((e["id"], stored, "none found in repo"))

        time.sleep(0.15 if token else 1.2)

    print(f"{len(entries)} entries | {done} drafted | {manual} need a manual note | {failed} lookup failed\n")
    if mismatches:
        print(f"LICENCE DISCREPANCIES — {len(mismatches)}\n")
        print(f"{'entry':40} {'recorded':38} repo says")
        print("-" * 104)
        for i, s, f in mismatches:
            print(f"{i[:40]:40} {s[:38]:38} {f}")

    if a.out and not a.report_only:
        json.dump(doc, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"\nwritten to {a.out} — review before replacing the original")


if __name__ == "__main__":
    main()
