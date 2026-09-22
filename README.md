# robotindex-data

The canonical, versioned data behind [robotindex.io](https://robotindex.io) — a catalog of consumer/prosumer robots and the AI models, simulation tooling, and datasets that train them.

This repo holds the raw JSON that [Builds & Mods](https://robotindex.io/builds-mods.html) and [Models & Datasets](https://robotindex.io/models-data.html) are meant to be built from, plus a full structured record for every product in the [Directory](https://robotindex.io/directory.html). **Today the live site actually serves its own same-origin copy of the Builds & Mods / Models & Datasets files** (`robotindex.io/data/*.json`), not this repo directly — this repo is the published, versioned mirror of that data, kept in sync by the scheduled workflow described below rather than fetched live. If that ever changes (the site fetching straight from here instead), this note should be the first thing updated.

The Directory data (`data/directory.json` and `data/products/*.json`) is different: **the live site doesn't serve these as JSON at all today.** The 135 product pages are hand-authored HTML, and the Directory's category/subcategory listing is inline JavaScript inside `directory.html`, not a fetched file. What's in this repo is a one-time extraction from the live site as of this commit — accurate as of today, but there's no automated way to keep it current the way the scheduled workflow does for the other two files, until the site itself is changed to render those pages from this data instead of the other way around. Treat it as a snapshot, not a live mirror, and see "Known limitations" below before building on it.

There's no build step: edit a JSON file here, merge it, and (for `builds-mods.json`/`models-data.json`) the change is reflected the next time the live site is redeployed and this repo's scheduled sync runs. This is also where corrections get filed — if something in the catalog is wrong, out of date, or missing, it's fixed here, not on the site itself.

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
  sync-data.yml            Scheduled workflow (every ~2 days, plus manual dispatch) that fetches
                            the live site's data/builds-mods.json and data/models-data.json,
                            validates them, and opens a PR here if anything changed. Does NOT
                            touch directory.json or products/ — see above for why. See that
                            file's comments for how it works.
scripts/
  validate_data.py          Validates builds-mods.json / models-data.json against their schemas.
                             Run by the sync workflow, and by hand before a manual PR.
  validate_products.py      Validates directory.json and every products/<id>.json record,
                             including that every productId in directory.json has a matching
                             record and vice versa (no orphans in either direction). Not run
                             automatically by anything yet — run it by hand after editing
                             either the directory or a product record.
```

## Known limitations of the Directory data (as of this commit)

This was extracted mechanically from the live HTML pages, then spot-checked and corrected where the extraction was clearly wrong — but it's worth knowing where the rough edges are before treating every field as equally solid:

- **`manufacturer`** is a required field, but the live pages never state it as its own labeled fact — it's inferred from the product description's prose or, failing that, from the manufacturer link's domain name. Most records are solid (either matched a clear "X's ..." / "from X" sentence, or a clean single-brand domain), but a handful of open-source/community projects without a single corporate manufacturer (`alohamini`, `lekiwi`, `linorobot2`) got a descriptive placeholder instead of a company name — worth a second look before relying on that field for those three.
- **`image`** is omitted from every record. The live pages embed product photos as inline base64 data, not as files under a path the way the schema's `image.src` field expects ("path under /images/") — extracting and hosting 135 actual image files is a separate piece of work, not done here.
- **Multi-model family pages** (`chasing-family`, `dji-neo-family`, `hoverair-x1-family`) cover 2–3 products each on one page with a comparison table instead of the usual spec-grid. Their `specs` entries are flattened into `"Model A: value; Model B: value"` strings per spec — readable, but a consumer expecting one value per field should know these three records describe a family, not a single product.
- **`pricing.tiers`** is populated only where the live page had an explicit tier-price table (currently just `unitree-go2`); every other multi-tier product's pricing is folded into the prose `pricing.summary` instead of broken out structurally.

None of this is invisible — `sourcing` on every record still carries the citation/caveat text from the live page, so a reader always sees what was and wasn't independently confirmed. But the four points above are about the *record's own shape*, not the specs' accuracy, so they're called out separately here.

Each data file is `{ "entries": [ ... ] }` — a flat array of entries, one object per catalog row. Nothing else lives in these files: live GitHub stats (stars, license, last-updated) are *not* stored here — they're fetched at request time by the site's own serverless function and cached at the CDN, so this repo only holds the editorial facts a human actually decided.

As of this bootstrap commit (September 2026): **39** Builds & Mods entries, **68** Models & Datasets entries, and **135** Directory product records. These numbers move — check `entries.length` (or, for the Directory, the number of files in `data/products/`) rather than trusting this README.

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

A scheduled GitHub Action (`.github/workflows/sync-data.yml`) fetches `robotindex.io/data/builds-mods.json` and `robotindex.io/data/models-data.json` from the live deployed site roughly every 2 days, validates them against the schemas above, and — only if something changed — opens a pull request here with the diff for a human to review and merge. It does not commit directly to `main`. You can also trigger it on demand from the Actions tab ("Run workflow") right after deploying a change to the live site, instead of waiting for the schedule.

This keeps the repo in step with what's actually deployed, but it can only be as current as the last deploy to robotindex.io — if a fix has been made to the site's data but not yet deployed, a sync in that window just re-fetches the same numbers as before.

## Validating your changes

Both files are validated against their schema with `scripts/validate_data.py` (uses Python's `jsonschema`, draft 2020-12) before anything is merged:

```bash
pip install jsonschema
python3 scripts/validate_data.py \
  data/builds-mods.json schema/builds-mods-entry.schema.json \
  data/models-data.json schema/models-data-entry.schema.json
```

It checks each entry against its schema, and additionally flags duplicate `id` values within a file — the one thing schema validation alone won't catch.

Also worth checking before opening a PR:
- Every `repo` / `link` actually resolves (no typos, no dead links).
- New `id`s use lowercase letters, digits, and hyphens only (`^[a-z0-9]+(-[a-z0-9]+)*$`).

## Contributing / filing a correction

- **Something's wrong** (bad link, outdated license, factual error): open an issue or PR correcting just that entry's fields. Include a source for the correction.
- **Adding an entry**: open a PR adding one object to the relevant `entries` array, following the schema above and the editorial standards. Cite your sources (repo URL, license file, official announcement) in the PR description — entries aren't merged without a way to verify them.
- **Never rename an existing `id`** — product pages and other entries on the live site link to entries by id, and a rename silently breaks those links.

## License

The code and schemas in this repo are available for reuse; the dataset content itself catalogs third-party projects and their own separately-stated licenses, which are recorded (not superseded) by the `license` field on each `models-data.json` entry. A repo-wide license for this dataset hasn't been finalized yet — check back or open an issue if that's blocking a use case.
