# Contributing

This dataset only stays trustworthy if every entry in it can be checked against a real source. That's true whether it's an edit made directly by the robotindex team or a correction suggested here.

## Reporting a correction

Open an issue with the **Data correction** template. Include:

- Which file and which entry (e.g. `data/builds-mods.json`, the `chvmp-champ` entry).
- What's currently wrong.
- What it should say instead.
- A source: the project's own repository, its `LICENSE` file, an official spec or product page. "I'm pretty sure" isn't enough — this dataset exists specifically because vendor claims and secondhand descriptions drift from reality.

## Opening a pull request

PRs are welcome for the same kind of change: fixing a stale description, adding a project that qualifies but isn't listed yet, correcting a license claim. A few things that keep review fast:

- **One correction per PR.** Bundled changes are harder to verify and slower to merge.
- **Link your source in the PR description**, not just in the diff.
- **Validate against the schema first.** Both `product.schema.json` and `builds-mods-entry.schema.json` are standard JSON Schema (draft 2020-12) — any validator will do, e.g. `pip install jsonschema` and check your file against the matching schema before opening the PR.
- **Don't invent an `id`.** If you're editing an existing entry, keep its `id` unchanged — other files reference entries by `id`, and renaming one breaks those links silently.

## What qualifies for `data/builds-mods.json`

This mirrors robotindex.io's own editorial policy:

- **`opensource` tab** — requires a public repository under an approved OSI or open hardware license. An open API or a modular case doesn't qualify on its own.
- **`configs` / `firmware` tabs** — community recipes, configuration profiles, and firmware/bypass projects. These aren't license-gated the same way, but still need to be real, maintained, and accurately described.

## No automated merging yet

There's no CI pipeline fact-checking claims against live sources, and no linting bot yet — every PR and issue gets a manual look. That's slower than a fully automated flow, but faster review with real verification beats fast review of unverified claims, especially this early.
