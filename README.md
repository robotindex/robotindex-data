# robotindex-data

The canonical, versioned data behind [robotindex.io](https://robotindex.io) — a catalog of consumer/prosumer robots and the AI models, simulation tooling, and datasets that train them.

This repo holds the raw JSON that [Builds & Mods](https://robotindex.io/builds-mods.html) and [Models & Datasets](https://robotindex.io/models-data.html) are meant to be built from, plus a full structured record for every product in the [Directory](https://robotindex.io/directory.html). **Today the live site actually serves its own same-origin copy of the Builds & Mods / Models & Datasets files** (`robotindex.io/data/*.json`), not this repo directly — this repo is the published, versioned mirror of that data, kept in sync by the scheduled workflow described below rather than fetched live. If that ever changes (the site fetching straight from here instead), this note should be the first thing updated.

The Directory data (`data/directory.json` and `data/products/*.json`) is different: **the live site doesn't serve these as JSON at all.** The 135 product pages are hand-authored HTML, and the Directory's category/subcategory listing is inline JavaScript inside `directory.html`, not a fetched file. Rather than wait for the site itself to change, this repo re-derives that data directly from the live HTML on a schedule (`.github/workflows/sync-products.yml`, using `scripts/sync_products.py`) — it fetches `directory.html` and every `product-*.html` page, parses them the same way the original one-time extraction did by hand, and opens a PR with whatever changed. See "Known limitations" below for what to double-check in that diff before merging, especially on a brand-new product.

There's no build step: edit a JSON file here, merge it, and (for `builds-mods.json`/`models-data.json`) the change is reflected the next time the live site is redeployed and this repo's scheduled sync runs. This is also where corrections get filed — if something in the catalog is wrong, out of date, or missing, it's fixed here, not on the site itself. For `directory.json`/`products/*.json` specifically, a manual fix here will get overwritten by the next scheduled re-extraction unless the underlying live page is also fixed — treat the live HTML as the source of truth for those two, and this repo's copy as a re-derived mirror of it, same as the other two files.

## What's in here

```
data/
  builds-mods.json          Every firmware, config, and open-source project the site lists
  models-data.json          Every world model, sim/training tool, and dataset the site lists
  directory.json            The full Directory browse structure: every product, organized by
                             category/subcategory, referencing product records by id
  products/<id>.json        One file per product (135 total) — specs, pricing, editorial
                             write-up, firmware/community links, and sourcing notes
schema/
  builds-mods-entry.schema.json     JSON Schema (draft 2020-12) for one builds-mods.json entry
  models-data-entry.schema.json     JSON Schema (draft 2020-12) for one models-data.json entry
  directory.schema.json             JSON Schema for directory.json
  product.schema.json               JSON Schema for one data/products/<id>.json record
.github/workflows/
  sync-data.yml             Scheduled workflow (every ~2 days, plus manual dispatch) that fetches
                             the live site's data/builds-mods.json and data/models-data.json,
                             validates them, and opens a PR here if anything changed. See that
                             file's comments for how it works.
  sync-products.yml         Scheduled workflow (every ~2 days, offset from sync-data.yml, plus
                             manual dispatch) that re-extracts directory.json and every
                             products/<id>.json from the live site's HTML and opens a PR if
                             anything changed. See scripts/sync_products.py for how it works.
scripts/
  validate_data.py          Validates builds-mods.json / models-data.json against their schemas.
                             Run by the sync-data workflow, and by hand before a manual PR.
  validate_products.py      Validates directory.json and every products/<id>.json record,
                             including that every productId in directory.json has a matching
                             record and vice versa (no orphans in either direction). Run by the
                             sync-products workflow, and by hand after editing either the
                             directory or a product record.
  sync_products.py          Fetches directory.html and every product-*.html page from the live
                             site and re-derives directory.json + products/*.json from them. Run
                             by the sync-products workflow; can also be run by hand with
                             --base-url pointing at a staging deploy.
```

## Known limitations of the Directory data

The directory/product extraction is mechanical — `sync_products.py` parses the live HTML the same way every time — so these are steady-state limitations of the approach, not one-off mistakes from the original bootstrap:

- **`manufacturer`** is a required field, but the live pages never state it as its own labeled fact — it's inferred from the product description's prose or, failing that, from the manufacturer link's domain name. A hand-verified `MANUFACTURER_OVERRIDES` table in `sync_products.py` corrects every case found so far, including open-source/community projects without a single corporate manufacturer (`alohamini`, `lekiwi`, `linorobot2`). A genuinely **new** product won't be in that table yet — its manufacturer guess shows up as a review flag in the workflow's run log, and is worth checking by hand in the PR diff before merging.
- **`image`** is omitted from every record. The live pages embed product photos as inline base64 data, not as files under a path the way the schema's `image.src` field expects ("path under /images/") — extracting and hosting real image files is a separate piece of work, not done here.
- **Multi-model family pages** (`chasing-family`, `dji-neo-family`, `hoverair-x1-family`) cover 2–3 products each on one page with a comparison table instead of the usual spec-grid. Their `specs` entries are flattened into `"Model A: value; Model B: value"` strings per spec — readable, but a consumer expecting one value per field should know these three records describe a family, not a single product.
- **`pricing.tiers`** is populated only where the live page has an explicit tier-price table (currently just `unitree-go2`); every other multi-tier product's pricing is folded into the prose `pricing.summary` instead of broken out structurally.
- **A page that 404s** (a directory item pointing at a not-yet-live product page) is skipped with a warning rather than failing the sync; its existing record, if any, is left untouched until the page goes live.
- **A product removed from the live directory** isn't auto-deleted here — `validate_products.py` will flag its now-orphaned record for a human to remove by hand.

None of this is invisible — `sourcing` on every record still carries the citation/caveat text from the live page, so a reader always sees what was and wasn't independently confirmed.

Each of `builds-mods.json` / `models-data.json` is `{ "entries": [ ... ] }` — a flat array of entries, one object per catalog row. Nothing else lives in these files: live GitHub stats (stars, license, last-updated) are *not* stored here — they're fetched at request time by the site's own serverless function and cached at the CDN, so this repo only holds the editorial facts a human actually decided.

As of the last update to this line (September 2026): **39** Builds & Mods entries, **68** Models & Datasets entries, and **135** Directory product records. These numbers move — check `entries.length` (or, for the Directory, the number of files in `data/products/`) rather than trusting this README.

## Schema

### `builds-mods.json` entry

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable, lowercase-hyphenated, unique. Product pages on the site link to an entry by this id — **never rename one once it's published.** Two entries can point at the same repo (e.g. two products both citing the same community project) as long as they're distinct editorial rows with distinct ids. |
| `tab` | enum | `opensource` \| `configs` \| `firmware`. `opensource` is gated by the site's open-source policy (must be a real public repo under an OSI/hardware-style license); `configs` and `firmware` aren't license-gated the same way. |
| `name` | string | Display name. |
| `desc` | string | One or two sentences, plain language, no marketing copy. |
| `repo` | string (URI) | Canonical source — usually a GitHub repo, occasionally a docs site or project homepage when no repo exists. Only `github.com` URLs get live star/license/update data; everything else just renders without that row. |

`additionalProperties: false` — nothing outside this list is allowed.

### `models-data.json` entry

| Field | Type | Notes |
|---|---|---|
| `id` | string | Same rules as above: stable, unique, never renamed. |
| `tab` | enum | `world-models` \| `simulation-training` \| `datasets`. |
| `category` | enum | `Open`, `Closed`, `Physics Engine`, `RL Training`, `Synthetic Data & Sensors`, `Robot Manipulation`, `Egocentric Human Video`, `Gameplay / Simulated Interaction`, `Human Motion Capture`. World-models entries must be `Open` or `Closed` — that's the load-bearing distinction on that tab. |
| `access` | enum | `Open` \| `Closed`. Whether the entry can be checked against real public source, or is described only from a vendor's own claims. For world models this always matches `category`; for simulation/training and datasets it shows as a prefix on the license badge instead. |
| `name` | string | Display name. |
| `desc` | string | Plain-language description. |
| `license` | string | Plain-language license/access status — `MIT`, `CC BY-NC 4.0`, `Proprietary`, `Gated — approval required`, a named custom license, etc. Never left vague when the real answer is a restriction. |
| `link` | string (URI) | A GitHub/Codeberg repo when one exists; a vendor's product or docs page when it doesn't (closed world models, gated datasets). |

`additionalProperties: false` here too.

## Editorial standards

**Datasets (RI Dataset Best Practices):** every dataset entry needs a real, independently verifiable GitHub repository — or, for well-established datasets, a direct link to the authors' own original release — with licenses reported exactly as found, and every entry checked against primary sources (the paper, the official repo or release, the project's own site). A dataset with a non-commercial-only or unspecified license is not listed as something a commercial/consumer product could train on, even if the raw data is otherwise freely downloadable — what matters is whether the license actually permits training and shipping a policy on it, not just redistributing the data.

**Builds & Mods `opensource` tab:** gated the same way — a real public repository under a genuine open license, not a teased or discontinued SDK, not a gated developer program, not a private wheel installed by a third-party wrapper. If a product's actual firmware/SDK is closed, it doesn't get an `opensource` entry, no matter how open-friendly the vendor's marketing sounds.

**Closed / vendor-described entries:** allowed on the `models-data.json` `world-models` tab (as `Closed`) precisely because the site is explicit that these are described from a vendor's own materials and haven't been independently verified — that distinction must never get blurred into looking as trustworthy as an `Open` entry.

## Keeping this repo current

Two scheduled GitHub Actions keep this repo in step with the live site, both offset a few hours apart and both opening a PR rather than committing directly to `main`:

- **`sync-data.yml`** fetches `robotindex.io/data/builds-mods.json` and `robotindex.io/data/models-data.json` from the live deployed site roughly every 2 days, validates them against the schemas above, and — only if something changed — opens a pull request here with the diff.
- **`sync-products.yml`** re-extracts `directory.json` and every `products/<id>.json` from the live site's `directory.html` and `product-*.html` pages on the same ~2-day cadence, validates the result, and opens a pull request if anything changed.

Both can be triggered on demand from the Actions tab ("Run workflow") right after deploying a change to the live site, instead of waiting for the schedule. Both can only be as current as the last deploy to robotindex.io — if a fix has been made but not yet deployed, a sync in that window just re-fetches what was already there.

## Validating your changes

`builds-mods.json` / `models-data.json` are validated against their schema with `scripts/validate_data.py` (uses Python's `jsonschema`, draft 2020-12) before anything is merged:

```bash
pip install jsonschema
python3 scripts/validate_data.py \
  data/builds-mods.json schema/builds-mods-entry.schema.json \
  data/models-data.json schema/models-data-entry.schema.json
```

`directory.json` / `products/*.json` are validated the same way with `scripts/validate_products.py`:

```bash
pip install jsonschema
python3 scripts/validate_products.py
```

Both scripts additionally flag duplicate `id` values, and `validate_products.py` also flags orphaned records or dangling `productId` references — things schema validation alone won't catch.

Also worth checking before opening a manual PR:
- Every `repo` / `link` actually resolves (no typos, no dead links).
- New `id`s use lowercase letters, digits, and hyphens only (`^[a-z0-9]+(-[a-z0-9]+)*$`).

## Contributing / filing a correction

- **Something's wrong** (bad link, outdated license, factual error) on `builds-mods.json` or `models-data.json`: open an issue or PR correcting just that entry's fields. Include a source for the correction.
- **Something's wrong** on `directory.json` or a `products/*.json` record: fix it on the live site (that's what `sync-products.yml` re-derives from) — a fix made only here gets overwritten by the next scheduled sync.
- **Adding an entry**: open a PR adding one object to the relevant `entries` array, following the schema above and the editorial standards. Cite your sources (repo URL, license file, official announcement) in the PR description — entries aren't merged without a way to verify them.
- **Never rename an existing `id`** — product pages and other entries on the live site link to entries by id, and a rename silently breaks those links.

## License

The code and schemas in this repo are available for reuse; the dataset content itself catalogs third-party projects and their own separately-stated licenses, which are recorded (not superseded) by the `license` field on each `models-data.json` entry. A repo-wide license for this dataset hasn't been finalized yet — check back or open an issue if that's blocking a use case.
