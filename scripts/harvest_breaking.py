#!/usr/bin/env python3
"""
harvest_breaking.py — scan Hacker News and Reddit for robotics work that has
just been released.

Every other route here has a structural delay. The GitHub harvest waits for
fifty stars and the Hugging Face harvest for a hundred downloads. The
organisation sweep only asks accounts we already decided to watch. arXiv only
sees work that was written up as a paper.

Pantheon's Argus fell through all four. It was published on 1 October, audited
nine of the largest open robotics datasets, had no paper, belonged to a company
nobody here had heard of, and was missed by a harvest two days later. It
reached us because a person saw a link.

This is that person, automated. Hacker News and r/robotics are where releases
get announced on day zero, by the people who made them, before any metric
exists. Neither needs an API key.

What it does NOT do is decide anything. The output is a short list for a human
to read, with each item marked as already-known or new. Social posts are a
noisy signal and the whole point of the other harvests is that they are not.

    python scripts/harvest_breaking.py --days 7
    python scripts/harvest_breaking.py --days 30 --dry-run
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
import urllib.error
import urllib.parse
import urllib.request

HN = "https://hn.algolia.com/api/v1/search_by_date"
REDDIT = "https://www.reddit.com/r/{sub}/new.json"
SUBS = ["robotics", "ROS", "reinforcementlearning", "embedded"]

UA = ("robotindex/1.0 (+https://robotindex.io; weekly robotics release scan; "
      "contact via github.com/robotindex)")

# Hacker News has no robotics section, so the query carries the filtering.
HN_QUERIES = ["robot", "robotics", "humanoid", "teleoperation", "manipulation robot",
              "robot arm", "quadruped", "ROS2", "embodied AI", "robot dataset",
              "robot learning", "sim2real"]

# A post is only interesting if it points at something we could index.
GITHUB = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+?)(?=[\s,.)\]\"'<>]|$)", re.I)
HUGGINGFACE = re.compile(
    r"huggingface\.co/(?:datasets/|models/)?([\w.\-]+)/([\w.\-]+?)(?=[\s,.)\]\"'<>]|$)", re.I)

# Enough robotics in the title to be worth a human's attention. Deliberately
# generous: this feed is read by a person, and a false positive costs a glance
# while a false negative costs the thing we built this for.
ROBOTICS = re.compile(
    r"\b(robot|robotic|robotics|humanoid|quadruped|biped|legged|drone|uav|rover|"
    r"manipulat|gripper|end.?effector|teleop|embodied|locomotion|actuator|servo|"
    r"ros2?\b|moveit|nav2|urdf|mujoco|isaac|gazebo|sim2real|sim.to.real|"
    r"vla\b|lerobot|cobot|exoskeleton|slam\b|lidar|proprioception|dexterous|"
    r"motion.planning|whole.body|autonomous (vehicle|driving|navigation))", re.I)

# Things that match "robot" and are not robotics.
NOISE = re.compile(r"\b(robots?\.txt|robocall|robo.?advisor|robinhood|robot vacuum deal|"
                   r"chatbot|robotic process automation|rpa\b)", re.I)

JUNK_REPO = re.compile(r"^(blob|tree|search|topics|about|features|pricing|sponsors|"
                       r"orgs|users|settings|login)$", re.I)


def get(url, tries=3, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 20)
                print(f"    rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(min(wait, 120))
                continue
            if attempt == tries - 1:
                return None, f"http {e.code}"
        except Exception as e:
            if attempt == tries - 1:
                return None, str(e)[:50]
        time.sleep(3 * (attempt + 1))
    return None, "failed"


def known():
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


def watched_orgs():
    """Organisations already on the curated watch list, so the feed can say
    whether a post is from someone we follow or from someone new."""
    try:
        m = json.load(open(os.path.join("data", "watched-orgs-map.json"),
                           encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for block in m["categories"].values():
        for o in block["orgs"]:
            for k in ("github", "huggingface"):
                if o.get(k):
                    out.add(o[k].lower())
    return out


def scan_hn(since_ts, per_query=50):
    out, seen = [], set()
    for q in HN_QUERIES:
        url = (f"{HN}?query={urllib.parse.quote(q)}&tags=story"
               f"&numericFilters=created_at_i>{since_ts}&hitsPerPage={per_query}")
        d, err = get(url)
        if err:
            print(f"  hn '{q}': {err}", file=sys.stderr)
            continue
        for h in (d.get("hits") or []):
            oid = h.get("objectID")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            title = h.get("title") or h.get("story_title") or ""
            out.append({
                "source": "hacker news",
                "title": title,
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                "discussion": f"https://news.ycombinator.com/item?id={oid}",
                "score": h.get("points") or 0,
                "comments": h.get("num_comments") or 0,
                "created": (h.get("created_at") or "")[:10],
                "text": f"{title} {h.get('url') or ''} {(h.get('story_text') or '')[:2000]}",
            })
        time.sleep(0.4)
    return out


def scan_reddit(since_ts, limit=100):
    out = []
    for sub in SUBS:
        after, pages = None, 0
        while pages < 3:
            url = f"{REDDIT.format(sub=sub)}?limit={limit}" + (f"&after={after}" if after else "")
            d, err = get(url)
            if err:
                print(f"  r/{sub}: {err}", file=sys.stderr)
                break
            children = (d.get("data") or {}).get("children") or []
            if not children:
                break
            stop = False
            for c in children:
                p = c.get("data") or {}
                if (p.get("created_utc") or 0) < since_ts:
                    stop = True
                    break
                title = p.get("title") or ""
                out.append({
                    "source": f"r/{sub}",
                    "title": title,
                    "url": p.get("url_overridden_by_dest") or p.get("url") or "",
                    "discussion": "https://www.reddit.com" + (p.get("permalink") or ""),
                    "score": p.get("score") or 0,
                    "comments": p.get("num_comments") or 0,
                    "created": datetime.datetime.utcfromtimestamp(
                        p.get("created_utc") or 0).date().isoformat(),
                    "text": f"{title} {p.get('url') or ''} {(p.get('selftext') or '')[:2000]}",
                })
            after = (d.get("data") or {}).get("after")
            pages += 1
            if stop or not after:
                break
            time.sleep(1.2)       # Reddit is stricter than HN
        time.sleep(1.2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default="breaking.csv")
    ap.add_argument("--min-score", type=int, default=3,
                    help="ignore posts below this score; 3 filters the unread without "
                         "requiring a post to have gone anywhere")
    ap.add_argument("--links-only", action="store_true",
                    help="only keep posts that point at a repository or dataset")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    since = datetime.datetime.utcnow() - datetime.timedelta(days=a.days)
    since_ts = int(since.timestamp())
    known_repos, known_datasets = known()
    watched = watched_orgs()
    print(f"scanning the last {a.days} days\n"
          f"{len(known_repos)} repos and {len(known_datasets)} datasets already known, "
          f"{len(watched)} accounts watched\n", file=sys.stderr)

    posts = scan_hn(since_ts) + scan_reddit(since_ts)
    print(f"  {len(posts)} posts fetched", file=sys.stderr)

    rows, dropped = [], {"not_robotics": 0, "noise": 0, "low_score": 0, "no_links": 0}
    for p in posts:
        if NOISE.search(p["title"]):
            dropped["noise"] += 1
            continue
        if not ROBOTICS.search(p["title"]):
            dropped["not_robotics"] += 1
            continue
        if p["score"] < a.min_score:
            dropped["low_score"] += 1
            continue
        repos = {f"{o}/{r}" for o, r in GITHUB.findall(p["text"])
                 if not JUNK_REPO.match(r)}
        dsets = {f"{o}/{r}" for o, r in HUGGINGFACE.findall(p["text"])}
        if a.links_only and not (repos or dsets):
            dropped["no_links"] += 1
            continue
        new_r = sorted(r for r in repos if r.lower() not in known_repos)
        new_d = sorted(d for d in dsets if d.lower() not in known_datasets)
        owners = {r.split("/")[0].lower() for r in repos} | \
                 {d.split("/")[0].lower() for d in dsets}
        rows.append({
            **{k: p[k] for k in ("source", "created", "title", "score", "comments",
                                 "url", "discussion")},
            "repos": "; ".join(sorted(repos)),
            "new_repos": "; ".join(new_r),
            "datasets": "; ".join(sorted(dsets)),
            "new_datasets": "; ".join(new_d),
            "watched_org": "yes" if owners & watched else ("no" if owners else ""),
            "is_new": bool(new_r or new_d),
        })

    rows.sort(key=lambda r: (-int(r["is_new"]), -r["score"]))
    new = [r for r in rows if r["is_new"]]
    unwatched = [r for r in new if r["watched_org"] == "no"]

    print(f"\n{len(posts)} posts | {len(rows)} robotics | {len(new)} name something "
          f"we do not have")
    for k, v in dropped.items():
        print(f"  -{v:<5} {k}")

    if unwatched:
        print(f"\n{len(unwatched)} are from accounts NOT on the watch list — these are "
              f"the ones worth reading first:")
        for r in unwatched[:20]:
            print(f"\n  [{r['source']}, {r['score']} points] {r['title'][:84]}")
            for x in r["new_repos"].split("; "):
                if x:
                    print(f"      github.com/{x}")
            for x in r["new_datasets"].split("; "):
                if x:
                    print(f"      huggingface.co/{x}")
            print(f"      {r['discussion']}")

    rest = [r for r in new if r["watched_org"] != "no"]
    if rest:
        print(f"\n{len(rest)} more from accounts already watched or with no link:")
        for r in rest[:15]:
            print(f"  [{r['source']}, {r['score']}] {r['title'][:76]}")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return 0
    if not rows:
        print("\nnothing to write")
        return 0
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwritten to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
