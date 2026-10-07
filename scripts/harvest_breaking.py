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
# Reddit closed the public JSON API to unauthenticated clients in 2023 and a
# first run of this script got nothing back from it at all — every row came
# from Hacker News and the failure was silent, because the error went to stderr
# while the log captured only stdout. The RSS feed is still open and needs no
# key, so that is what this uses. If a REDDIT_TOKEN is set, the JSON API is
# tried first because it carries scores and comment counts that RSS does not.
REDDIT_JSON = "https://oauth.reddit.com/r/{sub}/new"
REDDIT_RSS = "https://www.reddit.com/r/{sub}/new/.rss"
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


def get_text(url, tries=4, timeout=25):
    """Fetch a page as text rather than JSON."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ct = (r.headers.get("Content-Type") or "").lower()
                if "html" not in ct and "xml" not in ct and "text" not in ct:
                    return "", "not text"
                return r.read(400000).decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            # Reddit rate-limits the RSS feed hard from shared CI addresses: a
            # first run got r/robotics and then 429 on the other three subs,
            # because this returned on the first error instead of waiting.
            if e.code == 429 and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or 30) * (attempt + 1)
                print(f"    429, waiting {wait}s", file=sys.stderr)
                time.sleep(min(wait, 180))
                continue
            if e.code in (403, 404) or attempt == tries - 1:
                return "", f"http {e.code}"
        except Exception as e:
            if attempt == tries - 1:
                return "", str(e)[:40]
        time.sleep(3 * (attempt + 1))
    return "", "failed"


def reddit_token():
    """An application-only OAuth token, or None.

    Reddit closed the public JSON API to unauthenticated clients, and the RSS
    feed that replaced it carries no score and no comment count — so every
    Reddit post sorted equally at zero and the score floor had to be waived for
    them. With a token the listing endpoint returns both, and Reddit's limits
    are far more generous: 100 requests a minute rather than the handful the
    unauthenticated feed allows.

    Needs REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET, from a free "script" app
    registered at reddit.com/prefs/apps. Without them this returns None and the
    caller falls back to RSS, which still works and still finds things.
    """
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return None
    import base64
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token", data=data,
        headers={"Authorization": f"Basic {auth}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r).get("access_token")
    except Exception as e:
        print(f"  reddit auth failed ({str(e)[:40]}) — falling back to RSS",
              file=sys.stderr)
        return None


def scan_reddit_oauth(since_ts, token, limit=100, pages=3):
    """The listing endpoint, with scores and comment counts."""
    out = []
    for sub in SUBS:
        after, got = None, 0
        for _ in range(pages):
            url = (f"{REDDIT_JSON.format(sub=sub)}?limit={limit}"
                   + (f"&after={after}" if after else ""))
            req = urllib.request.Request(url, headers={
                "Authorization": f"bearer {token}", "User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    d = json.load(r)
            except Exception as e:
                print(f"  r/{sub}: {str(e)[:40]}", file=sys.stderr)
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
                    "created": datetime.datetime.fromtimestamp(
                        p.get("created_utc") or 0, datetime.timezone.utc).date().isoformat(),
                    "text": f"{title} {p.get('url') or ''} {(p.get('selftext') or '')[:3000]}",
                })
                got += 1
            after = (d.get("data") or {}).get("after")
            if stop or not after:
                break
            time.sleep(1.2)
        print(f"  r/{sub}: {got} posts (with scores)", file=sys.stderr)
        time.sleep(1.2)
    return out


def scan_reddit(since_ts):
    """Reddit's new-post feed. OAuth where a token is available, RSS otherwise.

    Returns the same shape as scan_hn, with score 0: RSS carries no score, so
    --min-score would silently drop everything from Reddit. These rows are
    exempted from that filter in main() instead.
    """
    tok = reddit_token()
    if tok:
        return scan_reddit_oauth(since_ts, tok)
    print("  no REDDIT_CLIENT_ID — using RSS, which carries no score or comment count",
          file=sys.stderr)
    out = []
    for sub in SUBS:
        body, err = get_text(REDDIT_RSS.format(sub=sub))
        if err or not body:
            print(f"  r/{sub}: {err or 'empty'}", file=sys.stderr)
            time.sleep(12)
            continue
        entries = re.findall(r"<entry>([\s\S]*?)</entry>", body)
        for e in entries:
            def tag(t):
                m = re.search(rf"<{t}[^>]*>([\s\S]*?)</{t}>", e)
                return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
            upd = tag("updated")[:10]
            try:
                ts = datetime.datetime.fromisoformat(
                    tag("updated").replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = since_ts
            if ts < since_ts:
                continue
            link = re.search(r'<link[^>]*href="([^"]+)"', e)
            title = re.sub(r"<[^>]+>", "", tag("title"))
            content = re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", tag("content")))
            out.append({
                "source": f"r/{sub}",
                "title": title,
                "url": link.group(1) if link else "",
                "discussion": link.group(1) if link else "",
                "score": 0,            # RSS carries none; see docstring
                "comments": 0,
                "created": upd,
                "text": f"{title} {content[:4000]}",
                "no_score": True,
            })
        print(f"  r/{sub}: {len([x for x in out if x['source'] == f'r/{sub}'])} posts",
              file=sys.stderr)
        time.sleep(12)   # Reddit needs real spacing between subreddits, not courtesy
    return out


def follow_links(posts, limit=60):
    """Read the page a post points at, when the post itself names no repository.

    A first run extracted a link from only 4 of 40 posts, because most of them
    point at an article rather than a repository — "JBR-001, an open-source 3D
    printable desktop robot" scored 133 points on Hacker News and yielded
    nothing, since the repository was named on the linked page.

    One fetch per post, most-discussed first, skipping anything that already
    carries a link and anything pointing at a platform we would be fetching
    from itself.
    """
    SKIP = re.compile(r"(news\.ycombinator|reddit\.com|youtube|youtu\.be|twitter|x\.com|"
                      r"linkedin|\.pdf$|\.mp4$|\.jpg$|\.png$)", re.I)
    todo = [p for p in posts
            if p.get("url") and not SKIP.search(p["url"])
            and not GITHUB.search(p["text"]) and not HUGGINGFACE.search(p["text"])]
    todo.sort(key=lambda p: -(p.get("score") or 0) - (p.get("comments") or 0))
    todo = todo[:limit]
    found = 0
    for i, p in enumerate(todo, 1):
        body, err = get_text(p["url"])
        if err or not body:
            continue
        # Only the links, not the whole page: a blog post can mention dozens of
        # repositories in passing, and the body text is not evidence of a release.
        hrefs = " ".join(re.findall(r'href="([^"]+)"', body)[:400])
        hits = GITHUB.findall(hrefs) or HUGGINGFACE.findall(hrefs)
        if hits:
            p["text"] += " " + hrefs
            p["link_followed"] = "yes"
            found += 1
        if i % 20 == 0:
            print(f"    followed {i}/{len(todo)}, {found} yielded links", file=sys.stderr)
        time.sleep(0.4)
    print(f"  followed {len(todo)} linked pages, {found} named a repository",
          file=sys.stderr)
    return posts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default="breaking.csv")
    ap.add_argument("--min-score", type=int, default=3,
                    help="ignore posts below this score; 3 filters the unread without "
                         "requiring a post to have gone anywhere")
    ap.add_argument("--links-only", action="store_true",
                    help="only keep posts that point at a repository or dataset")
    ap.add_argument("--no-follow", action="store_true",
                    help="do not fetch the page each post links to. Without following, "
                         "only about one post in ten names a repository in the post itself.")
    ap.add_argument("--follow-limit", type=int, default=60,
                    help="how many linked pages to fetch, most-discussed first")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=a.days)
    since_ts = int(since.timestamp())
    known_repos, known_datasets = known()
    watched = watched_orgs()
    print(f"scanning the last {a.days} days\n"
          f"{len(known_repos)} repos and {len(known_datasets)} datasets already known, "
          f"{len(watched)} accounts watched\n", file=sys.stderr)

    posts = scan_hn(since_ts) + scan_reddit(since_ts)
    print(f"  {len(posts)} posts fetched", file=sys.stderr)
    if not a.no_follow:
        posts = follow_links(posts, a.follow_limit)

    rows, dropped = [], {"not_robotics": 0, "noise": 0, "low_score": 0, "no_links": 0}
    for p in posts:
        if NOISE.search(p["title"]):
            dropped["noise"] += 1
            continue
        if not ROBOTICS.search(p["title"]):
            dropped["not_robotics"] += 1
            continue
        # Reddit rows come from RSS and have no score, so the floor would drop
        # every one of them.
        if not p.get("no_score") and p["score"] < a.min_score:
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
            "link_followed": p.get("link_followed", ""),
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
