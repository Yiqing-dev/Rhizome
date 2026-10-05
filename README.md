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

L2/L3 are recomputed from L1 (`rhz rebuild`). Human decisions, your own topics and review history live
only in `rhizome.db`, so `raw/` alone is **not** a full backup: Rhizome writes a consistent copy of the
database once a day (newest 14 kept) into a backup folder you can put on another disk or a synced
cloud folder (Settings → Backups, or `rhz settings set backup_dir "D:\\Backups\\Rhizome"`).

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

Download `Rhizome_x.y.z_x64-setup.exe` (or `…-offline-setup.exe` for machines without internet) from the GitHub Actions run
*Windows app* / a release, run it, and start Rhizome from the Start menu. Everything is bundled.
See [desktop/README.md](desktop/README.md) for what goes where, portable mode and Claude Desktop setup.
Behind a campus or company proxy, set `HTTPS_PROXY` before starting Rhizome (certificates installed
in Windows are trusted); `rhz doctor` checks the path to OpenAlex, NCBI and GitHub, and `rhz enrich`
fetches metadata for papers that were imported offline.

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
rhz sync hpc                               # consistent SQLite copy incl. vectors, one ssh session
# on the cluster:
export RHIZOME_SNAPSHOT=~/.rhizome/rhizome.db   # or pass --snapshot FILE to every command
rhz search "spatial domain detection"
rhz recall --from-file analysis.py
```

The snapshot is read-only: `ingest`, `rebuild`, `sync` and the other write commands refuse to run
against it, and `rhz` without a snapshot on a machine that has no library says so instead of
searching a new empty one. `rhz` warns when the snapshot is older than a week, was written by a
different schema version, or was indexed with a model the cluster cannot load.
`control_path` reuses an SSH ControlMaster connection you already authenticated (2FA servers;
not available with Windows' built-in OpenSSH, where the single session asks once). The upload
time limit is the remote's `"timeout"` (seconds, default 1800).

Installing on a cluster (Python ≥ 3.10; SQLite ≥ 3.34 for keyword search, older versions fall back
to alias matching):

```bash
pipx install ./backend                     # or: uv tool install ./backend
# or inside a conda env:  conda create -n rhz python=3.11 && conda activate rhz && pip install ./backend
# no network on the compute side: build a wheelhouse on a connected machine and install from it
pip wheel ./backend -w wheels && pip install --no-index --find-links wheels rhizome
```

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
- The RXF schema has one source: the Pydantic models in `rhizome/rxf/schema.py` (the validator builds its
  JSON Schema from them). After changing them run `rhz rxf schema -o rxf-spec/schema/rxf-v1.schema.json`
  to refresh the published copy (a contract test checks it matches).

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Optional models are downloaded at runtime
and are not distributed with Rhizome.
