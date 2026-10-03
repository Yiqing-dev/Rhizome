# RXF — Rhizome Exchange Format

RXF is Rhizome's only input format: a YAML document a chat assistant writes at the end of a paper
discussion. It is versioned independently of the app.

| File | Purpose |
| --- | --- |
| `schema/rxf-v1.schema.json` | JSON Schema (draft 2020-12), generated from `backend/rhizome/rxf/schema.py` |
| `examples/*.yaml` | Valid examples (synthetic papers) — contract-test inputs |
| `examples/invalid/*.yaml` | Must be rejected — contract-test inputs |
| `instructions/export-instructions.{zh,en}.md` | Text for the Claude / ChatGPT Project instructions |

## Rules of the format

- Every document carries `rxf_version`; ingest validates against that version's schema.
- YAML is parsed, converted to JSON, validated with the JSON Schema, then parsed into typed models.
  Dates stay strings; a surrounding ```` ```yaml ```` fence and a BOM are tolerated.
- Field names are fixed English. Values may be in any language.
- Unknown fields are errors (they usually mean the model drifted from the skeleton).
- A failed file is moved to `error/` with a report listing each path and problem; paste it back into
  the chat and ask for a corrected export.

## v1 additions beyond the original skeleton

All optional, defaults shown:

| Field | Default | Why |
| --- | --- | --- |
| `claims[].stance` | `supports` | lets an export state that the paper argues *against* a claim |
| `assets.datasets[].role` | `uses` | `uses` / `produces` edge |
| `assets.datasets[].name`, `.evidence` | — | datasets without an accession; evidence location |
| `assets.methods[].modality`, `.extends`, `.evidence`, `.biotools` | — | `of_modality` / `extends` edges, anchoring |
| `paper.authors`, `.venue`, `.url` | — | card display when OpenAlex is unavailable |

## Changelog

- **v1** (2026-10-03) — initial version.
