# robotindex-data

The canonical, versioned data behind [robotindex.io](https://robotindex.io) — a catalog of consumer/prosumer robots and the AI models, simulation tooling, and datasets that train them.

This repo holds the raw JSON that [Builds & Mods](https://robotindex.io/builds-mods.html) and [Models & Datasets](https://robotindex.io/models-data.html) fetch at request time. There's no build step: edit a JSON file here, merge it, and the live pages update on their next load. This is also where corrections get filed — if something in the catalog is wrong, out of date, or missing, it's fixed here, not on the site itself.

## What's in here

```
data/
  builds-mods.json        Every firmware, config, and open-source project the site lists
  models-data.json        Every world model, sim/training tool, and dataset the site lists
schema/
  builds-mods-entry.schema.json     JSON Schema (draft 2020-12) for one builds-mods.json entry
  models-data-entry.schema.json     JSON Schema (draft 2020-12) for one models-data.json entry
```

Each file is `{ "entries": [ ... ] }` — a flat array of entries, one object per catalog row. Nothing else lives in these files: live GitHub stats (stars, license, last-updated) are *not* stored here — they're fetched at request time by the site's own serverless function and cached at the CDN, so this repo only holds the editorial facts a human actually decided.

As of September 2026: **40** Builds & Mods entries (21 opensource / 7 configs / 12 firmware) and **68** Models & Datasets entries (7 world models / 13 simulation & training / 48 datasets). These numbers move — check `entries.length` rather than trusting this README.

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

## Validating your changes

Both files are validated against their schema with Python's `jsonschema` (draft 2020-12) before anything is merged:

```bash
pip install jsonschema
python3 - <<'EOF'
import json
from jsonschema import Draft202012Validator

for data_file, schema_file in [
    ("data/builds-mods.json", "schema/builds-mods-entry.schema.json"),
    ("data/models-data.json", "schema/models-data-entry.schema.json"),
]:
    data = json.load(open(data_file))
    schema = json.load(open(schema_file))
    validator = Draft202012Validator(schema)
    errors = []
    for i, entry in enumerate(data["entries"]):
        errors += [f"{data_file}[{i}] ({entry.get('id')}): {e.message}" for e in validator.iter_errors(entry)]
    print(data_file, "-", len(errors), "error(s)")
    for e in errors:
        print(" ", e)
EOF
```

Also worth checking before opening a PR:
- No duplicate `id` values within a file.
- Every `repo` / `link` actually resolves (no typos, no dead links).
- New `id`s use lowercase letters, digits, and hyphens only (`^[a-z0-9]+(-[a-z0-9]+)*$`).

## Contributing / filing a correction

- **Something's wrong** (bad link, outdated license, factual error): open an issue or PR correcting just that entry's fields. Include a source for the correction.
- **Adding an entry**: open a PR adding one object to the relevant `entries` array, following the schema above and the editorial standards. Cite your sources (repo URL, license file, official announcement) in the PR description — entries aren't merged without a way to verify them.
- **Never rename an existing `id`** — product pages and other entries on the live site link to entries by id, and a rename silently breaks those links.

## License

The code and schemas in this repo are available for reuse; the dataset content itself catalogs third-party projects and their own separately-stated licenses, which are recorded (not superseded) by the `license` field on each `models-data.json` entry. A repo-wide license for this dataset hasn't been finalized yet — check back or open an issue if that's blocking a use case.
