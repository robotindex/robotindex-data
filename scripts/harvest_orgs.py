#!/usr/bin/env python3
"""
harvest_orgs.py — list everything a watched organisation publishes on GitHub.

The topic search in harvest_gh.py finds repositories that have earned stars and
set a topic tag. Both take time, so the search is structurally blind to anything
new. Pantheon's Argus annotator went up on 1 October 2026, audited nine of the
largest open robotics datasets, and was invisible to a harvest run on 3 October
because it had neither 50 stars nor, apparently, a topic we search.

This closes that gap from the other side. Listing an organisation's repositories
is one API call with no star floor, no topic requirement and no date window, so
anything a watched org publishes shows up the same day. 200 orgs cost 200 calls
against the 5,000/hour authenticated limit — trivial next to the 4,165 the topic
search makes.

The watch list is derived from the data rather than guessed. Any owner already
in the index with two or more repositories and at least one active, or with
2,000+ stars and at least one active, is by definition an organisation that
publishes robotics work. Run with --derive to regenerate it from a harvest CSV;
otherwise it reads data/watched-orgs.json, which can be hand-edited to add orgs
that have not published anything we caught yet.

    python scripts/harvest_orgs.py --out orgs-new.csv
    python scripts/harvest_orgs.py --derive data/history/gh-2026-10-03.csv
    python scripts/harvest_orgs.py --since 2026-09-01

Output is only what is NOT already in data/history, so the result is a short
list of things the topic search missed, not another full census.
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
import urllib.request

API = "https://api.github.com"
ORGS_FILE = os.path.join("data", "watched-orgs.json")

# Same filters as the topic search, so a forgotten tutorial repository in a
# watched org does not get through just because of who owns it.
JUNK_NAME = re.compile(
    r"(^|[_-])(test|tests|demo|tutorial|tutorials|example|examples|homework|"
    r"assignment|coursework|practice|playground|sandbox|template|boilerplate|"
    r"starter|hello[_-]?world|my[_-]?\w+|learning[_-]|study|notes|cheatsheet|"
    r"interview|roadmap|awesome)([_-]|\d*$)", re.I)

ROBOTICS_WORD = re.compile(
    r"\b(robot|ros2?\b|slam\b|lidar|manipul|grasp|humanoid|quadruped|biped|"
    r"legged|drone|uav\b|rover|cobot|exoskelet|teleop|urdf|kinemat|locomot|"
    r"embodied|odometr|point.?cloud|mujoco|gazebo|isaac|pybullet|gripper|"
    r"actuator|servo|end.?effector|mobile.?base|mecanum|agv\b|amr\b|vla\b|"
    r"rlds\b|sim2real|sim.to.real|physical.ai|visual.inertial|annotat|"
    r"autonomous (vehicle|driving|robot|navigation|system|flight)|"
    r"motion planning|path planning|whole.?body|self.driving|"
    r"depth camera|rgb.?d\b|imu\b|moveit|nav2)", re.I)


def gh(url, token, tries=3):
    req = urllib.request.Request(url, headers={
        "User-Agent": "robotindex-orgs",
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}" if token else "",
    })
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429) and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 20)
                print(f"    rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception:
            if attempt < tries - 1:
                time.sleep(3)
                continue
            raise


def derive_watchlist(csv_path, min_repos=2, min_stars=2000):
    """Build the watch list from a harvest CSV. An owner qualifies on either
    count (several repositories, at least one alive) or weight (one repository
    with real traction, still alive). Both require something active: an org
    that has stopped publishing is not worth a call a month."""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    by = {}
    for r in rows:
        by.setdefault(r["owner"], []).append(r)
    out = []
    for owner, rs in by.items():
        active = sum(1 for x in rs if x["status"] == "active")
        stars = sum(int(x["stars"]) for x in rs)
        if active and (len(rs) >= min_repos or stars >= min_stars):
            out.append(owner)
    return sorted(out, key=str.lower)


def known_repos():
    """Everything already in data/history, so we only report what is new."""
    seen = set()
    for path in glob.glob(os.path.join("data", "history", "gh-*.csv")):
        try:
            for r in csv.DictReader(open(path, encoding="utf-8")):
                seen.add(r["repo"].lower())
        except Exception:
            continue
    return seen


def list_org(owner, token, since):
    """Every repository an owner has, newest first. Tries the org endpoint and
    falls back to the user one, because several of the most interesting
    publishers are individuals rather than organisations."""
    out = []
    for kind in ("orgs", "users"):
        page = 1
        while page <= 4:
            url = (f"{API}/{kind}/{owner}/repos?type=public&sort=created"
                   f"&direction=desc&per_page=100&page={page}")
            try:
                batch = gh(url, token)
            except Exception as e:
                print(f"  {owner}: {str(e)[:60]}", file=sys.stderr)
                return out
            if batch is None:
                break           # wrong endpoint, try the other
            if not batch:
                return out
            stop = False
            for it in batch:
                created = (it.get("created_at") or "")[:10]
                if since and created < since:
                    stop = True     # sorted by created desc, so we are done
                    break
                out.append(it)
            if stop or len(batch) < 100:
                return out
            page += 1
            time.sleep(0.3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="orgs-new.csv")
    ap.add_argument("--since", default=None,
                    help="only repos created on or after this date (default: 90 days ago)")
    ap.add_argument("--derive", metavar="CSV",
                    help="regenerate data/watched-orgs.json from a harvest CSV and exit")
    ap.add_argument("--min-stars", type=int, default=0,
                    help="star floor; 0 by default, because a repo published today has none")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.derive:
        orgs = derive_watchlist(a.derive)
        os.makedirs("data", exist_ok=True)
        with open(ORGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"_comment": "Organisations watched by harvest_orgs.py. "
                                   "Derived from a harvest CSV; hand-edit to add "
                                   "publishers we have not caught yet.",
                       "orgs": orgs}, f, indent=2)
        print(f"Wrote {len(orgs)} orgs to {ORGS_FILE}")
        return 0

    try:
        orgs = json.load(open(ORGS_FILE, encoding="utf-8"))["orgs"]
    except Exception as e:
        print(f"FATAL: could not read {ORGS_FILE}: {e}\n"
              f"Run with --derive data/history/gh-YYYY-MM-DD.csv first.", file=sys.stderr)
        return 1

    since = a.since or (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    token = os.environ.get("GITHUB_TOKEN")
    seen = known_repos()
    print(f"{len(orgs)} orgs, repos created since {since}, "
          f"{len(seen)} already known\n", file=sys.stderr)

    today = datetime.date.today()
    found, dropped = [], {"already_known": 0, "fork": 0, "junk_name": 0, "not_robotics": 0}
    for i, owner in enumerate(orgs, 1):
        for it in list_org(owner, token, since):
            full = it["full_name"]
            if full.lower() in seen:
                dropped["already_known"] += 1
                continue
            if it.get("fork"):
                dropped["fork"] += 1
                continue
            name = full.split("/", 1)[1]
            if JUNK_NAME.search(name):
                dropped["junk_name"] += 1
                continue
            desc = it.get("description") or ""
            if not ROBOTICS_WORD.search(desc + " " + full):
                dropped["not_robotics"] += 1
                continue
            if it["stargazers_count"] < a.min_stars:
                continue
            pushed = (it.get("pushed_at") or "")[:10]
            age = (today - datetime.date.fromisoformat(pushed)).days if pushed else None
            lic = (it.get("license") or {}).get("spdx_id") or ""
            found.append({
                "repo": full, "owner": owner, "stars": it["stargazers_count"],
                "forks": it.get("forks_count", 0),
                "license": "" if lic == "NOASSERTION" else lic,
                "created": (it.get("created_at") or "")[:10], "pushed": pushed,
                "days_since_push": age, "language": it.get("language") or "",
                "topics": " ".join(it.get("topics") or []),
                "url": it["html_url"],
                "description": re.sub(r"\s+", " ", desc)[:300],
            })
        if i % 25 == 0:
            print(f"  [{i}/{len(orgs)}] {len(found)} new so far", file=sys.stderr)
        time.sleep(0.15)

    found.sort(key=lambda r: r["created"], reverse=True)
    print(f"\n{len(found)} repositories not already in the index")
    for k, v in dropped.items():
        print(f"  -{v:<6} {k}")
    if found:
        print(f"\n{'created':<12} {'stars':>6}  {'licence':<14} repo")
        for r in found[:40]:
            print(f"{r['created']:<12} {r['stars']:>6}  {(r['license'] or '-'):<14} {r['repo']}")
        if len(found) > 40:
            print(f"{'':<12} {'':>6}  ... and {len(found)-40} more")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return 0

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(found[0].keys()) if found
                           else ["repo"])
        w.writeheader()
        w.writerows(found)
    print(f"\nwritten to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
