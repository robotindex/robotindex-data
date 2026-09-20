# Schema notes

Two schemas live here, at different stages of maturity.

## `builds-mods-entry.schema.json` — stable, live

Describes one entry in `data/builds-mods.json`. This is the real schema behind a real, deployed feature: [robotindex.io/builds-mods](https://robotindex.io/builds-mods) fetches `data/builds-mods.json` at request time and renders directly from it. Treat this schema as stable — changes here affect a live page.

## `product.schema.json` — draft, not yet live

Describes a full product record: specs, pricing, editorial copy, and a link into `builds-mods.json` by id. The three files in `data/products/` validate against it and show the target shape, but none of robotindex.io's ~139 product pages are generated from this yet — they're still individually hand-authored HTML. Expect this schema to change as more of the catalog migrates onto it; don't build anything that assumes it's final.

A few decisions worth knowing if you're extending either schema:

- **Specs stay label/value/note pairs, not fixed per-category fields.** A drone, a vacuum, and a robot arm share almost no spec vocabulary — Suction/Mopping vs. Cutting Width/Max Slope vs. Degrees of Freedom/Repeatability. Ordered free-text pairs keep the real data instead of forcing categories into a shape they don't share.
- **Editorial prose stays markdown, not further structured.** The long-form sections on a product page are genuinely narrative, not tabular — flattening them into fields would lose the actual content for no benefit to anyone consuming this as data.
- **A product's `firmwareCommunity` references a `builds-mods.json` entry by `id`, never by copying its name/description.** Duplicating that text across files is exactly what caused two real data bugs on the live site before this schema existed — a repo with no license got linked as if it qualified, and a real qualifying repo wasn't listed at all. Referencing by `id` makes that class of bug structurally impossible: there's exactly one place a repo's name and description can live.
