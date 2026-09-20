# robotindex-data

The structured, canonical dataset behind [robotindex.io](https://robotindex.io) — an open registry for physical robotics. robotindex is not just a website: it's meant to be the reference dataset consumer robotics is built on top of, the way GitHub, OpenRouter, and LlamaIndex are the reference layer for their respective ecosystems. This repo is the first public piece of that: the data itself, structured, versioned, and inspectable, instead of buried in hand-authored web pages.

## What's here today

- **`schema/`** — JSON Schema definitions for a product record (`product.schema.json`) and a Builds & Mods entry (`builds-mods-entry.schema.json`).
- **`data/builds-mods.json`** — all 31 open-source repos, firmware projects, and community recipes currently listed on [robotindex.io/builds-mods](https://robotindex.io/builds-mods). This is the real, live dataset — `builds-mods.html` on the site fetches this exact structure at request time and renders from it directly.
- **`data/products/`** — three example product records (`unitree-go2.json`, `tesla-optimus.json`, `roborock-saros-20-flow.json`) showing the target shape for the full catalog. **These are illustrative, not live yet** — robotindex.io's ~139 product pages are still individually hand-authored HTML and are being migrated onto this schema incrementally. Treat these three as a preview of where the catalog is headed, not a complete dataset.

## Honesty about where this stands

This is an early, deliberately small first slice, not a finished platform:

- Only the Builds & Mods dataset is actually wired into the live site right now.
- There's no API yet — this is a flat, versioned data repo you can clone or download.
- Corrections go through issues, not automated PR merges (see below) — there's no automated fact-checking pipeline yet, so every change gets a manual look, the same way every entry in this dataset was checked before it was added.
- The full product catalog schema will keep evolving as more of the site migrates onto it. Expect breaking changes to `product.schema.json` before it stabilizes.

## Using the data

Everything here is plain JSON, validated against the schemas in `schema/`. Fetch `data/builds-mods.json` directly, or clone the repo. There's no rate limit, no auth, and nothing here calls out to a paid API.

Live GitHub metadata (stars, license, last commit, open issues) shown on the site is **not** stored in this repo — it's fetched at request time from each project's own repository and cached briefly, since it changes constantly and would go stale the moment it was committed here. This repo holds the editorial facts a human decided: what something is called, what it does, which category it belongs in, and where its source lives.

## Suggesting a correction

Open an issue using the **Data correction** template — tell us what's wrong, what it should say instead, and a source (the project's own repo, its license file, an official spec page). Every correction gets checked against the same standard used to build this dataset in the first place: a claim needs a real source before it goes in, not just a plausible-sounding PR.

Pull requests are welcome too, but are reviewed manually with the same bar — there's no CI or automated linting checking claims yet, so don't expect fast auto-merges. That tooling is planned but not built.

## License

The dataset in this repository is licensed under [CC BY 4.0](LICENSE) — you can use, share, and adapt it, including commercially, as long as you credit robotindex. The schema files (`schema/*.json`) are provided under the same license for simplicity; treat them as freely reusable in your own tooling.

## robotindex.io

The website: directory, rankings, and the live Builds & Mods page this data powers. [robotindex.io](https://robotindex.io)
