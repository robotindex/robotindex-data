#!/usr/bin/env python3
"""
Re-extract data/directory.json and every data/products/<id>.json from the
live robotindex.io site (directory.html + every product-*.html page it
links to), and overwrite the files in this checkout with the result.

This is the automated version of the extraction that was originally done
by hand: it parses the same DIRECTORY array out of directory.html's inline
JavaScript, walks every item's product page, and pulls specs / pricing /
editorial / firmware-community / sourcing the same way. It's meant to be
run in CI (see .github/workflows/sync-products.yml), which then diffs the
result against what's committed and opens a PR if anything changed -- a
human reviews the diff before it's merged, the same way the
builds-mods.json / models-data.json sync works.

Known limitations (also documented in the repo README under "Known
limitations of the Directory data"):
  - manufacturer is inferred from prose / domain heuristics. A hand-built
    OVERRIDES table below corrects every case already found to need it;
    a genuinely NEW product with an ambiguous manufacturer will show up
    as a low-confidence guess in this script's printed output, for a
    human to check in the PR diff.
  - image data is not extracted (the live pages embed photos as inline
    base64, not as files at a path the schema's image.src field expects).
  - the three multi-model family pages (chasing-family, dji-neo-family,
    hoverair-x1-family) use a comparison table instead of the usual spec
    grid; their specs are flattened into "Model A: value; Model B: value"
    strings, same as the original manual extraction.
  - a directory item whose page 404s (a "pending" product not yet live)
    is skipped with a warning rather than failing the whole run -- its
    existing record, if any, is left untouched.
  - a product record that disappears from directory.html (no longer
    listed) is NOT auto-deleted here; scripts/validate_products.py will
    flag it as an orphan on the next validation run for a human to
    handle.

Usage:
    python scripts/sync_products.py [--base-url URL]

Writes data/directory.json and data/products/*.json in place. Exit code
0 on success (even with individual page fetch warnings), 1 on a
fatal error (e.g. directory.html itself couldn't be parsed).
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from urllib.parse import unquote

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    print("Missing dependency: pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)

DEFAULT_BASE_URL = "https://robotindex.io"
DATA_DIR = "data"
PRODUCTS_DIR = os.path.join(DATA_DIR, "products")

# Hand-verified corrections for every "low-confidence" manufacturer guess
# found so far, plus casing fixes for brands with a specific house style.
# A product not listed here keeps the heuristic guess, flagged in this
# script's output for review.
MANUFACTURER_OVERRIDES = {
    "1x-neo": "1X Technologies",
    "agility-robotics-digit-5": "Agility Robotics",
    "alohamini": "Independent contributors (Hugging Face LeRobot community)",
    "arctos-robotics-arm": "Arctos Robotics",
    "clear-robotics-clear-uv": "Clear Robotics",
    "hatchimals-alive-mystery-hatch": "Spin Master",
    "interbotix-viperx-300-6dof": "Trossen Robotics",
    "joy-for-all-companion-pet-pup": "Ageless Innovation",
    "joy-for-all-orange-tabby-cat": "Ageless Innovation",
    "joy-for-all-walker-squawker": "Ageless Innovation",
    "kscale-zbot-kbot": "K-Scale Labs",
    "learning-resources-code-and-go-robot-mouse": "Learning Resources",
    "lego-education-spike-prime": "LEGO Education",
    "lekiwi": "SIGRobotics (University of Illinois)",
    "linorobot2": "Linorobot (open-source project)",
    "little-live-pets-my-walking-penguin": "Moose Toys",
    "looi": "LOOI",
    "luwu-xgo-mini2": "Luwu Dynamics",
    "mangdang-mini-pupper-2-pro": "MangDang",
    "mondo-robotics-beni": "Mondo Robotics",
    "otto-diy-maker-kit": "OttoDIY",
    "roomba-max-875-combo": "iRobot",
    "roybi-robot": "ROYBI",
    "smp-robotics-s5-series": "SMP Robotics",
    "sunfounder-galaxyrvr": "SunFounder",
    "sunfounder-picar-x": "SunFounder",
    "tamagotchi-uni": "Bandai",
    "turtlebot4": "Clearpath Robotics",
    "ugo-nova": "ugo Inc.",
    "wlkata-mirobot": "WLKATA",
    "xlean-tr1": "xLean",
    "yukai-engineering-bocco-emo": "Yukai Engineering",
    "yukai-engineering-qoobo": "Yukai Engineering",
}

MANUFACTURER_PATTERNS = [
    re.compile(r"^([A-Z][A-Za-z0-9&.\- ]{1,40}?)'s\b"),
    re.compile(r"\bfrom ([A-Z][A-Za-z0-9&.\- ]{1,40}?)(?:[,.—]| —| \(|\.$)"),
    re.compile(r"\bby ([A-Z][A-Za-z0-9&.\- ]{1,40}?)(?:[,.—]| —| \(|\.$| in collaboration)"),
]

review_flags = []
fetch_warnings = []


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "robotindex-sync/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def fetch_ok(url):
    """Like fetch(), but returns None (with a warning logged) instead of
    raising on a 404 -- a directory item can point at a not-yet-live page."""
    try:
        return fetch(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            fetch_warnings.append(f"{url}: 404 (not live yet, skipped)")
            return None
        raise
    except Exception as e:
        fetch_warnings.append(f"{url}: {e}")
        return None


# ---------------------------------------------------------------- markdown

def inline_to_md(node):
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name in ("strong", "b"):
                parts.append(f"**{inline_to_md(child)}**")
            elif child.name in ("em", "i"):
                parts.append(f"*{inline_to_md(child)}*")
            elif child.name == "code":
                parts.append(f"`{inline_to_md(child)}`")
            elif child.name == "a":
                href = child.get("href", "")
                parts.append(f"[{inline_to_md(child)}]({href})")
            elif child.name == "small":
                parts.append(f" ({inline_to_md(child)})")
            elif child.name == "br":
                parts.append("\n")
            else:
                parts.append(inline_to_md(child))
    return "".join(parts).strip()


def table_to_md(table):
    rows = table.find_all("tr")
    if not rows:
        return ""
    out = []
    header = [inline_to_md(c) for c in rows[0].find_all(["th", "td"])]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join(["---"] * len(header)) + " |")
    for r in rows[1:]:
        cells = [inline_to_md(c) for c in r.find_all(["th", "td"])]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def list_to_md(ul):
    return "\n".join(f"- {inline_to_md(li)}" for li in ul.find_all("li", recursive=False))


def block_to_md(container):
    pieces = []
    for child in container.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                pieces.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        cls = child.get("class") or []
        if child.name == "table":
            pieces.append(table_to_md(child))
        elif child.name in ("ul", "ol"):
            pieces.append(list_to_md(child))
        elif child.name == "div" and "prose" in cls:
            pieces.append(inline_to_md(child))
        else:
            txt = inline_to_md(child)
            if txt:
                pieces.append(txt)
    return "\n\n".join(p for p in pieces if p.strip())


# --------------------------------------------------------- manufacturer

def guess_manufacturer(desc_text, manufacturer_url):
    for pat in MANUFACTURER_PATTERNS:
        m = pat.search(desc_text)
        if m:
            name = m.group(1).strip()
            if len(name) >= 2 and name[0].isupper() and name.lower() not in ("the", "a", "an", "this", "it"):
                return name, "high"
    if manufacturer_url:
        m = re.search(r"https?://([a-zA-Z0-9.\-]+)", manufacturer_url)
        if m:
            parts = [p for p in m.group(1).split(".") if p]
            sld = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
            return sld.replace("-", " ").title(), "low"
    return None, "none"


# ---------------------------------------------------------- directory.js

def parse_directory_array(html):
    """Pull the DIRECTORY JS array literal out of directory.html and parse
    it into Python data, without a full JS parser: bracket-depth-match the
    array (respecting string literals), then regex-quote its bare object
    keys so it becomes valid JSON."""
    marker = "const DIRECTORY = ["
    if marker not in html:
        raise RuntimeError("directory.html: 'const DIRECTORY = [' not found -- page structure changed")
    start = html.index(marker) + len(marker) - 1
    depth = 0
    in_str = False
    str_char = None
    end = None
    i = start
    while i < len(html):
        c = html[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == str_char:
                in_str = False
        else:
            if c in "\"'":
                in_str = True
                str_char = c
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        i += 1
    if end is None:
        raise RuntimeError("directory.html: DIRECTORY array is unterminated -- page structure changed")

    arr_text = html[start:end + 1]
    json_text = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', arr_text)
    json_text = re.sub(r',(\s*[\]}])', r'\1', json_text)
    return json.loads(json_text)


def build_directory_and_map(categories):
    """Returns (directory_json_dict, dir_map) where dir_map is
    href -> {name, category, subcategory, tier, openSource, pendingFlag}."""
    directory = {"categories": []}
    dir_map = {}
    for cat in categories:
        cat_out = {"category": cat["category"], "subcategories": []}
        for sub in cat.get("subcategories", []):
            sub_out = {"name": sub.get("name", ""), "items": []}
            for item in sub.get("items", []):
                href = item["href"]
                pid = href[len("product-"):-len(".html")] if href.startswith("product-") and href.endswith(".html") else href
                item_out = {
                    "name": item["name"],
                    "tier": item["tier"],
                    "productId": pid,
                }
                if item.get("openSource"):
                    item_out["openSource"] = True
                if item.get("pending"):
                    item_out["pending"] = True
                sub_out["items"].append(item_out)
                dir_map[href] = {
                    "name": item["name"],
                    "category": cat["category"],
                    "subcategory": sub.get("name", ""),
                    "tier": item["tier"],
                    "openSource": bool(item.get("openSource")),
                    "pendingFlag": bool(item.get("pending")),
                }
            cat_out["subcategories"].append(sub_out)
        directory["categories"].append(cat_out)
    return directory, dir_map


# ------------------------------------------------------------- extraction

def classify_firmware_community(soup, repo_to_id):
    section = None
    for h2 in soup.find_all("h2"):
        if h2.get_text(strip=True).startswith("Firmware"):
            section = h2.find_parent("section")
            break
    if section is None:
        return []
    entries = []
    for card in section.select(".cross-link-card"):
        name_el = card.select_one(".name")
        desc_el = card.select_one(".desc")
        name = name_el.get_text(strip=True) if name_el else ""
        desc = inline_to_md(desc_el) if desc_el else ""
        link = card.select_one("a.repo-link")
        combined = f"{name} {desc}"
        if "closed platform" in combined.lower():
            entries.append({"status": "closed-platform", "note": desc or name})
            continue
        if re.search(r"no (?:explicit )?open[- ]source license|no stated license|no open license", combined, re.I):
            entries.append({"status": "unlicensed", "note": desc or name})
            continue
        if link:
            href = link.get("href", "")
            if "builds-mods.html" in href:
                m = re.search(r"[?&]repo=([^&]+)", href)
                if m:
                    repo_url = unquote(m.group(1))
                    bm_id = repo_to_id.get(repo_url.rstrip("/").lower())
                    if bm_id:
                        entries.append({"buildsModsId": bm_id})
                        continue
                    entries.append({"status": "not-listed", "note": desc or f"Repo: {repo_url}"})
                    continue
            entries.append({"status": "not-listed", "note": desc or f"See {href}"})
        else:
            entries.append({"status": "not-listed", "note": desc or name or "No public repository listed."})
    return entries


def extract_specs_from_grid(soup):
    specs_h2 = next((h2 for h2 in soup.find_all("h2") if h2.get_text(strip=True).startswith("Specs")), None)
    if specs_h2 is None:
        return None
    grid = specs_h2.find_next_sibling("div", class_="spec-grid")
    if grid is None:
        return None
    out = []
    for card in grid.select(".spec-card"):
        label_el = card.select_one(".label")
        value_el = card.select_one(".value")
        if not label_el or not value_el:
            continue
        small_el = value_el.select_one("small")
        note = inline_to_md(small_el) if small_el else None
        value_clone = BeautifulSoup(str(value_el), "html.parser").find("div")
        if value_clone.select_one("small"):
            value_clone.select_one("small").decompose()
        entry = {"label": inline_to_md(label_el), "value": inline_to_md(value_clone)}
        if note:
            entry["note"] = note
        out.append(entry)
    return out


def extract_specs_from_table(soup):
    table = soup.select_one("table.compare-table")
    if table is None:
        return None
    thead = table.find("thead")
    tier_cols = [th.get_text(strip=True) for th in thead.select("th.tier-col")] if thead else []
    out = []
    for row in table.select("tbody tr"):
        th = row.find("th")
        label = th.get_text(strip=True) if th else ""
        tds = row.find_all("td")
        if len(tds) == 1 and tds[0].get("colspan"):
            out.append({"label": label, "value": inline_to_md(tds[0])})
        else:
            parts = []
            for col, td in zip(tier_cols, tds):
                small = td.select_one("small")
                note = f" ({inline_to_md(small)})" if small else ""
                clone = BeautifulSoup(str(td), "html.parser")
                if clone.select_one("small"):
                    clone.select_one("small").decompose()
                parts.append(f"{col}: {inline_to_md(clone)}{note}")
            out.append({"label": label, "value": "; ".join(parts)})
    return out


def extract_pricing(soup):
    h2 = next((c for c in soup.find_all("h2") if c.get_text(strip=True) == "Reference Price"), None)
    if h2 is None:
        return {}
    section = h2.find_parent("section")
    prose_divs = section.select(".prose") if section else []
    summary = "\n\n".join(inline_to_md(p) for p in prose_divs if inline_to_md(p))
    pricing = {}
    if summary:
        pricing["summary"] = summary
    table = section.select_one("table.tier-table") if section else None
    if table:
        tiers = []
        for r in table.find_all("tr")[1:]:
            cells = r.find_all("td")
            if len(cells) < 2:
                continue
            entry = {"tier": inline_to_md(cells[0]), "price": inline_to_md(cells[1])}
            if len(cells) > 2:
                notes = inline_to_md(cells[2])
                if notes:
                    entry["notes"] = notes
            tiers.append(entry)
        if tiers:
            pricing["tiers"] = tiers
    return pricing


def extract_editorial(soup):
    excluded = {"Specs", "Reference Price", "Firmware & Community", "Specs (Base)"}
    out = []
    for h2 in soup.find_all("h2"):
        heading = h2.get_text(strip=True)
        if heading in excluded:
            continue
        section = h2.find_parent("section")
        if section is None:
            continue
        body_parts = []
        for sib in h2.find_next_siblings():
            if sib.name == "table" and "tier-table" in (sib.get("class") or []):
                continue
            if sib.name == "div":
                cls = sib.get("class") or []
                if "prose" in cls:
                    body_parts.append(inline_to_md(sib))
            elif sib.name == "table":
                body_parts.append(table_to_md(sib))
            elif sib.name == "ul":
                body_parts.append(list_to_md(sib))
        body = "\n\n".join(p for p in body_parts if p.strip())
        if body.strip():
            out.append({"heading": heading, "body": body})
    return out


def extract_sourcing(soup):
    note = soup.select_one(".verify-note")
    return inline_to_md(note) if note else ""


def process_product(href, html_text, meta, repo_to_id):
    soup = BeautifulSoup(html_text, "html.parser")
    pid = href[len("product-"):-len(".html")]

    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else meta["name"]

    desc_el = soup.select_one(".product-desc")
    description = inline_to_md(desc_el) if desc_el else ""

    maker_link = soup.select_one("a.maker-link")
    manufacturer_url = maker_link.get("href") if maker_link else None

    if pid in MANUFACTURER_OVERRIDES:
        manufacturer = MANUFACTURER_OVERRIDES[pid]
    else:
        manufacturer, confidence = guess_manufacturer(description, manufacturer_url)
        if confidence != "high":
            review_flags.append((pid, "manufacturer", f"low-confidence guess: {manufacturer!r} -- not in MANUFACTURER_OVERRIDES, check by hand"))

    specs = extract_specs_from_grid(soup)
    is_table_page = False
    if specs is None:
        specs = extract_specs_from_table(soup)
        is_table_page = True
    if specs is None:
        review_flags.append((pid, "specs", "no spec-grid or compare-table found"))
        specs = []

    pricing = extract_pricing(soup)
    editorial = extract_editorial(soup)
    firmware_community = classify_firmware_community(soup, repo_to_id)
    sourcing = extract_sourcing(soup)

    closed_platform = bool(firmware_community) and all(e.get("status") == "closed-platform" for e in firmware_community)

    record = {
        "$schema": "../../schema/product.schema.json",
        "id": pid,
        "slug": href,
        "name": name,
        "manufacturer": manufacturer or "Unknown",
        "category": meta["category"],
        "subcategory": meta["subcategory"],
        "tier": meta["tier"],
        "flags": {
            "pending": meta["pendingFlag"],
            "openSource": meta["openSource"],
            "closedPlatform": closed_platform,
        },
        "description": description,
        "specs": specs,
        "sourcing": sourcing,
    }
    if manufacturer_url:
        record["manufacturerUrl"] = manufacturer_url
    if pricing:
        record["pricing"] = pricing
    if editorial:
        record["editorial"] = editorial
    if firmware_community:
        record["firmwareCommunity"] = firmware_community

    if is_table_page:
        review_flags.append((pid, "specs", "extracted from compare-table (multi-model page) -- verify shape"))

    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    try:
        directory_html = fetch(f"{base}/directory.html")
        categories = parse_directory_array(directory_html)
    except Exception as e:
        print(f"FATAL: could not fetch/parse directory.html: {e}", file=sys.stderr)
        return 1

    directory_json, dir_map = build_directory_and_map(categories)

    try:
        with open(os.path.join(DATA_DIR, "builds-mods.json"), encoding="utf-8") as f:
            builds_mods = json.load(f)
        repo_to_id = {e["repo"].rstrip("/").lower(): e["id"] for e in builds_mods["entries"]}
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"FATAL: could not load data/builds-mods.json for firmware cross-referencing: {e}", file=sys.stderr)
        return 1

    os.makedirs(PRODUCTS_DIR, exist_ok=True)
    ok = 0
    for href, meta in sorted(dir_map.items()):
        page_html = fetch_ok(f"{base}/{href}")
        if page_html is None:
            continue
        try:
            record = process_product(href, page_html, meta, repo_to_id)
        except Exception as e:
            fetch_warnings.append(f"{href}: extraction failed: {e}")
            continue
        out_path = os.path.join(PRODUCTS_DIR, record["id"] + ".json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        ok += 1

    directory_json["$schema"] = "../schema/directory.schema.json"
    with open(os.path.join(DATA_DIR, "directory.json"), "w", encoding="utf-8") as f:
        json.dump(directory_json, f, indent=2, ensure_ascii=False)

    print(f"Extracted {ok} of {len(dir_map)} directory items")
    if fetch_warnings:
        print(f"\n{len(fetch_warnings)} warning(s):")
        for w in fetch_warnings:
            print(f"  - {w}")
    if review_flags:
        print(f"\n{len(review_flags)} field(s) worth a human look:")
        for pid, field, note in review_flags:
            print(f"  - {pid} | {field} | {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

