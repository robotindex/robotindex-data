#!/usr/bin/env python3
"""
sweep_orgs.py — ask every organisation in the curated map what it has published.

The two harvests find things by popularity: fifty stars on GitHub, a hundred
monthly downloads on Hugging Face. Both are trailing signals, so anything
published recently is invisible, and anything that never sets a topic tag stays
invisible permanently. That is how huggingface/lerobot (27,960 stars) and
TheRobotStudio/SO-ARM100 (6,800 stars, the arm behind 64 of the datasets we
index) ended up outside an index that already tracks their hardware.

This asks a different question. Instead of "what is popular", it asks "what have
these 169 named organisations published" — one call each, no star floor, no
download floor, no topic requirement. A release shows up the day it appears.

It does three things in one pass:

  1. Verifies the handles. 115 of the 169 were written from memory and have
     never been confirmed. A handle that does not exist returns nothing, so the
     sweep settles them as a side effect rather than as separate work.
  2. Lists what each organisation owns, newest first, and marks what is already
     in the index.
  3. Writes a per-organisation summary — repositories, stars, datasets,
     downloads, last activity — which is the input to any ranking.

    python scripts/sweep_orgs.py
    python scripts/sweep_orgs.py --since 2026-01-01 --out sweep.csv
    python scripts/sweep_orgs.py --verify-only

About 340 calls for 169 organisations across both platforms, well inside
GitHub's 5,000/hour. Hugging Face needs no key for public listing.
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

GH_API = "https://api.github.com"
HF_API = "https://huggingface.co/api/datasets"
MAP_FILE = os.path.join("data", "watched-orgs-map.json")

# Same filters the harvests use, so a watched organisation's tutorial repo or
# test upload does not get through just because of who owns it.
JUNK_NAME = re.compile(
    r"(^|[_-])(test|tests|demo|tutorial|tutorials|example|examples|homework|"
    r"assignment|coursework|practice|playground|sandbox|template|boilerplate|"
    r"starter|hello[_-]?world|my[_-]?\w+|learning[_-]|study|notes|cheatsheet|"
    r"interview|roadmap|awesome|dotfiles|\.github)([_-]|\d*$)", re.I)

SHARD = re.compile(r"(^|[_-])(part|shard|chunk|split|slice|batch|seg|episode)"
                   r"[_-]?\d+$|[_-]\d{4,}$", re.I)

# Listing by owner bypasses the topic and tag filters the harvests rely on, so a
# watched organisation's unrelated work arrives with everything else: a first
# Hugging Face sweep returned NVIDIA's maths proofs and Voxel51's agricultural
# vision sets. Being a robotics publisher does not make every upload robotics.
# The filter is deliberately looser here than in the open harvest. There, a
# robotics word has to carry the whole judgement about an unknown repository.
# Here the organisation has already been vouched for by hand, so the filter only
# needs to separate its robotics work from its unrelated work — and a false
# negative costs more than a false positive. Note the leading \b is dropped:
# "TheRobotStudio" has no word boundary before "Robot".
ROBOTICS = re.compile(
    r"(robot|robo[a-z]*|manipul|grasp|teleop|lerobot|so10?[01]|so.arm|franka|unitree|"
    r"aloha|widowx|agilex|piper|kuka|kinova|xarm|galaxea|agibot|reachy|turtlebot|"
    r"crazyflie|gripper|humanoid|quadruped|legged|locomot|drone|uav\b|rover|"
    r"embodied|urdf|xacro|mjcf|sim2real|sim.to.real|vla\b|rlds\b|libero|mujoco|"
    r"isaac|gazebo|pybullet|robocasa|robotwin|bigym|calvin\b|bridgedata|droid\b|"
    r"open.x.embodiment|oxe\b|moveit|nav2|ros2?\b|rclpy|rclcpp|dds\b|zenoh|"
    r"policy|trajector|demonstration|proprio|end.effector|dexterous|bimanual|"
    r"tactile|pick.and.place|navigation|slam\b|point.cloud|lidar|odometry|"
    r"kinematic|actuator|servo|motion.capture|retarget|\barm\b|\barms\b|gello|dexterity|whole.body|end.?effector|hand\b|mocap)", re.I)


def gh_get(url, token, tries=3):
    headers = {"User-Agent": "robotindex-sweep",
               "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "no such account"
            if e.code in (403, 429):
                if e.headers.get("X-RateLimit-Remaining") == "0":
                    reset = int(e.headers.get("X-RateLimit-Reset", 0))
                    wait = max(5, reset - int(time.time()) + 2)
                    print(f"    rate limited, waiting {wait}s", file=sys.stderr)
                    time.sleep(min(wait, 900))
                    continue
                return None, "forbidden"
            if attempt == tries - 1:
                return None, f"http {e.code}"
        except Exception as e:
            if attempt == tries - 1:
                return None, str(e)[:40]
        time.sleep(2 * (attempt + 1))
    return None, "failed"


def hf_get(url, token, tries=3):
    headers = {"User-Agent": "robotindex-sweep"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return [], "no such account"
            if e.code == 429 and attempt < tries - 1:
                time.sleep(int(e.headers.get("Retry-After") or 20))
                continue
            if attempt == tries - 1:
                return None, f"http {e.code}"
        except Exception as e:
            if attempt == tries - 1:
                return None, str(e)[:40]
        time.sleep(2 * (attempt + 1))
    return None, "failed"


def known():
    """Everything already harvested, so the sweep reports only the gap."""
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


def gh_profile(handle, token):
    """The account's own profile: homepage, display name, description, location.

    One extra call per organisation. Worth it because the homepage is the one
    field the map cannot sensibly hold by hand — 169 URLs written from memory
    would be mostly wrong and would go stale, whereas GitHub holds whatever the
    organisation itself set and keeps it current.
    """
    for kind in ("orgs", "users"):
        d, err = gh_get(f"{GH_API}/{kind}/{handle}", token)
        if err == "no such account":
            continue
        if err or not d:
            return {}
        site = (d.get("blog") or "").strip()
        if site and not site.startswith(("http://", "https://")):
            site = "https://" + site
        return {
            "website": site,
            "display_name": d.get("name") or "",
            "bio": re.sub(r"\s+", " ", d.get("description") or d.get("bio") or "")[:200],
            "location": d.get("location") or "",
            "public_repos": d.get("public_repos", 0),
            "followers": d.get("followers", 0),
            "account_created": (d.get("created_at") or "")[:10],
        }
    return {}


def list_github(handle, token, since, pages=4):
    """Every public repository an account owns, newest first. Tries the org
    endpoint then the user one: several of the most interesting publishers are
    individuals, not organisations (CALVIN is under `mees`, LESS under
    `zoharri`)."""
    for kind in ("orgs", "users"):
        out, page = [], 1
        while page <= pages:
            url = (f"{GH_API}/{kind}/{handle}/repos?type=public&sort=created"
                   f"&direction=desc&per_page=100&page={page}")
            batch, err = gh_get(url, token)
            if err == "no such account":
                break               # wrong endpoint — try the other
            if err or batch is None:
                return out, err
            if not batch:
                return out, None
            stop = False
            for it in batch:
                if since and (it.get("created_at") or "")[:10] < since:
                    stop = True
                    break
                out.append(it)
            if stop or len(batch) < 100:
                return out, None
            page += 1
            time.sleep(0.2)
        if out:
            return out, None
    return [], "no such account"


def list_hf(handle, token, since, pages=3):
    out = []
    for page in range(pages):
        url = (f"{HF_API}?author={urllib.parse.quote(handle)}"
               f"&sort=createdAt&direction=-1&limit=100&skip={page * 100}&full=true")
        batch, err = hf_get(url, token)
        if err:
            return out, err
        if not batch:
            return out, None
        stop = False
        for d in batch:
            if since and (d.get("createdAt") or "")[:10] < since:
                stop = True
                break
            out.append(d)
        if stop or len(batch) < 100:
            return out, None
        time.sleep(0.2)
    return out, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=MAP_FILE)
    ap.add_argument("--out", default="sweep-new.csv")
    ap.add_argument("--summary-out", default="sweep-orgs.csv")
    ap.add_argument("--since", default=None,
                    help="only items created on or after this date (default: 180 days ago). "
                         "Pass 2000-01-01 for everything an organisation has ever published.")
    ap.add_argument("--verify-only", action="store_true",
                    help="only check which handles exist; no listing, no output file")
    ap.add_argument("--no-robotics-filter", action="store_true",
                    help="keep everything a watched account published, including its "
                         "unrelated work. Off by default: NVIDIA publishes maths proofs "
                         "and Voxel51 publishes agricultural imagery.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        m = json.load(open(a.map, encoding="utf-8"))
    except Exception as e:
        print(f"FATAL: could not read {a.map}: {e}", file=sys.stderr)
        return 1

    orgs = []
    for cat, block in m["categories"].items():
        for o in block["orgs"]:
            orgs.append({**o, "category": cat})

    since = a.since or (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
    gh_token = os.environ.get("GITHUB_TOKEN")
    hf_token = os.environ.get("HF_TOKEN")
    known_repos, known_datasets = known()
    today = datetime.date.today()

    print(f"{len(orgs)} organisations | items created since {since}\n"
          f"{len(known_repos)} repos and {len(known_datasets)} datasets already known\n",
          file=sys.stderr)

    new_items, summaries = [], []
    verified = {"github": 0, "huggingface": 0}
    bad_handles = []

    for i, o in enumerate(orgs, 1):
        s = {"name": o["name"], "category": o["category"],
             "github": o["github"] or "", "huggingface": o["huggingface"] or "",
             "gh_exists": "", "hf_exists": "",
             "repos": 0, "stars": 0, "repos_new": 0, "repos_known": 0,
             "datasets": 0, "downloads": 0, "datasets_new": 0, "datasets_known": 0,
             "website": "", "display_name": "", "location": "",
             "followers": 0, "public_repos": 0,
             "last_activity": "", "note": o.get("note", "")[:200]}

        if o["github"]:
            items, err = list_github(o["github"], gh_token, since)
            s["gh_exists"] = "no" if err == "no such account" else ("yes" if not err else err)
            if err == "no such account":
                bad_handles.append((o["name"], "github", o["github"]))
            else:
                verified["github"] += 1
                prof = gh_profile(o["github"], gh_token)
                s["website"] = prof.get("website", "")
                s["display_name"] = prof.get("display_name", "")
                s["location"] = prof.get("location", "")
                s["followers"] = prof.get("followers", 0)
                s["public_repos"] = prof.get("public_repos", 0)
                if prof.get("bio") and not s["note"]:
                    s["note"] = prof["bio"]
                for it in items:
                    full = it.get("full_name", "")
                    if not full or it.get("fork"):
                        continue
                    name = full.split("/", 1)[-1]
                    if JUNK_NAME.search(name):
                        continue
                    desc = it.get("description") or ""
                    if not a.no_robotics_filter and not ROBOTICS.search(full + " " + desc):
                        continue
                    s["repos"] += 1
                    s["stars"] += it.get("stargazers_count", 0)
                    pushed = (it.get("pushed_at") or "")[:10]
                    if pushed > s["last_activity"]:
                        s["last_activity"] = pushed
                    if full.lower() in known_repos:
                        s["repos_known"] += 1
                        continue
                    s["repos_new"] += 1
                    lic = (it.get("license") or {}).get("spdx_id") or ""
                    new_items.append({
                        "kind": "github", "org": o["name"], "category": o["category"],
                        "ref": full, "stars": it.get("stargazers_count", 0),
                        "downloads": "", "licence": "" if lic == "NOASSERTION" else lic,
                        "created": (it.get("created_at") or "")[:10], "updated": pushed,
                        "language": it.get("language") or "",
                        "topics": " ".join(it.get("topics") or []),
                        "url": it.get("html_url", ""),
                        "description": re.sub(r"\s+", " ", desc)[:250],
                    })

        if o["huggingface"]:
            items, err = list_hf(o["huggingface"], hf_token, since)
            s["hf_exists"] = "no" if err == "no such account" else ("yes" if not err else err)
            if err == "no such account":
                bad_handles.append((o["name"], "huggingface", o["huggingface"]))
            elif not err:
                verified["huggingface"] += 1
                for d in items:
                    rid = d.get("id") or ""
                    base = rid.split("/", 1)[-1]
                    if not rid or JUNK_NAME.search(base) or SHARD.search(base):
                        continue
                    card = d.get("cardData") or {}
                    desc = (card.get("description") or d.get("description") or "")
                    tags = d.get("tags") or []
                    if not a.no_robotics_filter and not ROBOTICS.search(
                            rid + " " + desc + " " + " ".join(tags)):
                        continue
                    s["datasets"] += 1
                    s["downloads"] += d.get("downloads", 0) or 0
                    mod = (d.get("lastModified") or "")[:10]
                    if mod > s["last_activity"]:
                        s["last_activity"] = mod
                    if rid.lower() in known_datasets:
                        s["datasets_known"] += 1
                        continue
                    s["datasets_new"] += 1
                    lic = card.get("license") or ""
                    if isinstance(lic, list):
                        lic = ", ".join(lic)
                    new_items.append({
                        "kind": "huggingface", "org": o["name"], "category": o["category"],
                        "ref": rid, "stars": d.get("likes", 0),
                        "downloads": d.get("downloads", 0) or 0, "licence": lic,
                        "created": (d.get("createdAt") or "")[:10], "updated": mod,
                        "language": "", "topics": " ".join(
                            t for t in tags if not t.startswith(
                                ("license:", "region:", "size_categories:"))),
                        "url": f"https://huggingface.co/datasets/{rid}",
                        "description": re.sub(r"\s+", " ", desc)[:250],
                    })

        if s["last_activity"]:
            try:
                s["days_since_activity"] = (
                    today - datetime.date.fromisoformat(s["last_activity"])).days
            except Exception:
                s["days_since_activity"] = ""
        else:
            s["days_since_activity"] = ""
        summaries.append(s)
        if i % 20 == 0:
            print(f"  [{i}/{len(orgs)}] {len(new_items)} new items so far",
                  file=sys.stderr)
        time.sleep(0.1)

    # ------------------------------------------------------------------ report
    print(f"\n{len(orgs)} organisations swept")
    print(f"  {verified['github']} GitHub handles resolve, "
          f"{sum(1 for x in bad_handles if x[1] == 'github')} do not")
    print(f"  {verified['huggingface']} Hugging Face handles resolve, "
          f"{sum(1 for x in bad_handles if x[1] == 'huggingface')} do not")

    if bad_handles:
        print(f"\n{len(bad_handles)} handles that do not exist — correct these in the map:")
        for name, plat, h in bad_handles:
            print(f"  {plat:<12} {h:<32} {name}")

    gh_new = [x for x in new_items if x["kind"] == "github"]
    hf_new = [x for x in new_items if x["kind"] == "huggingface"]
    print(f"\n{len(new_items)} items not already in the index")
    print(f"  {len(gh_new)} repositories")
    print(f"  {len(hf_new)} datasets")

    if gh_new:
        over = sum(1 for x in gh_new if x["stars"] >= 50)
        print(f"\n  {over} repositories already clear the 50-star floor — they met the "
              f"index's own bar and the search never saw them")
        print(f"\n  {'stars':>6}  {'updated':<11} repository")
        for x in sorted(gh_new, key=lambda r: -r["stars"])[:20]:
            print(f"  {x['stars']:>6}  {x['updated']:<11} {x['ref']}")

    if hf_new:
        over = sum(1 for x in hf_new if (x["downloads"] or 0) >= 100)
        print(f"\n  {over} datasets already clear the 100-download floor")
        print(f"\n  {'downloads':>9}  {'updated':<11} dataset")
        for x in sorted(hf_new, key=lambda r: -(r["downloads"] or 0))[:15]:
            print(f"  {x['downloads']:>9,}  {x['updated']:<11} {x['ref']}")

    live = [s for s in summaries if s["repos"] or s["datasets"]]
    silent = [s for s in summaries if not s["repos"] and not s["datasets"]]
    print(f"\n{len(live)} organisations publish something; {len(silent)} publish nothing "
          f"we can see")
    if silent:
        print("  " + ", ".join(s["name"] for s in silent[:18]) +
              (f" and {len(silent) - 18} more" if len(silent) > 18 else ""))

    if a.dry_run or a.verify_only:
        print("\n(nothing written)", file=sys.stderr)
        return 0

    if new_items:
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(new_items[0].keys()))
            w.writeheader()
            w.writerows(new_items)
        print(f"\nnew items written to {a.out}")

    cols = ["name", "display_name", "category", "website", "location",
            "github", "huggingface", "gh_exists", "hf_exists", "followers", "public_repos",
            "repos", "stars", "repos_new", "repos_known",
            "datasets", "downloads", "datasets_new", "datasets_known",
            "last_activity", "days_since_activity", "note"]
    with open(a.summary_out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(summaries)
    print(f"per-organisation summary written to {a.summary_out}")

    # Fold the homepages back into the map. GitHub holds what each organisation
    # set itself, so the map stays current without anyone maintaining a URL list.
    found = {s["name"]: s["website"] for s in summaries if s.get("website")}
    if found:
        changed = 0
        for block in m["categories"].values():
            for o in block["orgs"]:
                w = found.get(o["name"])
                if w and o.get("website") != w:
                    o["website"] = w
                    changed += 1
        if changed:
            with open(a.map, "w", encoding="utf-8") as fh:
                json.dump(m, fh, indent=2, ensure_ascii=False)
            print(f"{changed} homepages written back into {a.map}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
