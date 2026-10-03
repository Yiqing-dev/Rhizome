# Rhizome

**A personal literature asset graph.** Rhizome splits papers you have read and discussed into reusable
assets — datasets, methods, ideas, claims — links them with typed edges, and brings them back when they
are useful again. Single-user desktop software, Windows first, bilingual UI (English / 中文).

> 中文设计文档：[docs/design.zh.md](docs/design.zh.md) · 实现对照与进度：[docs/implementation.zh.md](docs/implementation.zh.md)

The kernel never calls an LLM API. Generation and reasoning happen in your Claude / ChatGPT chat;
results arrive as **RXF** exports (a YAML format) and maintenance work goes back to Claude through
**MCP**. Locally Rhizome only runs small models (embedding, reranker, NLI, an optional 2B LLM), and
works with built-in lightweight fallbacks before any model is downloaded.

## How it works

```
L0 raw      RXF exports + PDFs, content-addressed (sha256), append-only
L1 extract  versioned rows (model, prompt version, schema version)        ← recompute starts here
L2 entities Work · Dataset · Method · Idea · Claim · Topic · Organism · Modality
L3 graph    13 typed edges + per-asset vectors
human_decision  merges / rejections / confirmations — applied last on every rebuild, never lost
```

- **Capture**: end a chat with "export RXF", save the YAML into the inbox folder (or say "save to
  Rhizome" in Claude Desktop). Invalid files go to `error/` with a report you paste back into the chat.
- **Anchor**: DOIs resolve through OpenAlex (preprint + journal version = one node); GEO/SRA/ArrayExpress
  accessions and GitHub repos are checked against their APIs. IDs that do not exist are not stored.
- **Canonicalise**: exact alias → vector top-5 → reranker → thresholds (≥0.9 merge, 0.6–0.9 review
  queue, <0.6 new). Claims use NLI; contradictions always go to the review queue first.
- **Find**: hybrid search (vectors + FTS5) at asset level; every hit carries its source paper and the
  evidence location. Topic pages split a topic subtree into data / methods / ideas / claims / papers.
- **Recall**: `relevance × forgetting` — in Claude (`rhz_recall`), on an HPC login node
  (`rhz recall --from-file analysis.py` against a read-only snapshot), and automatically on ingest.
- **Internalise**: weekly synthesis of close-but-unlinked asset pairs across communities, a contested
  claims list, and FSRS spaced repetition with daily caps.

## Install (Windows)

Download `Rhizome_x.y.z_x64-setup.exe` (or the `.msi`) from the GitHub Actions run
*Windows app* / a release, run it, and start Rhizome from the Start menu. Everything is bundled.
See [desktop/README.md](desktop/README.md) for what goes where, portable mode and Claude Desktop setup.

## Install & run (development)

Requires Python ≥ 3.11 and Node ≥ 20.

```bash
pip install -e "./backend[dev]"            # optional extras: [models] [llm] [graph] [postgres]
(cd frontend && npm ci && npm run build)   # builds the UI into the Python package
rhz serve                                  # prints the UI link with its access token
```

```bash
rhz ingest paper.yaml                      # or drop files into the inbox; `rhz watch` / the app watch it
rhz search "GRN inference" --type method --organism "Arabidopsis thaliana"
rhz data GSE999001                         # how to download + linked papers
rhz recall --from-file analysis.py
rhz queue && rhz decide 12 merge           # review queue
rhz review                                 # FSRS cards in the terminal
rhz topic "chromatin accessibility" -d "open-chromatin assays and models" -e "scATAC-seq peak calling"
rhz rebuild                                # recompute L2/L3 from L1 after a model / schema change
rhz vocab -o rhizome-vocab.yaml            # topic vocabulary for the chat Project knowledge
rhz rxf instructions --lang zh_CN          # export instructions for the Claude / ChatGPT Project
rhz bench questions.yaml                   # retrieval benchmark against pre-registered thresholds
```

Data lives in `%APPDATA%\Rhizome` (Windows), `~/Library/Application Support/Rhizome` (macOS) or
`~/.local/share/rhizome` (Linux); override with `--data-dir` / `RHIZOME_DATA_DIR`. Everything
environment-specific is a setting (`rhz settings show`, `rhz settings set language zh_CN`).

### Claude Desktop (MCP)

```json
{ "mcpServers": { "rhizome": { "command": "rhizome-mcp" } } }
```

Tools: `rhz_ingest`, `rhz_recall`, `rhz_search`, `rhz_get`, `rhz_related`, `rhz_queue`, `rhz_decide`,
`rhz_digest`. The server talks to the running app over REST and falls back to in-process calls.
Add "call `rhz_recall` before discussing an analysis plan" to your Project instructions.

### Remote machines (HPC)

Remote clusters usually cannot reach your computer, so they read a snapshot:

```bash
rhz settings set remotes '[{"name": "hpc", "host": "me@login.cluster.edu", "path": "~/.rhizome/rhizome.db",
                           "control_path": "~/.ssh/cm-%r@%h:%p"}]'
rhz sync hpc                               # consistent SQLite copy incl. vectors, via scp
# on the cluster (after `pip install ./backend` from a clone):
rhz --snapshot ~/.rhizome/rhizome.db search "spatial domain detection"
```

`control_path` reuses an SSH ControlMaster connection you already authenticated (2FA servers).

## Repository layout

| Path | Contents |
| --- | --- |
| `backend/` | FastAPI app, pipeline, CLI (`rhz`), MCP server, Alembic migrations, tests |
| `frontend/` | React + sigma.js + Cytoscape.js + react-i18next UI |
| `desktop/` | Tauri v2 shell + PyInstaller sidecar spec → Windows MSI |
| `rxf-spec/` | RXF JSON Schema, examples (synthetic), export instructions (zh / en) |
| `docs/` | Design document and implementation notes |
| `scripts/` | CI gates: i18n key alignment, SPDX headers, dependency licenses |

## Development

- `python -m pytest backend/tests` — unit, RXF contract and retrieval-regression tests (offline).
- `python scripts/check_i18n.py` — zh-CN / en keys aligned, no hard-coded UI strings.
- Database changes always go through Alembic (`backend/rhizome/migrations`); the app backs up the
  database before every upgrade.
- No personal data in the repository: fixtures are synthetic (DOIs under the `10.5555` test prefix).
  `pre-commit` runs gitleaks. Dependencies must be Apache-2.0 compatible; CI rejects GPL / AGPL.
- After changing `rhizome/rxf/schema.py`: `rhz rxf schema -o rxf-spec/schema/rxf-v1.schema.json` and
  copy it to `backend/rhizome/rxf/schemas/` (a contract test checks they match).

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Optional models are downloaded at runtime
and are not distributed with Rhizome.
