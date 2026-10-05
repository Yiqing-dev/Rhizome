# SPDX-License-Identifier: Apache-2.0
"""`rhz` command-line client. Works against the local library, a running server, or (on remote
machines such as HPC login nodes) a read-only snapshot via --snapshot."""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer

from .config import get_settings, load_settings, set_overrides, set_settings, update_settings
from .i18n import _

app = typer.Typer(help="Rhizome - personal literature asset graph", no_args_is_help=True,
                  pretty_exceptions_enable=False)
rxf_app = typer.Typer(help="RXF exchange format tools")
settings_app = typer.Typer(help="Show or change settings")
models_app = typer.Typer(help="Optional local models")
app.add_typer(rxf_app, name="rxf")
app.add_typer(settings_app, name="settings")
app.add_typer(models_app, name="models")

_state: dict[str, Any] = {"json": False, "snapshot": None}


@app.callback()
def main(data_dir: Optional[Path] = typer.Option(None, envvar="RHIZOME_DATA_DIR", help="Data directory"),
         snapshot: Optional[Path] = typer.Option(None, envvar="RHIZOME_SNAPSHOT",
                                                 help="Read-only snapshot file (remote machines)"),
         as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
         lang: Optional[str] = typer.Option(None, help="Interface language: en | zh_CN")) -> None:
    st = load_settings(data_dir) if data_dir else get_settings()
    set_settings(st)
    if lang:
        st = set_overrides(language=lang)
    _state["json"] = as_json
    _state["snapshot"] = snapshot
    from .logging_setup import setup_logging

    if snapshot is None:
        setup_logging(st)


def _client(prefer_http: bool = True):
    from .client import connect

    return connect(get_settings(), snapshot=_state["snapshot"], prefer_http=prefer_http)


def _out(obj: Any) -> bool:
    if _state["json"]:
        typer.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
        return True
    return False


def _hit_line(h: dict[str, Any]) -> str:
    src = h.get("sources") or []
    where = f"  ← {src[0]['title'][:60]}" + (f" ({src[0]['evidence']})" if src[0].get("evidence") else "") if src else ""
    return f"[{h['id']:>5}] {h['type']:<8} {h['name'][:90]}{where}"


# ---- serve / watch ---------------------------------------------------------------------

@app.command()
def serve(host: Optional[str] = None, port: Optional[int] = None,
          watch_inbox: bool = typer.Option(True, help="Also watch the inbox directory")) -> None:
    """Run the API (and the bundled UI) on this machine."""
    import uvicorn

    from .api.app import create_app

    st = set_overrides(host=host, port=port) if (host or port) else get_settings()
    import os

    from .system import clear_server_marker, watch_parent, write_server_marker

    parent = os.environ.get("RHIZOME_PARENT_PID")
    if parent and parent.isdigit():
        watch_parent(int(parent))

    application = create_app(watch_inbox=watch_inbox)
    url = f"http://{st.host}:{st.port}"
    typer.echo(_("cli.serving", url=url))
    typer.echo(_("cli.open_ui", url=f"{url}/#token={application.state.token}"))
    write_server_marker(url)  # lets the CLI / MCP server find this instance on a non-default port
    try:
        uvicorn.run(application, host=st.host, port=st.port, log_level="warning")
    finally:
        clear_server_marker()


@app.command()
def mcp() -> None:
    """Run the MCP server on stdio (configured in Claude Desktop)."""
    from .mcp_server import main as mcp_main

    mcp_main()


@app.command()
def watch() -> None:
    """Watch the inbox and ingest RXF files as they arrive."""
    from .db.session import init_db
    from .inbox import watch as do_watch

    init_db()
    typer.echo(_("cli.watching", path=get_settings().inbox))

    def report(path, res):
        _print_ingest(path.name, res.to_dict())

    try:
        do_watch(report)
    except KeyboardInterrupt:
        pass


# ---- ingest ------------------------------------------------------------------------------

def _print_ingest(name: str, r: dict[str, Any]) -> None:
    if not r.get("ok"):
        typer.secho(_("cli.failed", file=name), fg="red")
        if r.get("report"):
            typer.echo(r["report"])
        return
    if r.get("duplicate"):
        typer.echo(_("cli.duplicate", file=name))
        return
    typer.secho(_("cli.ingested", file=name, work=r.get("work_key")), fg="green")
    if r.get("repairs"):
        typer.secho(_("cli.repaired", fixes="; ".join(_(f"rxf.repair.{c}") for c in r["repairs"])), fg="yellow")
    if r.get("suspect"):
        typer.secho(_("cli.suspect", ids=", ".join(r["suspect"])), fg="yellow")
    if r.get("related"):
        typer.echo(_("cli.related"))
        for w in r["related"]:
            dims = ", ".join(sorted({v["dimension"] for v in w["via"]}))
            typer.echo(f"  [{w['work_id']}] {w['title'][:80]}  ({dims})")


@app.command()
def ingest(files: list[Path], move: bool = typer.Option(False, help="Move files to done/ or error/"),
           repair: bool = typer.Option(False, help="Fix known export drift first (the original stays in L0, "
                                                    "the fix is recorded in L1)")) -> None:
    """Ingest RXF files (a same-named .pdf is attached automatically)."""
    if move:
        from .db.session import init_db
        from .pipeline.ingest import ingest_file

        init_db()
        for f in files:
            _print_ingest(f.name, ingest_file(f, repair=repair).to_dict())
        return
    from .pipeline.ingest import read_inbox_file

    c = _client()
    results = []
    for f in files:
        text, pdf = read_inbox_file(f)
        r = c.ingest(text, f.name, pdf, repair=repair)
        results.append(r)
        if not _state["json"]:
            _print_ingest(f.name, r)
    _out(results)
    if any(not r.get("ok") for r in results):
        raise typer.Exit(1)


# ---- query ---------------------------------------------------------------------------------

@app.command()
def search(q: str, type: Optional[str] = typer.Option(None, "--type", "-t", help="comma-separated entity types"),
           organism: Optional[str] = None, modality: Optional[str] = None,
           year_min: Optional[int] = None, year_max: Optional[int] = None,
           edge_type: Optional[str] = typer.Option(None, help="e.g. proposes, produces, evaluates"),
           limit: int = 20) -> None:
    """Hybrid search over assets."""
    hits = _client().search(q, types=type, organism=organism, modality=modality, year_min=year_min,
                            year_max=year_max, edge_type=edge_type, limit=limit)
    if _out(hits):
        return
    if not hits:
        typer.echo(_("cli.no_results"))
    for h in hits:
        typer.echo(_hit_line(h))


@app.command()
def get(ref: str) -> None:
    """Show a paper or asset card by id or key."""
    c = _client()
    card = c.get(int(ref)) if ref.isdigit() else c.get_by_key(ref)
    if card is None:
        typer.echo(_("api.not_found"))
        raise typer.Exit(1)
    if _out(card):
        return
    typer.secho(f"{card['type']}: {card['name']}", bold=True)
    typer.echo(f"key: {card['key']}" + (f"   id: {card['external_id']}" if card.get("external_id") else ""))
    for t in (card.get("attrs") or {}).get("tldr", []):
        typer.echo(f"  • {t}")
    for etype, edges in sorted(card["edges"].items()):
        typer.echo(f"{etype}:")
        for e in edges[:15]:
            arrow = "→" if e["direction"] == "out" else "←"
            ev = f"  ({e['evidence']})" if e.get("evidence") else ""
            typer.echo(f"  {arrow} [{e['other']['id']}] {e['other']['type']}: {e['other']['name'][:80]}{ev}")


@app.command()
def data(accession: str) -> None:
    """How to download a dataset and which papers it is linked to."""
    info = _client().data(accession)
    if info is None:
        typer.echo(_("api.not_found"))
        raise typer.Exit(1)
    if _out(info):
        return
    typer.secho(f"{info['name']}  [{accession}]", bold=True)
    a = info.get("attrs") or {}
    typer.echo("  " + " · ".join(str(a[k]) for k in ("organism", "tissue", "modality", "scale") if a.get(k)))
    if info.get("download"):
        typer.echo(f"{_('cli.dataset_howto')}: {info['download']}")
    typer.echo(f"{_('cli.used_by')}:")
    for p in info["papers"]:
        typer.echo(f"  [{p['id']}] {p['edge']}: {p['name'][:90]}")


@app.command()
def recall(text: Optional[str] = typer.Argument(None), from_file: Optional[Path] = typer.Option(None),
           limit: Optional[int] = None) -> None:
    """Recall assets relevant to some text or to a script (imports + comments)."""
    c = _client()
    if from_file:
        res = c.recall_code(from_file.read_text(encoding="utf-8", errors="replace"), limit)
        hits = res["results"]
    else:
        hits = c.recall(text or sys.stdin.read(), limit)
    if _out(hits):
        return
    if not hits:
        typer.echo(_("cli.no_results"))
    for h in hits:
        typer.echo(_hit_line(h))


# ---- maintenance -----------------------------------------------------------------------------

@app.command()
def queue(kind: Optional[str] = None, limit: int = 20) -> None:
    """List the review queue."""
    q = _client().queue(kind, limit)
    if _out(q):
        return
    if not q["items"]:
        typer.echo(_("cli.queue_empty"))
    for it in q["items"]:
        p = it["payload"]
        desc = p.get("a_name") or p.get("name") or p.get("new_text") or ""
        other = p.get("b_name") or p.get("topic_name") or p.get("claim_text") or ""
        typer.echo(f"#{it['id']:<5} {it['kind']:<15} {it['score']:.2f}  {desc[:50]}  ⇄  {other[:50]}"
                   f"   [{' / '.join(it['actions'])}]")
    typer.echo(f"({q['total']})")


@app.command()
def decide(item_id: int, action: str, note: Optional[str] = None) -> None:
    """Resolve a review-queue item (e.g. `rhz decide 12 merge`)."""
    _out(_client().resolve(item_id, action, note)) or typer.echo("ok")


@app.command()
def review(limit: int = 20) -> None:
    """Spaced-repetition review in the terminal (FSRS)."""
    from .db.session import init_db, session_scope
    from .services.cards import due_cards, grade

    init_db()
    with session_scope() as s:
        cards = due_cards(s, limit)
    if not cards:
        typer.echo(_("cli.no_cards"))
        return
    typer.echo(_("cli.card_prompt"))
    for c in cards:
        typer.secho(f"\nQ: {c['q']}", bold=True)
        typer.prompt("…", default="", show_default=False)
        typer.echo(f"A: {c['a']}")
        r = typer.prompt(">", default="3")
        if r == "q":
            break
        if r == "s":
            continue
        with session_scope() as s:
            res = grade(s, c["id"], int(r))
        typer.echo(f"   → {res['interval_days']} d")


@app.command("topic")
def topic_add(name: str, definition: Optional[str] = typer.Option(None, "--definition", "-d"),
              example: list[str] = typer.Option([], "--example", "-e"),
              counter: list[str] = typer.Option([], "--not", help="counter-example"),
              parent: Optional[str] = None, no_retro: bool = False, k: Optional[int] = None) -> None:
    """Create a topic and retro-tag the library with it."""
    r = _client(prefer_http=True).create_topic(name=name, definition=definition, examples=example,
                                               counter_examples=counter, parent=parent, retro_tag=not no_retro, k=k)
    _out(r) or typer.echo(json.dumps(r, ensure_ascii=False))


@app.command()
def rebuild(no_backup: bool = False) -> None:
    """Recompute L2/L3 from L1 (after model / mapping / schema changes)."""
    from .db.session import init_db, session_scope
    from .pipeline.rebuild import rebuild as do_rebuild

    init_db()
    with session_scope() as s:
        summary = do_rebuild(s, backup=not no_backup)
    if _out(summary):
        return
    typer.echo(_("cli.rebuild_done", summary=json.dumps({k: v for k, v in summary.items()
                                                          if k not in ("warnings", "decisions_skipped")})))
    for w in summary.get("warnings", []):
        typer.secho(w, fg="yellow")


@app.command()
def nightly(force_synthesis: bool = False) -> None:
    """Run the nightly batch now (communities, topic promotion, weekly synthesis candidates)."""
    from . import jobs
    from .db.session import init_db, session_scope

    init_db()
    with session_scope() as s:
        j = jobs.enqueue(s, "nightly", {"force_synthesis": force_synthesis})
        jid = j.id
    jobs.run_all()
    from .db.models import Job

    with session_scope() as s:
        _out(jobs.job_view(s.get(Job, jid))) or typer.echo(json.dumps(s.get(Job, jid).result))


@app.command()
def digest(days: int = 7) -> None:
    """Weekly digest: unlinked cross-field pairs and new contradictions."""
    d = _client().digest(days)
    if _out(d):
        return
    for p in d["pairs"]:
        typer.echo(f"#{p['item_id']}  {p['a']['name'][:60]}  ⇄  {p['b']['name'][:60]}  ({p['score']:.2f})")
    for c in d["contradictions"]:
        typer.secho(f"!  {c['work']['name'][:60]}  ⟂  {c['claim']['name'][:60]}", fg="yellow")


@app.command()
def bench(spec: Path, k: int = 10) -> None:
    """Score retrieval against pre-registered benchmark questions (gate G2)."""
    from .db.session import init_db, session_scope
    from .services.bench import run

    init_db()
    with session_scope() as s:
        res = run(s, spec.read_text(encoding="utf-8"), k)
    if not _out(res):
        for r in res["questions"]:
            typer.echo(f"{r['rank'] or '-':>3}  {r['q'][:80]}")
        typer.echo(json.dumps(res["metrics"]) + ("  PASS" if res["passed"] else "  FAIL"))
    raise typer.Exit(0 if res["passed"] else 1)


@app.command()
def vocab(output: Path = typer.Option(Path("rhizome-vocab.yaml"), "-o")) -> None:
    """Export rhizome-vocab.yaml for the chat Project knowledge."""
    from .db.session import init_db, session_scope
    from .services.vocab import export_vocab

    init_db()
    with session_scope() as s:
        output.write_text(export_vocab(s), encoding="utf-8")
    typer.echo(_("cli.vocab_done", path=output))


@app.command()
def snapshot(path: Path) -> None:
    """Write a consistent read-only snapshot (SQLite file incl. vectors)."""
    from .services.snapshot import make_snapshot

    make_snapshot(get_settings(), path)
    typer.echo(_("cli.snapshot_done", path=path))


@app.command()
def sync(remote: str, dry_run: bool = False) -> None:
    """Push a snapshot to a remote configured in settings (reuses an SSH ControlMaster if set)."""
    from .services.snapshot import sync as do_sync

    st = get_settings()
    target = next((r for r in st.remotes if r.name == remote), None)
    if target is None:
        typer.echo(_("cli.unknown_remote", name=remote))
        raise typer.Exit(1)
    cmds = do_sync(st, target, dry_run=dry_run)
    if dry_run:
        for c in cmds:
            typer.echo(" ".join(c))
    else:
        typer.echo(_("cli.synced", remote=remote))


@app.command()
def backup() -> None:
    """Copy the database into the backups directory."""
    from .db.session import backup_database

    typer.echo(_("cli.backup_done", path=backup_database(get_settings(), tag="manual")))


@app.command()
def diag(output: Path = typer.Option(None, "-o")) -> None:
    """Export a diagnostic bundle (logs, settings without secrets, counts). No paper content."""
    from . import __version__
    from .db.session import init_db, session_scope
    from .services.views import home_stats

    st = get_settings()
    init_db()
    output = output or Path(f"rhizome-diag-{datetime.now():%Y%m%d-%H%M%S}.zip")
    with session_scope() as s:
        stats = home_stats(s)
    info = {"version": __version__, "python": sys.version, "platform": sys.platform, "stats": stats,
            "settings": st.model_dump(mode="json", exclude={"contact_email", "remotes"}),
            "remotes": [r.name for r in st.remotes]}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, indent=2, ensure_ascii=False, default=str))
        for log in sorted(st.logs_dir.glob("rhizome.log*")):
            z.write(log, f"logs/{log.name}")
    typer.echo(_("cli.diag_done", path=output))


# ---- rxf -------------------------------------------------------------------------------------------

@rxf_app.command("schema")
def rxf_schema(output: Optional[Path] = typer.Option(None, "-o")) -> None:
    """Print (or write) the RXF JSON Schema."""
    from .rxf.schema import json_schema

    text = json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)


@rxf_app.command("validate")
def rxf_validate(files: list[Path]) -> None:
    """Validate RXF files without ingesting them."""
    from .rxf.loader import load_rxf, report_for

    bad = 0
    for f in files:
        r = load_rxf(f.read_text(encoding="utf-8-sig"))
        if r.ok:
            typer.secho(f"OK  {f}", fg="green")
        else:
            bad += 1
            typer.echo(report_for(f.name, r))
    raise typer.Exit(1 if bad else 0)


@rxf_app.command("instructions")
def rxf_instructions(lang: Optional[str] = None) -> None:
    """Print the export instructions to paste into the Claude / ChatGPT Project."""
    from importlib import resources

    from .config import ui_language

    lang = lang or ui_language()
    name = "export-instructions.zh.md" if lang.startswith("zh") else "export-instructions.en.md"
    typer.echo(resources.files("rhizome").joinpath("data", name).read_text("utf-8"))


# ---- settings / models -------------------------------------------------------------------------

@settings_app.command("show")
def settings_show() -> None:
    typer.echo(json.dumps(get_settings().model_dump(mode="json"), indent=2, ensure_ascii=False))


@settings_app.command("set")
def settings_set(key: str, value: str) -> None:
    """Set a setting, e.g. `rhz settings set language zh_CN` or `thresholds.merge_auto 0.92`.
    `rhz settings set <key> default` goes back to the built-in default."""
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    if value == "default":
        parsed = None
    patch: dict[str, Any] = {}
    cur = patch
    parts = key.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = parsed
    update_settings(patch)
    typer.echo(f"{key} = {parsed!r}")


@models_app.command("download")
def models_download(name: str = typer.Argument(..., help="bge-m3 | bge-reranker-v2-m3 | mdeberta")) -> None:
    """Download an optional model into the data directory (never bundled with the installer)."""
    from .ml.hf import MODEL_IDS

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        typer.echo(_("cli.download_hint"))
        raise typer.Exit(1)
    path = snapshot_download(MODEL_IDS[name], cache_dir=str(get_settings().models_dir))
    typer.echo(path)


if __name__ == "__main__":  # pragma: no cover
    app()
