#!/usr/bin/env python3
"""
harvest_hf.py — pull the robotics datasets on Hugging Face that are worth indexing.

The robotics tag currently returns 80,198 datasets. Most are not datasets in any
useful sense: LeRobot writes a dataset card automatically, so every person who
runs the tutorial publishes one. Three of the entries on the first page of
results are `m1b/testdrive1`, `aractingi/test` and `DavesArmoury/not_a_robot`,
each carrying the identical 135-byte card "This dataset was created using
LeRobot."

So the work here is almost entirely filtering. Four gates, each cheap:

  1. downloads  — a floor, default 500/month. The single strongest signal.
  2. card size  — the LeRobot stub is ~135-145 bytes. Anything under ~400 bytes
                  has no description, no licence and no provenance.
  3. name       — test / demo / tmp / my_dataset / record-N and similar.
  4. dedup      — same base name under many owners is usually a re-upload.

Everything that survives is written out with its licence, download count, tags
and last-modified date, ready to merge into the index.

EMBODIMENT: with --readme (default on), each surviving dataset's full README is
fetched and searched for the robot it was recorded on. The listing endpoint only
returns ~300 characters of card text, which was enough to identify a robot for
23% of them; the full README usually has a Hardware or Setup section. Every
match records HOW it was found — tag, card or readme — because a declared tag is
worth more than a phrase in prose, and a later reader needs to know which to
doubt.

    python scripts/harvest_hf.py --min-downloads 500 --out hf-candidates.csv
    python scripts/harvest_hf.py --min-downloads 100 --pages 20 --dry-run
    python scripts/harvest_hf.py --no-readme          # skip the extra fetches

No auth needed for public data. HF_TOKEN is honoured if present and raises the
rate limit.
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

API = "https://huggingface.co/api/datasets"

# Names that indicate someone was trying the tooling, not publishing data.
JUNK_NAME = re.compile(
    r"(^|[_/-])(test|tests|testing|demo|tmp|temp|debug|sample|example|scratch|trial|"
    r"try|foo|bar|untitled|new_?dataset|my_?dataset|record_?\d*|eval_?\d*|"
    r"dataset_?\d*|run_?\d*|session_?\d*|so100_?\w*|so101_?\w*|koch_?\w*)"
    r"([_/-]|\d*$)", re.I)

# The LeRobot auto-card is ~135-145 bytes. Anything this short carries no
# description, no licence and no provenance — there is nothing to index.
MIN_CARD_BYTES = 400

# Robot patterns. Each requires the manufacturer name or an unambiguous phrase.
# A looser earlier version matched "spot-the-difference" to Boston Dynamics Spot
# and "the league's g1 division archive" to a Unitree G1, so bare model numbers
# and common words are deliberately excluded.
EMBODIMENT = [
    (r"\bfranka\b|\bpanda arm\b|\bfr3\b",                       "Franka Panda"),
    (r"unitree\s*g1|\bg1\b[^.]{0,30}(humanoid|robot|arm|loco)", "Unitree G1"),
    (r"unitree\s*h1\b|\bh1-2\b",                                "Unitree H1"),
    (r"unitree\s*h2\b",                                         "Unitree H2"),
    (r"unitree\s*go2|unitree\s*b2",                             "Unitree Go2"),
    (r"\bso-?10[01]\b",                                         "SO-100/101"),
    (r"\baloha\b",                                              "ALOHA"),
    (r"\bwidowx\b",                                             "WidowX"),
    (r"\bur5e?\b|universal robots",                             "UR5"),
    (r"\bxarm\b|ufactory",                                      "xArm"),
    (r"\bkuka\b|\biiwa\b",                                      "Kuka"),
    (r"\bsawyer\b|rethink robotics",                            "Sawyer"),
    (r"\bjaco\b|kinova",                                        "Kinova Jaco"),
    (r"hello robot|\bstretch (robot|re1|re2|3)\b",              "Hello Robot Stretch"),
    (r"agilex|\bpiper arm\b|\bcobot magic\b",                   "AgileX PiPER"),
    (r"agibot|\bgenie-?1\b",                                    "AgiBot"),
    (r"\byam (arm|station|episode|pair|robot)",                 "YAM"),
    (r"fourier\s*gr-?1|\bgr-?1\b[^.]{0,20}(humanoid|robot)",    "Fourier GR-1"),
    (r"\bpr2\b",                                                "PR2"),
    (r"boston dynamics|\bspot robot\b",                         "Boston Dynamics"),
    (r"\breachy\b|pollen robotics",                             "Pollen Reachy"),
    (r"\bfetch (robot|mobile manipulator)\b",                   "Fetch"),
    (r"\btiago\b|pal robotics",                                 "PAL TIAGo"),
    (r"\bpiper\b[^.]{0,20}(arm|robot)",                         "AgileX PiPER"),
    (r"\bumi\b[^.]{0,30}(gripper|handheld|data|interface)",     "UMI (handheld)"),
    (r"\bgalaxea\b|\br1 pro\b",                                 "Galaxea"),
    (r"\bdobot\b",                                              "Dobot"),
    (r"\bmycobot\b|elephant robotics",                          "myCobot"),
    (r"\bkoch\b[^.]{0,15}(arm|robot|v1)",                       "Koch arm"),
    (r"\bviperx\b|trossen",                                     "Trossen ViperX"),
    (r"egocentric|ego-exo|head-mounted camera|project aria|"
     r"\baria glasses\b|first-person video",                    "none — human video"),
    (r"\bmocap\b|motion capture|\bvicon\b|\boptitrack\b",       "none — motion capture"),
]

# LeRobot writes a robot_type field into every dataset card's info.json block.
# It is a structured value rather than prose, so it beats pattern-matching: a
# card reading "robot_type": "h1" is unambiguous, whereas a bare "h1" in text
# could be anything. Checked before the prose patterns.
ROBOT_TYPE = re.compile(r'"robot_type"\s*:\s*"([^"]+)"', re.I)

ROBOT_TYPE_MAP = [
    (r"^h1",                                   "Unitree H1"),
    (r"^h2",                                   "Unitree H2"),
    (r"^g1",                                   "Unitree G1"),
    (r"^go2",                                  "Unitree Go2"),
    (r"panda|franka",                          "Franka Panda"),
    (r"^so[_-]?10[01]|^so_follower|^biso101|lekiwi", "SO-100/101"),
    (r"^ur5|^ur10|^ur\b|universal",            "UR5"),
    (r"piper|agilex|cobot_magic",              "AgileX PiPER"),
    (r"aloha|trossen",                         "ALOHA"),
    (r"widowx",                                "WidowX"),
    (r"viperx",                                "Trossen ViperX"),
    (r"xarm|ufactory",                         "xArm"),
    (r"galaxea",                               "Galaxea"),
    (r"mycobot|elephant",                      "myCobot"),
    (r"kinova|jaco",                           "Kinova Jaco"),
    (r"^kuka|iiwa",                            "Kuka"),
    (r"stretch",                               "Hello Robot Stretch"),
    (r"reachy",                                "Pollen Reachy"),
    (r"agibot|genie",                          "AgiBot"),
    (r"^yam",                                  "YAM"),
    (r"fourier|^gr-?1",                        "Fourier GR-1"),
]


def from_robot_type(text):
    """Pull embodiment from any robot_type fields in the card. Returns [] if none."""
    out = []
    for v in ROBOT_TYPE.findall(text or ""):
        v = v.lower().strip()
        for pat, label in ROBOT_TYPE_MAP:
            if re.search(pat, v):
                if label not in out:
                    out.append(label)
                break
    return out


# Hugging Face tags that name hardware directly. A declared tag is stronger
# evidence than a phrase in prose, so these are checked first and recorded
# separately.
TAG_MAP = {
    "unitree-h2": "Unitree H2", "unitree-h1": "Unitree H1", "unitree-g1": "Unitree G1",
    "unitree-go2": "Unitree Go2", "franka": "Franka Panda", "franka-panda": "Franka Panda",
    "so100": "SO-100/101", "so101": "SO-100/101", "aloha": "ALOHA", "widowx": "WidowX",
    "ur5": "UR5", "xarm": "xArm", "kuka": "Kuka", "jaco": "Kinova Jaco",
    "stretch": "Hello Robot Stretch", "agilex": "AgileX PiPER", "piper": "AgileX PiPER",
    "agibot": "AgiBot", "reachy": "Pollen Reachy", "mycobot": "myCobot",
    "motion-capture": "none — motion capture", "egocentric": "none — human video",
}


def fetch(url, token=None, timeout=30):
    h = {"User-Agent": "robotindex-harvest"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout))


def fetch_readme(rid, token=None, timeout=25):
    """Raw README for a dataset repo. Returns '' on any failure — a missing
    readme is a normal outcome, not an error worth stopping for."""
    url = f"https://huggingface.co/datasets/{urllib.parse.quote(rid)}/raw/main/README.md"
    h = {"User-Agent": "robotindex-harvest"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            return r.read(200_000).decode("utf-8", "replace")
    except Exception:
        return ""


def find_embodiment(tags, text):
    """Return (list of robots, how it was determined).

    Order matters: a declared robot_type or tag is evidence the publisher set
    deliberately; a phrase in prose is an inference we made.
    """
    rt = from_robot_type(text)
    if rt:
        return rt, "robot_type"

    from_tags = []
    for t in tags:
        v = TAG_MAP.get(t.lower().strip())
        if v and v not in from_tags:
            from_tags.append(v)
    if from_tags:
        return from_tags, "tag"

    low = (text or "").lower()
    found = []
    for pat, name in EMBODIMENT:
        if name not in found and re.search(pat, low):
            found.append(name)
    return found, ("readme" if found else "")


def harvest(min_downloads, pages, token, recent_downloads=10, recent_days=90):
    """Walk the robotics tag twice.

    The first pass sorts by downloads and stops at the floor. The second sorts
    by creation date and takes anything from the last `recent_days` at a much
    lower floor.

    The second exists because the first is structurally late. A dataset
    published last week has had no time to accumulate 100 downloads and is
    invisible to a download-ordered walk however good it is. Reading two years
    of robotics papers turned up 49 datasets this harvest had never seen, 32 of
    them live, and 28 of those already above the 100 floor — they qualified
    under this rule and were simply never reached.
    """
    seen, kept = {}, []
    dropped = {"downloads": 0, "stub_card": 0, "junk_name": 0, "duplicate": 0}
    basenames = {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=recent_days)).isoformat()

    passes = [("downloads", min_downloads, "most downloaded"),
              ("createdAt", recent_downloads, f"created since {cutoff}")]

    for sort_key, floor, label in passes:
        print(f"\n  pass: {label} (floor {floor})", file=sys.stderr)
        for page in range(pages):
            url = (f"{API}?filter=robotics&sort={sort_key}&direction=-1"
                   f"&limit=100&skip={page * 100}&full=true")
            try:
                batch = fetch(url, token)
            except Exception as e:
                print(f"  page {page}: {e}", file=sys.stderr)
                break
            if not batch:
                break

            below_floor = 0
            past_window = False
            for d in batch:
                rid = d.get("id") or ""
                if not rid or rid in seen:
                    continue

                created = (d.get("createdAt") or "")[:10]
                if sort_key == "createdAt" and created and created < cutoff:
                    # newest-first, so everything after this is older still
                    past_window = True
                    break

                seen[rid] = True

                dl = d.get("downloads", 0) or 0
                if dl < floor:
                    below_floor += 1
                    dropped["downloads"] += 1
                    continue

                owner, _, base = rid.partition("/")
                if JUNK_NAME.search(base):
                    dropped["junk_name"] += 1
                    continue

                card = d.get("cardData") or {}
                # `full=true` returns the parsed card. An empty or near-empty
                # one is the LeRobot stub.
                desc = (card.get("description") or d.get("description") or "").strip()
                tags = d.get("tags") or []
                if len(json.dumps(card)) < MIN_CARD_BYTES and len(desc) < 80:
                    dropped["stub_card"] += 1
                    continue

                key = base.lower().replace("-", "_")
                if key in basenames:
                    dropped["duplicate"] += 1
                    continue
                basenames[key] = rid

                lic = card.get("license")
                if isinstance(lic, list):
                    lic = ", ".join(lic)
                if not lic:
                    lic = next((t.split(":", 1)[1] for t in tags
                                if t.startswith("license:")), "")

                kept.append({
                    "id": rid,
                    "owner": owner,
                    "downloads": dl,
                    "likes": d.get("likes", 0),
                    "license": lic or "",
                    "modified": (d.get("lastModified") or "")[:10],
                    "created": created,
                    "is_lerobot": "LeRobot" in tags or "lerobot" in tags,
                    "found_by": "downloads" if sort_key == "downloads" else "recent",
                    "tags": " ".join(t for t in tags if not t.startswith(
                        ("license:", "region:", "size_categories:"))),
                    "url": f"https://huggingface.co/datasets/{rid}",
                    "description": re.sub(r"\s+", " ", desc)[:300],
                    "_tags": tags,
                })

            if past_window:
                print(f"  stopped at page {page}: past the {recent_days}-day window",
                      file=sys.stderr)
                break
            # sorted descending, so a whole page under the floor means we are done
            if below_floor == len(batch):
                print(f"  stopped at page {page}: entire page below {floor} downloads",
                      file=sys.stderr)
                break
            time.sleep(0.4)

    return kept, dropped, len(seen)


def add_embodiment(kept, token, use_readme):
    """Attach embodiment to each kept row. One extra fetch per dataset."""
    for i, k in enumerate(kept, 1):
        tags = k.pop("_tags", [])
        # Try tags and the short card text first — free, no extra request.
        emb, how = find_embodiment(tags, k["description"])
        if emb and how == "tag":
            k["embodiment"], k["embodiment_source"] = "; ".join(emb), "tag"
            continue
        if emb:
            k["embodiment"], k["embodiment_source"] = "; ".join(emb), "card"
            continue
        if not use_readme:
            k["embodiment"], k["embodiment_source"] = "", ""
            continue
        text = fetch_readme(k["id"], token)
        emb, how = find_embodiment([], text)
        k["embodiment"] = "; ".join(emb)
        k["embodiment_source"] = ("readme-robot_type" if how == "robot_type"
                                  else "readme" if emb else "")
        time.sleep(0.12)
        if i % 100 == 0:
            print(f"  ...readme {i}/{len(kept)}", file=sys.stderr)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-downloads", type=int, default=500)
    ap.add_argument("--recent-downloads", type=int, default=10,
                    help="download floor for datasets created in the last --recent-days; "
                         "a new upload has had no time to earn the main floor")
    ap.add_argument("--recent-days", type=int, default=90)
    ap.add_argument("--pages", type=int, default=30, help="100 per page")
    ap.add_argument("--out", default="hf-candidates.csv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-readme", action="store_true",
                    help="skip the per-dataset README fetch (faster, much lower embodiment coverage)")
    a = ap.parse_args()

    token = os.environ.get("HF_TOKEN")
    kept, dropped, scanned = harvest(a.min_downloads, a.pages, token, a.recent_downloads, a.recent_days)

    print(f"\nscanned {scanned} | kept {len(kept)}")
    for k, v in dropped.items():
        print(f"  -{v:<6} {k}")

    if kept:
        print(f"\nresolving embodiment for {len(kept)}"
              f"{' (readme fetch on)' if not a.no_readme else ' (tags and card only)'}...",
              file=sys.stderr)
        kept = add_embodiment(kept, token, not a.no_readme)

        no_lic = sum(1 for k in kept if not k["license"])
        lerobot = sum(1 for k in kept if k["is_lerobot"])
        emb = [k for k in kept if k["embodiment"]]
        print(f"\n  no declared licence: {no_lic} of {len(kept)} ({no_lic/len(kept)*100:.0f}%)")
        print(f"  LeRobot-format:      {lerobot}")
        print(f"  embodiment resolved: {len(emb)} of {len(kept)} ({len(emb)/len(kept)*100:.0f}%)")

        import collections
        by_src = collections.Counter(k["embodiment_source"] for k in emb)
        print(f"    by source: {dict(by_src)}")
        robots = collections.Counter()
        for k in emb:
            for r in k["embodiment"].split("; "):
                robots[r] += 1
        print("\n  most common embodiments:")
        for r, n in robots.most_common(12):
            print(f"    {n:>4}  {r}")

        print(f"\n{'downloads':>10}  {'licence':<16} {'embodiment':<22} id")
        for k in kept[:30]:
            print(f"{k['downloads']:>10,}  {(k['license'] or '-')[:16]:<16} "
                  f"{(k['embodiment'] or '-')[:22]:<22} {k['id']}")
        if len(kept) > 30:
            print(f"{'':>10}  ... and {len(kept)-30} more")

    if a.dry_run:
        print("\n(dry run — nothing written)", file=sys.stderr)
        return

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(kept[0].keys()) if kept else ["id"])
        w.writeheader()
        w.writerows(kept)
    print(f"\nwritten to {a.out}")


if __name__ == "__main__":
    main()
