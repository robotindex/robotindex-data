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
    "diffusion-policy", "world-models", "world-model",
    "physical-ai",
    # simulation, and the policy-learning engines built on top of it
    "sim-to-real", "sim2real", "isaac-sim", "isaac-lab", "isaacgym", "mujoco",
    "gazebo", "pybullet", "genesis", "maniskill", "robosuite", "robomimic",
    "robotics-simulation",
    # dataset schemas and formats — the bridge between code and the data hubs
    "rlds", "open-x-embodiment",
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
    "6d-pose-estimation", "3d-vision",
    "gaussian-splatting", "odometry", "sensor-fusion", "3d-object-detection",
    "perception", "place-recognition",
]

# Broad tags that are honey pots on their own — arduino alone returns LED
# controllers and weather stations — so they are paired with a robotics tag.
# GitHub treats multiple topic: qualifiers as AND.
QUALIFIED = [
    ("arduino", "robotics"), ("esp32", "robotics"), ("raspberry-pi", "robotics"),
    ("object-detection", "robotics"), ("pose-estimation", "robotics"),
    ("control", "robotics"), ("planning", "robotics"),
    # Moved here after an unqualified run: these brought in 4,177 repos with no
    # robotics word anywhere — Hugging Face transformers, FinGPT, React
    # Navigation, trading ML. Two are outright ambiguous: "localization" mostly
    # means i18n, and "mapping" mostly means data mapping.
    ("reinforcement-learning", "robotics"), ("deep-reinforcement-learning", "robotics"),
    ("diffusion-models", "robotics"), ("simulator", "robotics"),
    ("vision-language-model", "robotics"), ("vlm", "robotics"),
    ("localization", "robotics"), ("mapping", "robotics"),
    ("3d-reconstruction", "robotics"),
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

# Topic tags are set by authors and always leak, so a repo must also mention
# something robotic somewhere in its name, description or topics. On the first
# unqualified run this separated the set cleanly: embodiment resolved for 17% of
# what it kept and 1% of what it dropped.
ROBOTICS_WORD = re.compile(
    # Stems, so "manipul" catches manipulation and manipulator. Leading \b only —
    # a trailing one would stop every stem matching its own suffixes.
    r"\b(robot|ros2?\b|slam\b|lidar|manipul|grasp|humanoid|quadruped|biped|"
    r"legged|drone|uav\b|rover|cobot|exoskelet|teleop|urdf|kinemat|locomot|"
    r"embodied|odometr|point.?cloud|mujoco|gazebo|isaac|pybullet|gripper|"
    r"actuator|servo|end.?effector|mobile.?base|mecanum|agv\b|amr\b|vla\b|"
    r"rlds\b|sim2real|sim.to.real|physical.ai|visual.inertial|"
    r"autonomous (vehicle|driving|robot|navigation|system|flight)|"
    r"motion planning|path planning|whole.?body|self.driving|"
    r"depth camera|rgb.?d\b|imu\b|ros2|moveit|nav2)", re.I)

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
    (r"\bur[3-9]e?\b|\bur10e?\b|universal robots|\bur7e\b",      "Universal Robots"),
    (r"\bxarm\b|ufactory",                                     "xArm"),
    (r"\bkuka\b|\biiwa\b|\blbr\b",                            "KUKA"),
    (r"kinova|\bjaco\b",                                       "Kinova"),
    (r"\bsawyer\b|\bbaxter\b|rethink robotics",                "Rethink"),
    (r"turtlebot\d?",                                           "TurtleBot"),
    # "husky" is also a very popular JavaScript git-hooks package, so it needs
    # robot context. Jackal and Dingo are unambiguous.
    (r"clearpath|\bjackal\b|\bdingo\b|"
     r"\bhusky\b[^.]{0,40}(robot|ugv|rover|a200|base|platform)|"
     r"(robot|ugv|rover|mobile)[^.]{0,40}\bhusky\b",   "Clearpath"),
    (r"\bcassie\b|agility robotics",                           "Agility"),
    (r"crazyflie|bitcraze",                                     "Crazyflie"),
    (r"hello robot|stretch (re1|re2|3|robot)",                  "Hello Robot Stretch"),
    (r"agilex|cobot magic",                                     "AgileX"),
    (r"mycobot|elephant robotics",                              "myCobot"),
    (r"\breachy\b|pollen robotics",                             "Pollen Reachy"),
    # "Tiago" is a common Portuguese first name, so it needs robot context.
    # Note \b does not sit between "tiago" and "_robot" — underscore is a word
    # character — so the separator is matched explicitly.
    (r"pal[- ]robotics|"
     r"tiago[-_ ]?(robot|base|pro|ros|sim|dual|head|gripper|navigation)|"
     r"\btiago\b[^.]{0,30}(robot|mobile manipulator|humanoid)|"
     r"(robot|mobile manipulator)[^.]{0,30}\btiago\b",  "PAL TIAGo"),
    (r"agibot",                                                 "AgiBot"),
    (r"fourier|\bgr-?1\b(?=.{0,25}(humanoid|robot|arm))",        "Fourier"),
    (r"galaxea",                                                "Galaxea"),
    (r"\bpr2\b|willow garage",                                  "PR2"),
    (r"\bfetch\b(?=.{0,20}(robot|mobile))",                     "Fetch"),
    (r"\bopencat\b|petoi",                                      "Petoi"),
    (r"\bdji\b|\btello\b|robomaster",                          "DJI"),
    (r"\brobotis\b|dynamixel|\bop3\b",                         "ROBOTIS"),
    (r"\bicub\b",                                               "iCub"),
    (r"\bk-?scale\b|\bkbot\b|\bzbot\b",                       "K-Scale"),
    # Added after mining both corpora for hardware names we had no pattern for.
    # BARX looked like a robot and is a paper — Cross-Embodiment Transfer via
    # Behavior-Aligned Representations — so it is deliberately absent.
    (r"\bopenarm\b|enactic",                                    "OpenArm"),
    (r"\bflexiv\b",                                             "Flexiv"),
    (r"\blekiwi\b",                                             "LeKiwi"),
    (r"seeed[-_ ]?b601|\bseeed\b[^.]{0,20}(arm|robot|follower)", "Seeed B601"),
    (r"\baibot2\b|alphabot2",                                   "Aibot2"),
    (r"\bpiperx\b",                                             "AgileX"),
    # From checking every product in the directory against both corpora.
    (r"\bsharpa\b",                                             "Sharpa"),
    (r"\bdobot\b|x-?trainer",                                   "Dobot"),
    (r"yahboom|rosmaster|\bdofbot\b",                           "Yahboom"),
    # "xiaomi" alone matched the company's VLA and autonomous-driving work
    # (xiaomi-research/onevl, XiaomiMiMo/MiMo-Embodied, unidrivevla), none of
    # which is a CyberDog. Same failure as bare "husky" and bare "tiago":
    # a name that is a company, a product and a common word at once.
    (r"cyberdog",                                               "Xiaomi CyberDog"),
    (r"hiwonder|mentorpi|puppypi",                              "Hiwonder"),
    (r"\bumi\b[^.]{0,30}(gripper|handheld|interface|data)|"
     r"universal manipulation interface",                       "UMI (handheld)"),
    (r"\bzeroth\b",                                             "K-Scale"),
    (r"interbotix",                                             "ALOHA / Trossen"),
    # Boston Dynamics is deliberately absent. Of 30 matches only one was
    # genuinely theirs, a Spot ROS driver; the rest cited Spot as inspiration
    # for an open quadruped. "ROS driver for Boston Dynamics Spot" and
    # "inspired by Boston Dynamics Spot" are the same string to a regex, and a
    # pattern that keeps the first keeps the second. One real repository is not
    # worth twenty-nine wrong ones. ANYmal is ANYbotics, so it stands alone.
    (r"\banymal\b",                                              "ANYbotics ANYmal"),
    (r"\bduckiebot\b",                                          "Duckiebot"),
    (r"allegro hand|\bleap hand\b|shadow (dexterous )?hand",     "dexterous hand"),
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


# Repos whose purpose is cataloguing robots mention all of them. Attributing
# such a repo to any single robot is wrong — mujoco_menagerie and
# robot_descriptions.py each list 190+ models.
COLLECTION = re.compile(
    r"\b(collection of|catalog(ue)?|menagerie|model zoo|robot zoo|"
    r"descriptions? (of|for) (robot|many)|[0-9]{2,}\+? robot|"
    r"list of robot|index of robot|awesome)", re.I)


def add_embodiment(rows, token, use_readme):
    """Description and topics first — free. README only where those fail."""
    for i, r in enumerate(rows, 1):
        if COLLECTION.search(r["description"]):
            r["embodiment"], r["embodiment_source"] = "", "skipped-collection"
            continue
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


def harvest(min_stars, since_year, token, recent_stars=10, recent_days=90):
    """Walk the topics twice.

    The main pass uses the full star floor across every quarter since
    `since_year`. A second pass covers only the last `recent_days` at the much
    lower `recent_stars` floor, because a repository published last week has
    had no time to earn 50 stars and is invisible to the main pass however
    significant it is. Pantheon's Argus annotator, released two days before the
    first run of this script and audited nine of the largest robotics datasets,
    was missed for exactly that reason.
    """
    today = datetime.date.today()
    slices = quarters(since_year, today)
    recent_from = (today - datetime.timedelta(days=recent_days)).isoformat()
    seen = {}
    truncated = []
    nq = (len(TOPICS) + len(QUALIFIED)) * (len(slices) + 1)
    print(f"{len(TOPICS)} topics + {len(QUALIFIED)} qualified pairs, "
          f"x {len(slices)} quarters plus one recent pass = {nq} queries\n",
          file=sys.stderr)

    queries = [(t, f"topic:{t}") for t in TOPICS] + \
              [(f"{a}+{b}", f"topic:{a} topic:{b}") for a, b in QUALIFIED]

    for ti, (label, clause) in enumerate(queries, 1):
        got = 0
        # (window, floor): every quarter at the normal floor, then the last
        # `recent_days` at the lower one
        windows = [(a, b, min_stars) for a, b in slices]
        windows.append((recent_from, today.isoformat(), recent_stars))
        for a, b, floor in windows:
            q = f"{clause} created:{a}..{b} stars:>={floor} fork:false"
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


# ---------------------------------------------------------------- manifest gate
#
# Everything above decides what a repository IS from what it SAYS: its name, its
# description, its topics. That is lexical, and it fails in both directions —
# topic:robotics returns a web scraper, while a driver whose description reads
# "control software for our arm" has no robotics word in it at all.
#
# These patterns ask a different question: what does the repository CONTAIN. A
# robot description file, a ROS build manifest, or a dependency on a simulator
# is structural evidence that no amount of prose can fake. NVIDIA's maths proofs
# have no URDF; Voxel51's agricultural datasets do not depend on robosuite.
#
# It costs one API call per repository, so it is used selectively: to rescue
# repositories the lexical filter rejected, and to let a repository past the
# per-owner cap. It is not run over everything.

ROBOT_ASSET = re.compile(
    r"\.(urdf|xacro|mjcf|sdf|usd|usda)$|"
    r"(^|/)(package\.xml|scene\.xml|robot\.xml|CATKIN_IGNORE|COLCON_IGNORE)$|"
    r"(^|/)(urdf|xacro|meshes|mjcf|mujoco|launch|rviz|moveit_config|"
    r"robot_description|config/joint)/", re.I)

DEP_FINGERPRINT = re.compile(
    r"\b(lerobot|rlds|robomimic|robosuite|isaacgym|isaaclab|isaac-sim|mujoco|"
    r"dm-control|genesis-world|pinocchio|pybullet|rerun-sdk|roboticstoolbox|"
    r"ros2?-|rclpy|rclcpp|moveit|nav2|open3d|pytransform3d|urdfpy|yourdfpy|"
    r"placo|crocoddyl|ocs2|drake|sapien|habitat-sim|gymnasium-robotics)\b", re.I)

DEP_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "environment.yml",
             "package.xml", "CMakeLists.txt", "Cargo.toml")


def repo_tree(full, branch, token, timeout=20):
    """Every path in a repository, in one call. Returns [] on any failure, so a
    missing tree is treated as no evidence rather than as an error."""
    url = f"https://api.github.com/repos/{full}/git/trees/{branch}?recursive=1"
    try:
        d = gh(url, token, timeout=timeout, tries=2)
    except Exception:
        return []
    if not isinstance(d, dict):
        return []
    return [t.get("path", "") for t in (d.get("tree") or [])]


def manifest_evidence(full, branch, token, read_deps=True):
    """Does this repository contain robotics assets?

    Returns (bool, reason). Checks paths first because that is free once the
    tree is fetched, and only reads dependency files if the paths say nothing.
    """
    paths = repo_tree(full, branch or "main", token)
    if not paths:
        paths = repo_tree(full, "master", token)
    if not paths:
        return False, ""

    for pth in paths:
        if ROBOT_ASSET.search(pth):
            return True, f"asset:{pth.rsplit('/', 1)[-1][:40]}"

    if not read_deps:
        return False, ""

    # A dependency on a simulator or a robotics middleware is the next
    # strongest signal. Only the root copies, to keep this to one extra call.
    for name in DEP_FILES:
        if name not in paths and f"./{name}" not in paths:
            continue
        raw = f"https://raw.githubusercontent.com/{full}/{branch or 'main'}/{name}"
        try:
            req = urllib.request.Request(raw, headers={"User-Agent": "robotindex-gh"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read(40000).decode("utf-8", "replace")
        except Exception:
            continue
        m = DEP_FINGERPRINT.search(body)
        if m:
            return True, f"dep:{m.group(0).lower()}"
    return False, ""


def keep(items, owner_cap):
    """Filter the harvested items. Repositories rejected for lacking a robotics
    word, or for exceeding the per-owner cap, are returned separately so a
    manifest check can rescue the ones that contain robot assets — see
    rescue() and the note above manifest_evidence()."""
    rows, dropped = [], {"junk_name": 0, "not_robotics": 0, "bookish": 0,
                         "no_robotics_word": 0, "owner_cap": 0}
    rescuable = {"no_robotics_word": [], "owner_cap": []}
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
        # Deliberately NOT checking topics here: the topic tag is what returned
        # the repo, so re-reading it is circular. cs-video-courses, wechaty and
        # kornia all carry topic:robotics and are not robotics.
        if not ROBOTICS_WORD.search(desc + " " + full):
            dropped["no_robotics_word"] += 1
            rescuable["no_robotics_word"].append((full, it))
            continue
        if by_owner.get(owner, 0) >= owner_cap:
            dropped["owner_cap"] += 1
            rescuable["owner_cap"].append((full, it))
            continue
        by_owner[owner] = by_owner.get(owner, 0) + 1

        pushed = (it.get("pushed_at") or "")[:10]
        age = (today - datetime.date.fromisoformat(pushed)).days if pushed else None
        status = ("archived" if it.get("archived") else
                  "active" if age is not None and age <= 90 else
                  "slowing" if age is not None and age <= 365 else "inactive")
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
    return rows, dropped, rescuable


def rescue(rescuable, token, limit_word=400, limit_cap=0, read_deps=True):
    """Re-admit repositories that the lexical filter or the owner cap rejected
    but that contain robot assets.

    Two different failures, rescued for different reasons.

    The cap is the clearer case. It exists so one prolific publisher cannot
    dominate a leaderboard, but it was discarding real work: leggedrobotics is
    at the cap, and hoi-retarget — 127 stars, carrying topic:robotics — was
    dropped by it. A repository holding a URDF or depending on a simulator is
    exactly what the index is for, so it should not be lost to a display rule.
    Every capped repository is checked by default.

    The word filter is noisier, so only the most-starred rejects are checked.
    The filter is right far more often than it is wrong — it removed 4,177
    entries with no robot, arm, drone or sensor in them — but a first-party
    driver whose description reads "control software for our arm" has no
    robotics word in it either.

    Returns (rescued_rows, stats). Each rescued row carries found_by so the
    provenance survives into the index.
    """
    out, stats = [], {"checked": 0, "rescued_cap": 0, "rescued_word": 0,
                      "calls": 0, "by_reason": {}}
    today = datetime.date.today()

    def admit(full, it, why, how):
        owner, _, _ = full.partition("/")
        pushed = (it.get("pushed_at") or "")[:10]
        age = (today - datetime.date.fromisoformat(pushed)).days if pushed else None
        lic = (it.get("license") or {}).get("spdx_id") or ""
        desc = it.get("description") or ""
        return {
            "repo": full, "owner": owner, "stars": it["stargazers_count"],
            "forks": it.get("forks_count", 0),
            "open_issues": it.get("open_issues_count", 0),
            "license": "" if lic == "NOASSERTION" else lic,
            "license_detected": lic,
            "commercial": ("yes" if lic in PERMISSIVE else
                           "copyleft" if lic.startswith(("GPL", "AGPL", "LGPL")) else
                           "undeclared"),
            "created": (it.get("created_at") or "")[:10], "pushed": pushed,
            "days_since_push": age,
            "status": ("archived" if it.get("archived") else
                       "active" if age is not None and age <= 90 else
                       "slowing" if age is not None and age <= 365 else "inactive"),
            "language": it.get("language") or "",
            "topics": " ".join(it.get("topics") or []),
            "url": it["html_url"],
            "description": re.sub(r"\s+", " ", desc)[:300],
            "embodiment": "", "embodiment_source": "",
            "found_by": f"manifest:{how}", "manifest_evidence": why,
        }

    # capped repositories: the cap is a display rule, not a judgement about worth
    capped = rescuable.get("owner_cap", [])
    if limit_cap:
        capped = sorted(capped, key=lambda kv: -kv[1]["stargazers_count"])[:limit_cap]
    for full, it in capped:
        stats["checked"] += 1
        stats["calls"] += 1
        ok, why = manifest_evidence(full, it.get("default_branch"), token, read_deps)
        if ok:
            out.append(admit(full, it, why, "cap"))
            stats["rescued_cap"] += 1
            stats["by_reason"][why.split(":")[0]] = \
                stats["by_reason"].get(why.split(":")[0], 0) + 1
        time.sleep(0.05)

    # word-filter rejects: only the most-starred, since the filter is usually right
    words = sorted(rescuable.get("no_robotics_word", []),
                   key=lambda kv: -kv[1]["stargazers_count"])[:limit_word]
    for full, it in words:
        stats["checked"] += 1
        stats["calls"] += 1
        ok, why = manifest_evidence(full, it.get("default_branch"), token, read_deps)
        if ok:
            out.append(admit(full, it, why, "word"))
            stats["rescued_word"] += 1
            stats["by_reason"][why.split(":")[0]] = \
                stats["by_reason"].get(why.split(":")[0], 0) + 1
        time.sleep(0.05)

    return out, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-stars", type=int, default=50)
    ap.add_argument("--recent-stars", type=int, default=10,
                    help="star floor for repos created in the last --recent-days; a new "
                         "repository has had no time to earn the main floor")
    ap.add_argument("--recent-days", type=int, default=90)
    ap.add_argument("--since", type=int, default=2015, help="first year of creation dates")
    ap.add_argument("--owner-cap", type=int, default=10)
    ap.add_argument("--out", default="gh-candidates.csv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-readme", action="store_true",
                    help="skip the per-repo README fetch (much lower embodiment coverage)")
    ap.add_argument("--no-manifest", action="store_true",
                    help="skip the manifest rescue. Without it, repositories holding a "
                         "URDF or depending on a simulator stay dropped because their "
                         "description happens to use no robotics word, and anything over "
                         "the owner cap is lost regardless of what it contains.")
    ap.add_argument("--manifest-word-limit", type=int, default=400,
                    help="how many of the most-starred word-filter rejects to check")
    ap.add_argument("--manifest-cap-limit", type=int, default=0,
                    help="how many capped repos to check; 0 means all of them")
    a = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("warning: no GITHUB_TOKEN — search is 10 req/min and this needs "
              "hundreds. Expect it to crawl.\n", file=sys.stderr)

    items, truncated = harvest(a.min_stars, a.since, token,
                               a.recent_stars, a.recent_days)
    rows, dropped, rescuable = keep(items, a.owner_cap)

    print(f"\nunique repos found: {len(items)} | kept {len(rows)}")
    for k, v in dropped.items():
        print(f"  -{v:<6} {k}")

    for r in rows:
        r.setdefault("found_by", "search")
        r.setdefault("manifest_evidence", "")

    if not a.no_manifest:
        n_cap = len(rescuable["owner_cap"])
        n_word = min(a.manifest_word_limit, len(rescuable["no_robotics_word"]))
        print(f"\nmanifest check on {n_cap} capped and {n_word} word-filtered repos",
              file=sys.stderr)
        extra, mstats = rescue(rescuable, token,
                               a.manifest_word_limit, a.manifest_cap_limit)
        rows.extend(extra)
        print(f"\nmanifest rescue: {len(extra)} re-admitted from "
              f"{mstats['checked']} checked")
        print(f"  {mstats['rescued_cap']:<5} over the owner cap but holding robot assets")
        print(f"  {mstats['rescued_word']:<5} no robotics word in the description, "
              f"but robot assets present")
        if mstats["by_reason"]:
            for k, v in sorted(mstats["by_reason"].items(), key=lambda kv: -kv[1]):
                print(f"      {v:>4}  evidence from {k}")
        if extra:
            print(f"\n  {'stars':>6}  {'evidence':<22} repo")
            for r in sorted(extra, key=lambda x: -x["stars"])[:15]:
                print(f"  {r['stars']:>6}  {r['manifest_evidence'][:22]:<22} {r['repo']}")
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
        print(f"  inactive or archived: "
              f"{(st['inactive']+st['archived'])/len(rows)*100:.0f}%")
        lic = collections.Counter(r["commercial"] for r in rows)
        print(f"licence: {dict(lic)}")
        print(f"\n{'stars':>7}  {'status':<9} {'licence':<14} repo")
        for r in rows[:30]:
            print(f"{r['stars']:>7,}  {r['status']:<9} "
                  f"{(r['license'] or '-'):<14} {r['repo']}")
        if len(rows) > 30:
            print(f"{'':>7}  ... and {len(rows)-30} more")

        print(f"\nmost neglected, by last push:")
        for r in sorted([x for x in rows if x["status"] in ("inactive", "archived")],
                        key=lambda x: -(x["days_since_push"] or 0))[:15]:
            print(f"  {r['status']:<9} {r['days_since_push']:>5}d  "
                  f"{r['stars']:>6,}*  {r['repo']}")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return

    # Columns from the union of every row, not from the first one: rescued rows
    # carry found_by and manifest_evidence, and taking the shape from row zero
    # would silently drop whichever kind happened not to be first.
    cols, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols or ["repo"], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
