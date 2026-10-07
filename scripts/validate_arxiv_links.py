#!/usr/bin/env python3
"""
validate_arxiv_links.py — fetch every repository and dataset an arXiv paper
named, and record what is actually there.

harvest_arxiv.py produces claims. An author writes "code available at
github.com/lab/thing" in an abstract, and that is all we know: the repository
may never have been pushed, may have been renamed, may be private, or may be a
placeholder with a README promising code soon. A list of 1,732 such claims is a
discovery feed, not an index, and merging it unchecked would put a great deal
of fiction into the data.

So this reads the CSV the arXiv harvest wrote, calls the GitHub and Hugging
Face APIs once per link, and writes back what each one really is: whether it
exists, how many stars or downloads it has, what licence it carries, when it
was last pushed, and whether it is empty or archived.

    python scripts/validate_arxiv_links.py data/history/arxiv-2026-10-06.csv
    python scripts/validate_arxiv_links.py --limit 50 --dry-run <csv>

GitHub allows 5,000 authenticated calls an hour, so 1,700 repositories is one
pass well inside the limit. Hugging Face needs no key for public datasets.

The output is deliberately not merged into the index here. Deciding that a
paper naming a repository is reason enough to index it, where fifty strangers
starring it is the current bar, is a judgement about what the index means, and
belongs to a person rather than to this script.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

GH_API = "https://api.github.com/repos/"
HF_API = "https://huggingface.co/api/datasets/"
RAW = "https://raw.githubusercontent.com/"

# Candidate README filenames, in the order GitHub itself resolves them.
READMES = ["README.md", "readme.md", "README.rst", "README.txt", "README"]


def get(url, token=None, tries=3):
    headers = {"User-Agent": "robotindex-validate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            if e.code in (404, 451):
                return None, "not found"
            if e.code == 403:
                # distinguish a rate limit from a blocked repository
                if e.headers.get("X-RateLimit-Remaining") == "0":
                    reset = int(e.headers.get("X-RateLimit-Reset", 0))
                    wait = max(5, reset - int(time.time()) + 2)
                    print(f"    rate limited, waiting {wait}s", file=sys.stderr)
                    time.sleep(min(wait, 900))
                    continue
                return None, "forbidden"
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None, f"http {e.code}"
        except Exception as e:
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None, str(e)[:40]
    return None, "failed"


def fetch_readme(full, branch, token, limit=60000):
    """The README text, or "".

    Worth the extra call: in the main index 326 of 556 robot attributions come
    from README text rather than from the name, description or topics. Judging
    these repositories on metadata alone resolved hardware for 1% of them
    against 8% for the same fields in the index, which measures what we did not
    read rather than what is not there.
    """
    for name in READMES:
        url = f"{RAW}{full}/{branch}/{name}"
        req = urllib.request.Request(url, headers={"User-Agent": "robotindex-validate"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read(limit).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return ""
        except Exception:
            return ""
    return ""


def check_repo(full, token, want_readme=True):
    d, err = get(GH_API + full, token)
    if err:
        return {"kind": "github", "ref": full, "status": err}
    pushed = (d.get("pushed_at") or "")[:10]
    lic = (d.get("license") or {}).get("spdx_id") or ""
    return {
        "kind": "github", "ref": full, "status": "ok",
        "name": d.get("full_name", full),
        "stars": d.get("stargazers_count", 0),
        "forks": d.get("forks_count", 0),
        "licence": "" if lic in ("NOASSERTION", None) else lic,
        "created": (d.get("created_at") or "")[:10],
        "pushed": pushed,
        "size_kb": d.get("size", 0),
        "archived": bool(d.get("archived")),
        "empty": d.get("size", 0) == 0,
        "language": d.get("language") or "",
        "description": (d.get("description") or "").replace("\n", " ")[:200],
        "url": d.get("html_url", ""),
        "readme": (fetch_readme(d.get("full_name", full),
                                d.get("default_branch") or "main", token)
                   if want_readme and d.get("size", 0) else ""),
    }


def check_dataset(ref, token=None):
    d, err = get(HF_API + ref)
    if err:
        return {"kind": "huggingface", "ref": ref, "status": err}
    card = d.get("cardData") or {}
    lic = card.get("license") or d.get("license") or ""
    if isinstance(lic, list):
        lic = ";".join(lic)
    return {
        "kind": "huggingface", "ref": ref, "status": "ok",
        "name": d.get("id", ref),
        "stars": d.get("likes", 0),
        "downloads": d.get("downloads", 0),
        "licence": lic,
        "created": (d.get("createdAt") or "")[:10],
        "pushed": (d.get("lastModified") or "")[:10],
        "archived": bool(d.get("disabled")),
        "empty": False,
        "description": (d.get("description") or "").replace("\n", " ")[:200],
        "url": f"https://huggingface.co/datasets/{ref}",
    }


def resolve_hardware(records):
    """Name the robot each repository is for, using the same patterns as the
    main harvest. Imported rather than copied so the two cannot drift."""
    try:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "harvest_gh", os.path.join(here, "harvest_gh.py"))
        hg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hg)
    except Exception as e:
        print(f"  (no hardware attribution: {e})", file=sys.stderr)
        for r in records:
            r["robots"] = ""
        return records
    for r in records:
        if r.get("status") != "ok":
            r["robots"] = ""
            continue
        text = " ".join([r.get("name", ""), r.get("description", ""),
                         r.get("readme", "")])
        r["robots"] = "; ".join(hg.find_embodiment(text))
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_in", help="the CSV harvest_arxiv.py wrote")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0, help="check only the first N links")
    ap.add_argument("--no-readme", action="store_true",
                    help="skip the README fetch; halves the requests and loses most "
                         "of the hardware attribution")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv_in, encoding="utf-8")))
    repos, dsets, where = {}, {}, {}
    for r in rows:
        for x in (r.get("new_repos") or "").split("; "):
            if x:
                repos.setdefault(x, []).append(r["arxiv_id"])
        for x in (r.get("new_datasets") or "").split("; "):
            if x:
                dsets.setdefault(x, []).append(r["arxiv_id"])
    targets = [("github", k) for k in sorted(repos)] + \
              [("huggingface", k) for k in sorted(dsets)]
    if a.limit:
        targets = targets[:a.limit]

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("No GITHUB_TOKEN — 60 calls an hour unauthenticated, which is not "
              "enough. Set it and re-run.", file=sys.stderr)
    print(f"{len(repos)} repositories and {len(dsets)} datasets named in "
          f"{len(rows)} papers\nchecking {len(targets)}\n", file=sys.stderr)

    out = []
    for i, (kind, ref) in enumerate(targets, 1):
        rec = (check_repo(ref, token, not a.no_readme) if kind == "github"
               else check_dataset(ref))
        rec["papers"] = ";".join((repos if kind == "github" else dsets)[ref][:3])
        rec["paper_count"] = len((repos if kind == "github" else dsets)[ref])
        out.append(rec)
        if i % 100 == 0:
            ok = sum(1 for x in out if x["status"] == "ok")
            print(f"  [{i}/{len(targets)}] {ok} alive", file=sys.stderr)
        time.sleep(0.05)

    out = resolve_hardware(out)
    alive = [x for x in out if x["status"] == "ok"]
    dead = [x for x in out if x["status"] != "ok"]
    empty = [x for x in alive if x.get("empty")]
    arch = [x for x in alive if x.get("archived")]

    print(f"\n{len(out)} links checked")
    print(f"  {len(alive)} exist ({len(alive)/max(1,len(out))*100:.0f}%)")
    print(f"  {len(dead)} do not")
    for reason, n in sorted(
            {d["status"]: sum(1 for x in dead if x["status"] == d["status"])
             for d in dead}.items(), key=lambda kv: -kv[1]):
        print(f"      {n:>5}  {reason}")
    print(f"  {len(empty)} exist but are empty")
    print(f"  {len(arch)} are archived")

    if alive:
        gh = [x for x in alive if x["kind"] == "github"]
        if gh:
            print(f"\n  of {len(gh)} live repositories:")
            print(f"    {sum(1 for x in gh if x['stars'] >= 50):>5} have 50+ stars "
                  f"(the main harvest floor)")
            print(f"    {sum(1 for x in gh if 10 <= x['stars'] < 50):>5} have 10 to 49")
            print(f"    {sum(1 for x in gh if x['stars'] < 10):>5} have under 10")
            print(f"    {sum(1 for x in gh if not x['licence']):>5} declare no licence")
            top = sorted(gh, key=lambda x: -x["stars"])[:15]
            print(f"\n  {'stars':>6}  {'licence':<14} repository")
            for x in top:
                print(f"  {x['stars']:>6}  {(x['licence'] or '-'):<14} {x['name']}")

    withhw = [x for x in alive if x.get("robots")]
    if withhw:
        import collections as _c
        c = _c.Counter(e for x in withhw for e in x["robots"].split("; ") if e)
        print(f"\n  {len(withhw)} name identifiable hardware "
              f"({len(withhw)/max(1,len(alive))*100:.0f}%)")
        for k, v in c.most_common(12):
            print(f"      {v:>4}  {k}")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return 0

    path = a.out or a.csv_in.replace(".csv", "-validated.csv")
    cols = ["kind", "ref", "status", "name", "stars", "forks", "downloads",
            "licence", "created", "pushed", "size_kb", "archived", "empty",
            "language", "description", "robots", "url", "papers", "paper_count"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    print(f"\nwritten to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
