#!/usr/bin/env python3
"""
harvest_arxiv.py — find the repositories and datasets that robotics papers
announce, before either platform's own signals catch up.

The GitHub harvest needs stars and the Hugging Face harvest needs downloads.
Both lag by weeks, because a release only earns either after people find it. A
paper, by contrast, names its repository and its dataset on the day it appears,
in the abstract, in the comment field, or on the project page.

So this reads recent cs.RO submissions and pulls every github.com,
huggingface.co, Zenodo, OSF and bucket URL out of them, then reports only what
is not already in data/history. The output is a short list of releases the two
harvests have not seen.

What it will not do is find work that was never written up. Pantheon's Argus
annotator was announced in a research blog post with no arXiv paper, and would
be missed here as it was missed everywhere else. Discovery of that kind needs a
person reading, and this is not a substitute for it.

    python scripts/harvest_arxiv.py --days 30 --out arxiv-new.csv
    python scripts/harvest_arxiv.py --days 7 --dry-run

The arXiv API asks for no more than one request every three seconds and no
authentication. This honours that; a 30-day window is roughly 10 requests.
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

API = "http://export.arxiv.org/api/query"

# cs.RO is the core, but a lot of manipulation and world-model work is filed
# under vision or learning with robotics only as a cross-list. Searching the
# categories directly is more reliable than keyword matching on the abstract.
CATEGORIES = ["cs.RO"]
CROSSLIST = ["cs.CV", "cs.LG", "cs.AI"]

# Terms that mark a paper as announcing an artefact rather than only a method.
# Applied only to the cross-listed categories, where cs.RO alone is too broad.
ARTEFACT = re.compile(
    r"\b(dataset|benchmark|we release|we open.?source|publicly available|"
    r"open.?source(d)?|code and data|data and code|available at|"
    r"we provide|toolkit|simulator|annotations?)\b", re.I)

GITHUB = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+?)(?=[\s,.)\]\"'<]|$)", re.I)
HUGGINGFACE = re.compile(
    r"huggingface\.co/(?:datasets/)?([\w.\-]+)/([\w.\-]+?)(?=[\s,.)\]\"'<]|$)", re.I)
OTHER_HOST = re.compile(
    r"(zenodo\.org/record[s]?/\d+|osf\.io/\w{5}|figshare\.com/\S+|"
    r"dataverse\.[\w.]+/\S+|storage\.googleapis\.com/[\w\-./]+|"
    r"[\w\-]+\.s3[.\-][\w\-.]*amazonaws\.com/[\w\-./]*)", re.I)
PROJECT_PAGE = re.compile(r"https?://([\w\-]+\.github\.io[\w\-/.]*)", re.I)

# A repo path that is obviously not a release.
JUNK_REPO = re.compile(r"^(blob|tree|search|topics|about|features|pricing)$", re.I)


def fetch(url, timeout=30, tries=4, pause=4.0):
    """GET with retries. arXiv returns an empty feed often enough that a single
    attempt is unreliable: a first backfill run lost six consecutive months
    because each one's first page came back empty and the loop moved on. An
    empty body is treated as a failure worth retrying, not as an answer."""
    req = urllib.request.Request(url, headers={"User-Agent": "robotindex-arxiv"})
    last = ""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                last = r.read().decode("utf-8", "replace")
            if "<entry>" in last or "totalResults>0<" in last:
                return last          # real answer, including a real zero
        except Exception as e:
            print(f"    fetch attempt {attempt+1}: {str(e)[:50]}", file=sys.stderr)
        if attempt < tries - 1:
            time.sleep(pause * (attempt + 1))
    return last


def entries(xml):
    return re.findall(r"<entry>([\s\S]*?)</entry>", xml)


def tag(name, entry):
    short = name.split(":")[-1]
    m = re.search(rf"<{name}[^>]*>([\s\S]*?)</(?:arxiv:)?{short}>", entry)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def known():
    """Everything already harvested, so we only report what is new."""
    repos, datasets = set(), set()
    for path in glob.glob(os.path.join("data", "history", "gh-*.csv")):
        try:
            for r in csv.DictReader(open(path, encoding="utf-8")):
                repos.add(r["repo"].lower())
        except Exception:
            pass
    for path in glob.glob(os.path.join("data", "history", "hf-*.csv")):
        try:
            for r in csv.DictReader(open(path, encoding="utf-8")):
                datasets.add(r["id"].lower())
        except Exception:
            pass
    return repos, datasets


def query(cat, start, per_page=100, window=None):
    """One page of a category. `window` is an optional (from, to) pair of dates,
    used by the backfill: arXiv caps a single result set at 30,000, and slicing
    by submission month keeps every request well inside that and well inside the
    paging the API will actually serve."""
    q = f"cat:{cat}"
    if window:
        a, b = window
        q += f" AND submittedDate:[{a.replace('-','')}0000 TO {b.replace('-','')}2359]"
    url = (f"{API}?search_query={urllib.parse.quote(q)}"
           f"&sortBy=submittedDate&sortOrder=descending"
           f"&start={start}&max_results={per_page}")
    return fetch(url)


def months_back(years):
    """(first, last) day of each month, newest first, going back `years`."""
    today = datetime.date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(years * 12):
        first = datetime.date(y, m, 1)
        nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
        last = datetime.date(nm_y, nm_m, 1) - datetime.timedelta(days=1)
        out.append((first.isoformat(), min(last, today).isoformat()))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def backfill(years, per_page=200, pause=3.2, max_pages=40):
    """Walk cs.RO back `years`, one submission month at a time.

    Separate from harvest() because the shapes differ: the weekly run reads the
    newest papers until it passes a cutoff, while this needs every paper in a
    fixed window and has to page through each month. Two years of cs.RO is
    roughly 8,000 to 10,000 papers — around 60 requests, a few minutes at the
    rate arXiv asks for.

    Bounded deliberately. cs.RO goes back to 1993, and a repository named in a
    paper from 2014 is overwhelmingly likely to be dead; half the robotics code
    we track is already inactive at a one-year threshold. Two years matches the
    2015 floor on the GitHub harvest and covers the current generation of work.
    """
    papers, seen, incomplete = [], set(), []
    windows = months_back(years)
    for wi, (a, b) in enumerate(windows, 1):
        got = 0
        for page in range(max_pages):
            try:
                xml = query("cs.RO", page * per_page, per_page, window=(a, b))
            except Exception as e:
                print(f"  {a}: {str(e)[:60]}", file=sys.stderr)
                break
            ents = entries(xml)
            if not ents:
                if page == 0:
                    total = re.search(r"totalResults[^>]*>(\d+)<", xml)
                    if not total or total.group(1) != "0":
                        print(f"  {a}: first page empty after retries — window may be "
                              f"incomplete", file=sys.stderr)
                        incomplete.append(a)
                break
            for e in ents:
                aid = tag("id", e).rsplit("/", 1)[-1]
                if aid in seen:
                    continue
                seen.add(aid)
                papers.append({
                    "arxiv_id": aid,
                    "published": tag("published", e)[:10],
                    "title": tag("title", e),
                    "categories": " ".join(re.findall(r'term="([\w.\-]+)"', e)),
                    "text": tag("summary", e) + " " + tag("arxiv:comment", e),
                    "url": f"https://arxiv.org/abs/{aid}",
                })
                got += 1
            if len(ents) < per_page:
                break
            time.sleep(pause)
        print(f"  [{wi}/{len(windows)}] {a}: {got} papers, {len(papers)} total",
              file=sys.stderr)
        time.sleep(pause)
    if incomplete:
        print(f"\n{len(incomplete)} window(s) returned nothing and may be incomplete: "
              f"{', '.join(incomplete)}\nRe-run with --backfill-years to fill them.",
              file=sys.stderr)
    return papers


def harvest(days, max_pages=12):
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    papers, seen_ids = [], set()
    for cat in CATEGORIES + CROSSLIST:
        stop = False
        for page in range(max_pages):
            try:
                xml = query(cat, page * 100)
            except Exception as e:
                print(f"  {cat} page {page}: {str(e)[:60]}", file=sys.stderr)
                break
            ents = entries(xml)
            if not ents:
                break
            for e in ents:
                pub = tag("published", e)[:10]
                if pub < cutoff:
                    stop = True
                    continue
                aid = tag("id", e).rsplit("/", 1)[-1]
                if aid in seen_ids:
                    continue
                cats = re.findall(r'term="([\w.\-]+)"', e)
                summary = tag("summary", e)
                comment = tag("arxiv:comment", e)
                # a cross-listed paper must also be tagged cs.RO, and must look
                # like it is announcing something rather than only describing a
                # method, or the volume from cs.CV alone is unmanageable
                if cat != "cs.RO":
                    if "cs.RO" not in cats:
                        continue
                    if not ARTEFACT.search(summary + " " + comment):
                        continue
                seen_ids.add(aid)
                papers.append({
                    "arxiv_id": aid,
                    "published": pub,
                    "title": tag("title", e),
                    "categories": " ".join(cats),
                    "text": summary + " " + comment,
                    "url": f"https://arxiv.org/abs/{aid}",
                })
            print(f"  {cat}: {len(papers)} papers so far", file=sys.stderr)
            if stop or len(ents) < 100:
                break
            time.sleep(3.2)        # arXiv asks for one request per three seconds
        time.sleep(3.2)
    return papers


def extract(papers, known_repos, known_datasets):
    rows = []
    for p in papers:
        t = p["text"]
        repos = {f"{a}/{b}" for a, b in GITHUB.findall(t) if not JUNK_REPO.match(b)}
        dsets = {f"{a}/{b}" for a, b in HUGGINGFACE.findall(t)}
        other = set(OTHER_HOST.findall(t))
        proj = set(PROJECT_PAGE.findall(t))
        if not (repos or dsets or other):
            continue
        new_repos = sorted(r for r in repos if r.lower() not in known_repos)
        new_dsets = sorted(d for d in dsets if d.lower() not in known_datasets)
        rows.append({
            "arxiv_id": p["arxiv_id"],
            "published": p["published"],
            "title": p["title"][:200],
            "categories": p["categories"],
            "url": p["url"],
            "repos": "; ".join(sorted(repos)),
            "new_repos": "; ".join(new_repos),
            "datasets": "; ".join(sorted(dsets)),
            "new_datasets": "; ".join(new_dsets),
            "other_hosts": "; ".join(sorted(other)),
            "project_page": "; ".join(sorted(proj)),
            "is_new": bool(new_repos or new_dsets or other),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--backfill-years", type=int, default=0,
                    help="instead of the recent window, walk cs.RO back this many years, "
                         "one submission month at a time. 2 is the intended value: it "
                         "matches the 2015 floor on the GitHub harvest and avoids "
                         "collecting repositories that died years ago.")
    ap.add_argument("--out", default="arxiv-new.csv")
    ap.add_argument("--all", action="store_true",
                    help="write every paper with a link, not only the ones with "
                         "something we have not already harvested")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    known_repos, known_datasets = known()
    print(f"{len(known_repos)} repos and {len(known_datasets)} datasets already "
          f"harvested\nlooking back {a.days} days\n", file=sys.stderr)

    if a.backfill_years:
        print(f"backfilling {a.backfill_years} years of cs.RO\n", file=sys.stderr)
        papers = backfill(a.backfill_years)
    else:
        papers = harvest(a.days)
    rows = extract(papers, known_repos, known_datasets)
    new = [r for r in rows if r["is_new"]]

    print(f"\n{len(papers)} papers | {len(rows)} name a repo or dataset | "
          f"{len(new)} name something we have not harvested")

    if new:
        nr = sorted({x for r in new for x in r["new_repos"].split("; ") if x})
        nd = sorted({x for r in new for x in r["new_datasets"].split("; ") if x})
        no = sorted({x for r in new for x in r["other_hosts"].split("; ") if x})
        print(f"\n  {len(nr)} repositories not in the GitHub harvest")
        for x in nr[:25]:
            print(f"    github.com/{x}")
        print(f"\n  {len(nd)} datasets not in the Hugging Face harvest")
        for x in nd[:25]:
            print(f"    huggingface.co/datasets/{x}")
        if no:
            print(f"\n  {len(no)} releases hosted elsewhere")
            for x in no[:15]:
                print(f"    {x}")
        print(f"\n{'date':<12} {'arXiv':<12} title")
        for r in new[:25]:
            print(f"{r['published']:<12} {r['arxiv_id']:<12} {r['title'][:60]}")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return 0

    out = rows if a.all else new
    if not out:
        print("\nnothing to write")
        return 0
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"\nwritten to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
