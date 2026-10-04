# RXF — Rhizome Exchange Format

RXF is Rhizome's only input format: a YAML document a chat assistant writes at the end of a paper
discussion. It is versioned independently of the app.

| File | Purpose |
| --- | --- |
| `schema/rxf-v1.schema.json` | JSON Schema (draft 2020-12): a published copy generated from `backend/rhizome/rxf/schema.py`, which is the only source (the validator builds its schema from the models at runtime) |
| `examples/*.yaml` | Valid examples (synthetic papers) — contract-test inputs |
| `examples/invalid/*.yaml` | Must be rejected — contract-test inputs |
| `instructions/export-instructions.{zh,en}.md` | Text for the Claude / ChatGPT Project instructions |

## Rules of the format

- Every document carries `rxf_version`; ingest validates against that version's schema.
- YAML is parsed, converted to JSON, validated with the JSON Schema, then parsed into typed models.
  Dates stay strings; a surrounding ```` ```yaml ```` fence and a BOM are tolerated.
- Field names are fixed English. Values may be in any language.
- Unknown fields are errors (they usually mean the model drifted from the skeleton).
- Items may carry a file-local `id`. When a file declares any ids, `user_insights[].links_to` and
  `review_cards[].about` must be ids declared in that file (pointing at a topic, claim, dataset,
  method, idea or user insight); on import each reference becomes an edge from the user's idea
  (`relates_to`, or `applicable_to` for a topic). Files without ids keep the older behaviour:
  references are names matched against the library.
- RXF is recognised by content (`rxf_version:` at the top level), not by file name or extension.
- A failed file is moved to `error/` with a report grouped by cause (path pattern + field, with a count
  and a fix hint); paste it back into the chat and ask for a corrected export.
- Known export drift (a flattened `transfer`, `topics[].new`) is never fixed silently. On request
  (`rhz ingest --repair`, "Repair and import" in the app) Rhizome imports a repaired copy: the original
  bytes stay in L0 and the L1 row records the repairs in `meta.repairs`.

## v1 additions beyond the original skeleton

All optional, defaults shown:

| Field | Default | Why |
| --- | --- | --- |
| `claims[].stance` | `supports` | lets an export state that the paper argues *against* a claim |
| `assets.datasets[].role` | `uses` | `uses` / `produces` edge |
| `assets.datasets[].name`, `.evidence` | — | datasets without an accession; evidence location |
| `assets.methods[].modality`, `.extends`, `.evidence`, `.biotools` | — | `of_modality` / `extends` edges, anchoring |
| `paper.authors`, `.venue`, `.url` | — | card display when OpenAlex is unavailable |
| `id` on topics, claims, datasets, methods, ideas, issues, user_insights, review_cards | — | in-file references from `links_to` / `about` |
| `id`, `exported_at`, `language` at the top level | — | stored in L1; `language` will feed the alias `lang` field |

## Changelog

- **v1** (2026-10-03) — initial version.
- **v1, additive** (2026-10-04) — optional `id`s and in-file references, top-level `id` / `exported_at`
  / `language`. Older v1 files stay valid.
