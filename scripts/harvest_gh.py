#!/usr/bin/env python3
"""
harvest_gh.py — pull the robotics repositories on GitHub worth indexing.

Two limits shape this. The search API returns at most 1,000 results per query
however many pages you request, and it allows 30 requests a minute even with a
token. So the work is splitting one broad question into many narrow ones and
pacing them.

Partitioning: each topic is searched in calendar-quarter slices of creation
date. `topic:robotics created:2026-01-01..2026-03-31 stars:>=50` returns a few
hundred rather than tens of thousands, comfortably inside the cap. Any slice
that comes back at exactly 1,000 is reported as truncated so the window can be
narrowed.

Filtering, in the same spirit as the Hugging Face harvest:

  1. stars      — a floor, default 50.
  2. forks      — excluded at query level with fork:false.
  3. name       — test / demo / tutorial / homework / awesome and similar.
  4. owner cap  — one account cannot dominate the set.
  5. not-robotics — agent-skill packs, coding harnesses and personal blogs that
                 tag themselves robotics. Learned the hard way: an earlier pass
                 returned 202 repos of which 53 were not robotics at all.

What gets recorded per repo is deliberately weighted toward health rather than
popularity. GitHub has no download count, so stars measure attention, not use.
Last push, archived status and open issues say whether anyone is still there —
which for someone choosing a dependency is the more useful question.

    python scripts/harvest_gh.py --min-stars 50 --out gh-candidates.csv
    python scripts/harvest_gh.py --min-stars 100 --since 2024 --dry-run

Needs GITHUB_TOKEN. Unauthenticated search is 10 requests a minute and this
makes several hundred.
"""

import argparse
import csv
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

SEARCH = "https://api.github.com/search/repositories"

TOPICS = [
    # core
    "robotics", "ros", "ros2", "ros2-humble", "robot-learning", "embodied-ai",
    "imitation-learning", "humanoid-robot", "humanoid", "robot-manipulation",
    "manipulation", "autonomous-robots",
    # models and action generation
    "vision-language-action", "vision-language-action-model", "vla",
    "diffusion-policy", "diffusion-models", "world-models", "world-model",
    "physical-ai", "vision-language-model", "vlm",
    # simulation, and the policy-learning engines built on top of it
    "sim-to-real", "sim2real", "isaac-sim", "isaac-lab", "isaacgym", "mujoco",
    "gazebo", "pybullet", "genesis", "maniskill", "robosuite", "robomimic",
    "robotics-simulation", "simulator",
    # dataset schemas and formats — the bridge between code and the data hubs
    "rlds", "open-x-embodiment",
    # learning
    "reinforcement-learning", "deep-reinforcement-learning",
    # locomotion
    "legged-locomotion", "quadruped", "bipedal-locomotion",
    "whole-body-control",
    # planning and control
    "motion-planning", "path-planning", "trajectory-optimization",
    "inverse-kinematics", "mpc", "navigation",
    # dexterity, bimanual and tactile
    "bimanual-manipulation", "dexterous-manipulation", "tactile-sensing",
    "grasping",
    # teleoperation, middleware and hardware
    "teleoperation", "lerobot", "urdf", "zenoh", "micro-ros", "rerun",
    "drone", "uav",
    # perception
    "slam", "lidar-slam", "point-cloud", "lidar-point-cloud", "lidar",
    "6d-pose-estimation", "3d-reconstruction", "3d-vision",
    "gaussian-splatting", "localization", "mapping", "odometry",
    "sensor-fusion", "3d-object-detection", "perception", "place-recognition",
]

# Broad tags that are honey pots on their own — arduino alone returns LED
# controllers and weather stations — so they are paired with a robotics tag.
# GitHub treats multiple topic: qualifiers as AND.
QUALIFIED = [
    ("arduino", "robotics"), ("esp32", "robotics"), ("raspberry-pi", "robotics"),
    ("object-detection", "robotics"), ("pose-estimation", "robotics"),
    ("control", "robotics"), ("planning", "robotics"),
]

# Checked against the live API and dropped as empty or near-empty:
#   quadrupedal-robot 0, isaac-orbit 0, genesis-sim 1, sim2real-transfer 1, wbc 2.
# legged-locomotion was dropped after the first run showed 1 repo, but a direct
# query returns 24 — the first run missed them on the star floor and the 2020
# cutoff, not because the tag is unused. It is back.

JUNK_NAME = re.compile(
    r"(^|[_-])(test|tests|demo|tutorial|tutorials|example|examples|homework|"
    r"assignment|coursework|practice|playground|sandbox|template|boilerplate|"
    r"starter|hello[_-]?world|my[_-]?\w+|learning[_-]|study|notes|cheatsheet|"
    r"interview|roadmap|awesome)([_-]|\d*$)", re.I)

# Descriptions that mean "not robotics" however the repo is tagged. Each of
# these was a false positive in an earlier manual pass.
NOT_ROBOTICS = re.compile(
    r"\b(claude|cursor|copilot|coding agent|agent skills?|skill pack|"
    r"prompt|llm wrapper|mcp server|awesome[- ]list|curated list|"
    r"paper list|reading list|my (blog|portfolio|website|notes)|"
    r"interview (prep|questions)|leetcode|connectome|fruit fly|"
    r"minecraft|video game|game engine demo|"
    # web scraping tools that tag themselves robotics
    r"web ?(crawler|scraper|scraping|spider)|headless browser|"
    r"screen ?scrap|typing simulator|robotic process automation|"
    # adjacent domains that tag themselves robotics
    r"text[- ]to[- ]cad|cad (web ?app|superpowers|application)|openscad|"
    r"web-based user interfaces?|gui (framework|library)|frontend framework|"
    r"image synthesis|text[- ]to[- ]image|video generation model|"
    r"speech synthesis|no-code|low-code)\b", re.I)

# Reading material rather than runnable code. Checked against description and
# name. "A collection of models" is a usable artifact; "a survey of models" is
# not, so the wording here is deliberately narrow.
BOOKISH = re.compile(
    r"\b(text ?book|handbook|lecture notes?|course material|a survey (of|for|on)|"
    r"paper list|reading list|roadmap|awesome[- ]|study notes?|tutorial series|"
    r"learning path|book)\b"
    # \b does not apply to CJK, so these are matched without it
    r"|教程|书稿|指南|课程|笔记", re.I)

BOOKISH_NAME = re.compile(
    r"(^|[-_])(book|guide|survey|roadmap|awesome|papers?|notes|tutorial|"
    r"overview|cookbook)([-_]|$)", re.I)

PERMISSIVE = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC",
              "Unlicense", "0BSD", "MPL-2.0", "Zlib"}


# ---------------------------------------------------------------- embodiment

# Which robot a repo is for. Repo descriptions are 150 characters and rarely
# name hardware — a first pass over descriptions and topics alone resolved only
# 6% — so with --readme the README is fetched and searched too, the same way the
# Hugging Face harvest does it.
#
# Patterns require a manufacturer name or an unambiguous model token. Bare model
# numbers were tried and rejected: "spot-the-difference" matched Boston Dynamics
# and "the league's g1 division" matched a Unitree G1.
EMBODIMENT = [
    (r"\bfranka|panda arm|\bfr3\b",                            "Franka"),
    (r"unitree|\bgo1\b|\bgo2\b|aliengo|laikago",               "Unitree"),
    (r"\bg1\b(?=.{0,25}(humanoid|robot|loco))|\bh1\b(?=.{0,25}(humanoid|robot))", "Unitree"),
    (r"\bso-?10[01]\b|\bso-?arm\b",                            "SO-100/101"),
    (r"\baloha\b|\bviperx\b|widowx|trossen",                   "ALOHA / Trossen"),
    (r"\bur5e?\b|\bur10e?\b|universal robots",                 "Universal Robots"),
    (r"\bxarm\b|ufactory",                                     "xArm"),
    (r"\bkuka\b|\biiwa\b",                                     "KUKA"),
    (r"kinova|\bjaco\b",                                       "Kinova"),
    (r"\bsawyer\b|\bbaxter\b|rethink robotics",                "Rethink"),
    (r"turtlebot",                                              "TurtleBot"),
    (r"clearpath|\bjackal\b|\bhusky\b|\bdingo\b",             "Clearpath"),
    (r"boston dynamics|\banymal\b",                            "Boston Dynamics / ANYbotics"),
    (r"\bcassie\b|agility robotics",                           "Agility"),
    (r"crazyflie|bitcraze",                                     "Crazyflie"),
    (r"\bpx4\b|ardupilot|pixhawk",                             "PX4 / ArduPilot"),
    (r"hello robot|stretch (re1|re2|3|robot)",                  "Hello Robot Stretch"),
    (r"agilex|cobot magic",                                     "AgileX"),
    (r"mycobot|elephant robotics",                              "myCobot"),
    (r"\breachy\b|pollen robotics",                             "Pollen Reachy"),
    (r"\btiago\b|pal robotics",                                 "PAL"),
    (r"agibot",                                                 "AgiBot"),
    (r"fourier intelligence|\bgr-?1\b(?=.{0,20}(humanoid|robot))", "Fourier"),
    (r"galaxea",                                                "Galaxea"),
    (r"\bpr2\b|willow garage",                                  "PR2"),
    (r"\bfetch\b(?=.{0,20}(robot|mobile))",                     "Fetch"),
    (r"\bopencat\b|petoi",                                      "Petoi"),
    (r"\bdji\b|\btello\b|robomaster",                          "DJI"),
    (r"waveshare|jetbot|jetracer",                              "Waveshare / Jetson"),
    (r"\brobotis\b|dynamixel|\bop3\b",                         "ROBOTIS"),
    (r"\bicub\b",                                               "iCub"),
    (r"\bk-?scale\b|\bkbot\b|\bzbot\b",                       "K-Scale"),
    (r"neo gamma|1x technologies",                              "1X"),
    (r"figure ai|\bfigure 0[23]\b",                             "Figure"),
]


def fetch_readme(repo, token, timeout=25):
    """Raw README. Empty string on any failure — a missing readme is normal."""
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/README.md"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "robotindex-harvest"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(150_000).decode("utf-8", "replace")
        except Exception:
            continue
    return ""


def find_embodiment(text):
    out = []
    low = (text or "").lower()
    for pat, name in EMBODIMENT:
        if name not in out and re.search(pat, low):
            out.append(name)
    return out


def add_embodiment(rows, token, use_readme):
    """Description and topics first — free. README only where those fail."""
    for i, r in enumerate(rows, 1):
        quick = find_embodiment(r["description"] + " " + r["topics"] + " " + r["repo"])
        if quick:
            r["embodiment"], r["embodiment_source"] = "; ".join(quick), "description"
            continue
        if not use_readme:
            r["embodiment"], r["embodiment_source"] = "", ""
            continue
        found = find_embodiment(fetch_readme(r["repo"], token))
        r["embodiment"] = "; ".join(found)
        r["embodiment_source"] = "readme" if found else ""
        time.sleep(0.05)
        if i % 250 == 0:
            print(f"    ...readme {i}/{len(rows)}", file=sys.stderr)
    return rows


def gh(url, token, timeout=30, tries=4):
    """GET with backoff. Search is 30/min even authenticated, so 403 and 429
    are expected rather than exceptional."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "robotindex-harvest",
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}" if token else "",
    })
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or (8 * (attempt + 1)))
                print(f"    rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception:
            if attempt < tries - 1:
                time.sleep(4)
                continue
            raise


def quarters(since_year, until):
    """Calendar quarters from since_year to now, as (start, end) ISO strings."""
    out = []
    y = since_year
    while True:
        for q in ((1, 1, 3, 31), (4, 1, 6, 30), (7, 1, 9, 30), (10, 1, 12, 31)):
            a = datetime.date(y, q[0], q[1])
            b = datetime.date(y, q[2], q[3])
            if a > until:
                return out
            out.append((a.isoformat(), min(b, until).isoformat()))
        y += 1


def harvest(min_stars, since_year, token):
    today = datetime.date.today()
    slices = quarters(since_year, today)
    seen = {}
    truncated = []
    print(f"{len(TOPICS)} topics + {len(QUALIFIED)} qualified pairs, "
          f"x {len(slices)} quarters = "
          f"{(len(TOPICS)+len(QUALIFIED))*len(slices)} queries\n", file=sys.stderr)

    queries = [(t, f"topic:{t}") for t in TOPICS] + \
              [(f"{a}+{b}", f"topic:{a} topic:{b}") for a, b in QUALIFIED]

    for ti, (label, clause) in enumerate(queries, 1):
        got = 0
        for a, b in slices:
            q = f"{clause} created:{a}..{b} stars:>={min_stars} fork:false"
            page = 1
            while True:
                url = (SEARCH + "?q=" + urllib.parse.quote(q) +
                       f"&sort=stars&order=desc&per_page=100&page={page}")
                try:
                    d = gh(url, token)
                except Exception as e:
                    print(f"  {label} {a}: {str(e)[:60]}", file=sys.stderr)
                    break
                total = d.get("total_count", 0)
                if total >= 1000 and page == 1:
                    truncated.append((label, a, total))
                items = d.get("items", [])
                for it in items:
                    seen.setdefault(it["full_name"], it)
                got += len(items)
                if len(items) < 100 or page >= 10:
                    break
                page += 1
                time.sleep(2.2)
            time.sleep(2.2)
        print(f"  [{ti}/{len(queries)}] {label}: {got} results, "
              f"{len(seen)} unique so far", file=sys.stderr)
    return seen, truncated


def keep(items, owner_cap):
    rows, dropped = [], {"junk_name": 0, "not_robotics": 0, "bookish": 0, "owner_cap": 0}
    by_owner = {}
    today = datetime.date.today()
    for full, it in sorted(items.items(), key=lambda kv: -kv[1]["stargazers_count"]):
        owner, _, name = full.partition("/")
        desc = it.get("description") or ""
        if JUNK_NAME.search(name):
            dropped["junk_name"] += 1
            continue
        if NOT_ROBOTICS.search(desc) or NOT_ROBOTICS.search(name):
            dropped["not_robotics"] += 1
            continue
        if BOOKISH.search(desc) or BOOKISH_NAME.search(name):
            dropped["bookish"] += 1
            continue
        if by_owner.get(owner, 0) >= owner_cap:
            dropped["owner_cap"] += 1
            continue
        by_owner[owner] = by_owner.get(owner, 0) + 1

        pushed = (it.get("pushed_at") or "")[:10]
        age = (today - datetime.date.fromisoformat(pushed)).days if pushed else None
        status = ("archived" if it.get("archived") else
                  "active" if age is not None and age <= 90 else
                  "slowing" if age is not None and age <= 365 else "stale")
        lic = (it.get("license") or {}).get("spdx_id") or ""
        rows.append({
            "repo": full,
            "owner": owner,
            "stars": it["stargazers_count"],
            "forks": it.get("forks_count", 0),
            "open_issues": it.get("open_issues_count", 0),
            "license": "" if lic == "NOASSERTION" else lic,
            "license_detected": lic,
            "commercial": ("yes" if lic in PERMISSIVE else
                           "copyleft" if lic.startswith(("GPL", "AGPL", "LGPL")) else
                           "undeclared"),
            "created": (it.get("created_at") or "")[:10],
            "pushed": pushed,
            "days_since_push": age,
            "status": status,
            "language": it.get("language") or "",
            "topics": " ".join(it.get("topics") or []),
            "url": it["html_url"],
            "description": re.sub(r"\s+", " ", desc)[:300],
            "embodiment": "",
            "embodiment_source": "",
        })
    return rows, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-stars", type=int, default=50)
    ap.add_argument("--since", type=int, default=2015, help="first year of creation dates")
    ap.add_argument("--owner-cap", type=int, default=10)
    ap.add_argument("--out", default="gh-candidates.csv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-readme", action="store_true",
                    help="skip the per-repo README fetch (much lower embodiment coverage)")
    a = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("warning: no GITHUB_TOKEN — search is 10 req/min and this needs "
              "hundreds. Expect it to crawl.\n", file=sys.stderr)

    items, truncated = harvest(a.min_stars, a.since, token)
    rows, dropped = keep(items, a.owner_cap)

    print(f"\nunique repos found: {len(items)} | kept {len(rows)}")
    for k, v in dropped.items():
        print(f"  -{v:<6} {k}")
    if truncated:
        print(f"\n{len(truncated)} slices hit the 1,000-result cap and are "
              f"incomplete — narrow the window for these:")
        for t, a_, n in truncated[:12]:
            print(f"    {t} {a_}: {n}")

    if rows:
        import collections
        print(f"\nresolving embodiment for {len(rows)}"
              f"{' (readme fetch on)' if not a.no_readme else ' (description only)'}...",
              file=sys.stderr)
        rows = add_embodiment(rows, token, not a.no_readme)
        emb = [r for r in rows if r["embodiment"]]
        print(f"embodiment resolved: {len(emb)} of {len(rows)} "
              f"({len(emb)/len(rows)*100:.0f}%)")
        ec = collections.Counter()
        for r in emb:
            for e in r["embodiment"].split("; "):
                ec[e] += 1
        for e, n in ec.most_common(15):
            print(f"    {n:>4}  {e}")

        st = collections.Counter(r["status"] for r in rows)
        print(f"\nmaintenance: {dict(st)}")
        print(f"  stale or archived: "
              f"{(st['stale']+st['archived'])/len(rows)*100:.0f}%")
        lic = collections.Counter(r["commercial"] for r in rows)
        print(f"licence: {dict(lic)}")
        print(f"\n{'stars':>7}  {'status':<9} {'licence':<14} repo")
        for r in rows[:30]:
            print(f"{r['stars']:>7,}  {r['status']:<9} "
                  f"{(r['license'] or '-'):<14} {r['repo']}")
        if len(rows) > 30:
            print(f"{'':>7}  ... and {len(rows)-30} more")

        print(f"\nmost neglected, by last push:")
        for r in sorted([x for x in rows if x["status"] in ("stale", "archived")],
                        key=lambda x: -(x["days_since_push"] or 0))[:15]:
            print(f"  {r['status']:<9} {r['days_since_push']:>5}d  "
                  f"{r['stars']:>6,}*  {r['repo']}")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["repo"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
