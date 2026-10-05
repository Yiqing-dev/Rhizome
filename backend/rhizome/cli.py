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

from .config import get_settings, load_settings, redact_url, set_overrides, set_settings, update_settings
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
def main(ctx: typer.Context, data_dir: Optional[Path] = typer.Option(None, envvar="RHIZOME_DATA_DIR", help="Data directory"),
         snapshot: Optional[Path] = typer.Option(None, envvar="RHIZOME_SNAPSHOT",
                                                 help="Read-only snapshot file (remote machines)"),
         as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
         lang: Optional[str] = typer.Option(None, help="Interface language: en | zh_CN")) -> None:
    if ctx.invoked_subcommand == "data-dir" and not data_dir:
        return  # must work when the library it points at is missing
    st = load_settings(data_dir) if data_dir else get_settings()
    set_settings(st)
    if lang:
        st = set_overrides(language=lang)
    _state["json"] = as_json
    _state["snapshot"] = snapshot
    from .logging_setup import setup_logging

    sub = ctx.invoked_subcommand
    if snapshot is None:
        setup_logging(st, role=sub if sub in ("serve", "mcp") else "cli")
    else:
        if sub in WRITE_COMMANDS:
            _require_writable()
        from .services.snapshot import snapshot_warnings

        for w in snapshot_warnings(snapshot, st):
            typer.secho(w, fg="yellow", err=True)


# Commands that change the library or need it to be the real one: refused in snapshot mode.
WRITE_COMMANDS = frozenset({"serve", "mcp", "watch", "ingest", "decide", "undo", "retract", "reject", "rename", "alias",
                            "merge", "topic", "rebuild", "nightly", "enrich", "snapshot", "sync", "data-dir",
                            "backups", "restore", "backup", "review", "gc", "edit", "recover"})


def _require_writable() -> None:
    if _state["snapshot"] is not None:
        typer.secho(_("cli.read_only"), fg="red", err=True)
        raise typer.Exit(2)


def _client(prefer_http: bool = True, create: bool = True):
    """``create=False`` for read commands: no library is an error with a hint, not a new empty one
    (on a cluster that usually means --snapshot / RHIZOME_SNAPSHOT was forgotten)."""
    from .client import NoLibrary, connect

    try:
        return connect(get_settings(), snapshot=_state["snapshot"], prefer_http=prefer_http, create=create)
    except NoLibrary as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from None
    except PermissionError as e:  # a write through a read-only snapshot
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from None


def _local_session():
    """In-process session for commands that read tables directly: the snapshot read-only, or the
    library (upgraded first)."""
    from .db.session import init_db, session_scope

    if _state["snapshot"] is not None:
        from .config import snapshot_settings

        return session_scope(snapshot_settings(_state["snapshot"]), read_only=True)
    init_db()
    return session_scope()


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

    import logging

    from . import __version__
    from .db.session import current_revision, get_engine

    log = logging.getLogger("rhizome.serve")
    application = create_app(watch_inbox=watch_inbox)
    url = f"http://{st.host}:{st.port}"
    db = st.data_dir / "rhizome.db"
    log.info("start: Rhizome %s, data %s, db %.1f MB rev %s, %s, frozen=%s, parent=%s", __version__, st.data_dir,
             db.stat().st_size / 1048576 if db.exists() else 0, current_revision(get_engine(st)), url,
             bool(getattr(sys, "frozen", False)), parent or "-")
    typer.echo(_("cli.serving", url=url))
    import os as _os

    application.state.enforce_host = True  # a browser on this machine: Host must be loopback
    # the desktop shell passes the token in and captures this output into a log: never echo it
    shown = url if _os.environ.get("RHIZOME_API_TOKEN") else f"{url}/#token={application.state.token}"
    typer.echo(_("cli.open_ui", url=shown))
    write_server_marker(url)  # lets the CLI / MCP server find this instance on a non-default port
    try:
        uvicorn.run(application, host=st.host, port=st.port, log_level="warning")
    finally:
        clear_server_marker()
        log.info("stop")


@app.command()
def mcp() -> None:
    """Run the MCP server on stdio (configured in Claude Desktop)."""
    from .mcp_server import main as mcp_main

    mcp_main()


@app.command()
def watch() -> None:
    """Watch the inbox and ingest RXF files as they arrive."""
    from .client import HttpClient
    from .db.session import init_db
    from .inbox import watch as do_watch

    if isinstance(_client(prefer_http=True), HttpClient):  # the app already watches this inbox
        typer.secho(_("cli.watch_app_running"), fg="yellow")
        raise typer.Exit(0)
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
    if r.get("replaced"):
        typer.secho(_("cli.replaced", ids=", ".join(f"#{i}" for i in r["replaced"])), fg="yellow")
    elif r.get("existing_exports"):
        typer.secho(_("cli.existing_exports", ids=", ".join(f"#{i}" for i in r["existing_exports"])), fg="yellow")
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
                                                    "the fix is recorded in L1)"),
           replace: bool = typer.Option(False, help="This export supersedes the paper's earlier ones")) -> None:
    """Ingest RXF files (a same-named .pdf is attached automatically)."""
    if move:
        from .db.session import init_db
        from .pipeline.ingest import ingest_file

        init_db()
        for f in files:
            _print_ingest(f.name, ingest_file(f, repair=repair, replace=replace).to_dict())
        return
    from .pipeline.ingest import read_inbox_file
    from .rxf.loader import RxfEncodingError

    c = _client()
    results = []
    for f in files:
        try:
            text, pdf = read_inbox_file(f)
        except RxfEncodingError as e:
            r = {"ok": False, "report": f"{f.name}: {e}"}
            results.append(r)
            typer.secho(r["report"], fg="red", err=True)
            continue
        r = c.ingest(text, f.name, pdf, repair=repair, replace=replace)
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
           limit: int = 20, offset: int = 0) -> None:
    """Hybrid search over assets."""
    hits = _client(create=False).search(q, types=type, organism=organism, modality=modality, year_min=year_min,
                                        year_max=year_max, edge_type=edge_type, limit=limit, offset=offset)
    if _out(hits):
        return
    if not hits:
        typer.echo(_("cli.no_results"))
    for h in hits:
        typer.echo(_hit_line(h))


@app.command()
def get(ref: str) -> None:
    """Show a paper or asset card by id or key."""
    c = _client(create=False)
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
    info = _client(create=False).data(accession)
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
    c = _client(create=False)
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
    q = _client(create=False).queue(kind, limit)
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


# ---- corrections (human decisions; every one can be undone with `rhz undo ID`) ---------------------

def _decide(op: str, payload: dict[str, Any]) -> None:
    r = _client().decide(op, payload)
    if _out(r):
        return
    if r.get("ok") is False:
        typer.secho(str(r.get("error")), fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(_("cli.decided", id=r["id"], op=r["op"]))


@app.command()
def decisions(limit: int = 30) -> None:
    """Your decisions, newest first (merges, renames, rejections, ...); `rhz undo ID` reverts one."""
    rows = _client(create=False).decisions(limit)["decisions"]
    if _out(rows):
        return
    for d in rows:
        mark = " (undone)" if d["revoked_at"] else ""
        typer.echo(f"#{d['id']:<5} {d['created_at'][:16]}  {d['op']:<13} {json.dumps(d['payload'], ensure_ascii=False)[:100]}{mark}")


@app.command()
def undo(decision_id: int) -> None:
    """Revert a decision; the graph is rebuilt without it."""
    r = _client().revoke(decision_id)
    if _out(r):
        return
    if r.get("ok") is False:
        typer.secho(_("api.not_found"), fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(_("cli.undone", id=decision_id) if r.get("changed") else _("cli.undo_noop", id=decision_id))


@app.command()
def retract(work: str) -> None:
    """Withdraw a paper's exports (e.g. a wrong or duplicate export); undo with `rhz undo`."""
    _decide("retract", {"work": work})


@app.command()
def reject(key: str) -> None:
    """Hide a wrong asset (e.g. a hallucinated dataset) everywhere; undo with `rhz undo`."""
    _decide("reject_entity", {"key": key})


@app.command()
def rename(key: str, name: str) -> None:
    """Rename an entity (the old name stays an alias)."""
    _decide("rename", {"key": key, "name": name})


@app.command()
def alias(key: str, alias_text: str, lang: Optional[str] = None) -> None:
    """Add an alias (another name it should be found by)."""
    _decide("add_alias", {"key": key, "alias": alias_text, **({"lang": lang} if lang else {})})


@app.command()
def edit(key: str, text: str) -> None:
    """Rewrite one of your insights (or any idea / claim) in your own words; the export keeps the
    original and `rhz undo` restores it."""
    _decide("edit_text", {"key": key, "text": text})


@app.command()
def merge(source: str, into: str) -> None:
    """Merge one entity into another (same type); undo with `rhz undo`."""
    _decide("merge", {"from": source, "into": into})


@app.command()
def review(limit: int = 20) -> None:
    """Spaced-repetition review in the terminal (FSRS)."""
    from .db.session import init_db, session_scope
    from .services.cards import due_cards, grade, suspend

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
        if r == "k":
            continue
        if r in ("s", "S"):  # S: never make cards for this asset again
            with session_scope() as s:
                suspend(s, c["id"], entity=r == "S")
            typer.echo(_("cli.card_suspended"))
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
def rebuild(no_backup: bool = False,
            force: bool = typer.Option(False, help="Rebuild even if some stored exports cannot be replayed")) -> None:
    """Recompute L2/L3 from L1 (after model / mapping / schema changes). Stored exports are checked
    first; one the current version cannot read stops the rebuild unless --force (it is then listed
    and left out)."""
    from .client import HttpClient

    c = _client(prefer_http=True)
    if isinstance(c, HttpClient):  # the app is running: its worker does it, no second writer
        j = c.run_job("rebuild", {"backup": not no_backup, "force": force})
        if j.get("status") != "done":
            typer.secho(j.get("error") or j.get("status"), fg="red", err=True)
            raise typer.Exit(1)
        summary = j["result"]
    else:
        from .db.session import init_db, session_scope
        from .pipeline.rebuild import RebuildAborted
        from .pipeline.rebuild import rebuild as do_rebuild

        init_db()
        try:
            with session_scope() as s:
                summary = do_rebuild(s, backup=not no_backup, force=force)
        except RebuildAborted as e:
            typer.secho(_("cli.rebuild_aborted", n=len(e.failures)), fg="red", err=True)
            for f in e.failures:
                typer.echo(f"  #{f['extraction_id']}  {f['work_key']}  {f['error']}", err=True)
            raise typer.Exit(1) from None
    if _out(summary):
        raise typer.Exit(1 if summary.get("failed") else 0)
    typer.echo(_("cli.rebuild_done", summary=json.dumps({k: v for k, v in summary.items()
                                                          if k not in ("warnings", "decisions_skipped", "failed")})))
    for w in summary.get("warnings", []):
        typer.secho(w, fg="yellow")
    for f in summary.get("failed", []):
        typer.secho(f"  #{f['extraction_id']}  {f['work_key']}  {f['error']}", fg="red", err=True)
    if summary.get("failed"):
        raise typer.Exit(1)


@app.command()
def nightly(force_synthesis: bool = False) -> None:
    """Run the nightly batch now (communities, topic promotion, weekly synthesis candidates)."""
    j = _client(prefer_http=True).run_job("nightly", {"force_synthesis": force_synthesis})
    _out(j) or typer.echo(json.dumps(j.get("result") if j.get("status") == "done" else j, ensure_ascii=False))


@app.command()
def enrich(limit: int = 200) -> None:
    """Fetch OpenAlex metadata (abstract, venue, references) for papers whose lookup failed at
    ingest time (offline, rate limit, outage)."""
    j = _client(prefer_http=True).run_job("enrich", {"limit": limit})
    _out(j) or typer.echo(json.dumps(j.get("result") if j.get("status") == "done" else j, ensure_ascii=False))


@app.command()
def doctor() -> None:
    """Check the network path to OpenAlex, NCBI and GitHub (proxy, certificates, rate limits)."""
    import os

    from .external.verify import get

    rows = []
    for name, url in (("OpenAlex", "https://api.openalex.org/works?per-page=1"),
                      ("NCBI E-utilities", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json"),
                      ("GitHub API", "https://api.github.com/rate_limit"),
                      ("BioStudies", "https://www.ebi.ac.uk/biostudies/api/v1/search?pageSize=1")):
        try:
            r = get(url, retries=0)
            rows.append({"service": name, "ok": r.status_code < 400, "status": r.status_code})
        except Exception as e:  # noqa: BLE001
            rows.append({"service": name, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
    proxy = {k: v for k, v in os.environ.items() if k.lower() in ("https_proxy", "http_proxy", "no_proxy")}
    try:
        import truststore  # noqa: F401

        certs = "system certificate store"
    except ImportError:
        certs = "certifi bundle"
    out = {"offline_setting": get_settings().offline, "proxy": proxy, "certificates": certs, "checks": rows}
    if _out(out):
        return
    typer.echo(f"offline: {out['offline_setting']}   proxy: {proxy or '-'}   certificates: {certs}")
    for r in rows:
        typer.secho(f"  {'OK ' if r['ok'] else 'FAIL'} {r['service']:<18} {r.get('status') or r.get('error')}",
                    fg="green" if r["ok"] else "red")


@app.command()
def digest(days: int = 7) -> None:
    """Weekly digest: unlinked cross-field pairs and new contradictions."""
    d = _client(create=False).digest(days)
    if _out(d):
        return
    for p in d["pairs"]:
        typer.echo(f"#{p['item_id']}  {p['a']['name'][:60]}  ⇄  {p['b']['name'][:60]}  ({p['score']:.2f})")
    for c in d["contradictions"]:
        typer.secho(f"!  {c['work']['name'][:60]}  ⟂  {c['claim']['name'][:60]}", fg="yellow")


@app.command()
def bench(spec: Path, k: int = 10) -> None:
    """Score retrieval against pre-registered benchmark questions (gate G2)."""
    from .services.bench import run

    with _local_session() as s:
        res = run(s, spec.read_text(encoding="utf-8"), k)
    if not _out(res):
        for r in res["questions"]:
            typer.echo(f"{r['rank'] or '-':>3}  {r['q'][:80]}")
        typer.echo(json.dumps(res["metrics"]) + ("  PASS" if res["passed"] else "  FAIL"))
    raise typer.Exit(0 if res["passed"] else 1)


@app.command()
def vocab(output: Path = typer.Option(Path("rhizome-vocab.yaml"), "-o"),
          candidates: bool = typer.Option(True, help="Include candidate topics that at least one paper is on")) -> None:
    """Export rhizome-vocab.yaml for the chat Project knowledge."""
    from .services.vocab import export_vocab

    with _local_session() as s:
        output.write_text(export_vocab(s, include_candidates=candidates), encoding="utf-8")
    typer.echo(_("cli.vocab_done", path=output))


@app.command()
def snapshot(path: Path) -> None:
    """Write a consistent read-only snapshot (SQLite file incl. vectors)."""
    from .services.snapshot import make_snapshot

    make_snapshot(get_settings(), path)
    typer.echo(_("cli.snapshot_done", path=path))


@app.command()
def sync(remote: str, dry_run: bool = False) -> None:
    """Push a snapshot to a remote configured in settings, in one SSH session (one password / 2FA
    prompt; reuses an SSH ControlMaster if set)."""
    from .services.snapshot import SyncError, sync_warnings
    from .services.snapshot import sync as do_sync

    st = get_settings()
    target = next((r for r in st.remotes if r.name == remote), None)
    if target is None:
        typer.echo(_("cli.unknown_remote", name=remote))
        raise typer.Exit(1)
    for w in sync_warnings(target):
        typer.secho(w, fg="yellow", err=True)
    try:
        cmds = do_sync(st, target, dry_run=dry_run)
    except SyncError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1) from None
    if dry_run:
        for c in cmds:
            typer.echo(" ".join(c))
    else:
        typer.echo(_("cli.synced", remote=remote))
        typer.echo(_("cli.synced_hint", path=target.path))


@app.command("data-dir")
def data_dir_cmd(reset: bool = typer.Option(False, "--reset", help="Go back to the default location")) -> None:
    """Show where the library is (and why), or go back to the default location."""
    from .config import POINTER_FILE, data_dir_source, platform_data_dir, set_data_dir_pointer

    if reset:
        set_data_dir_pointer(None)
        typer.echo(str(platform_data_dir()))
        return
    source, path = data_dir_source()
    typer.echo(f"{path}  ({source}; pointer: {platform_data_dir() / POINTER_FILE})")


@app.command()
def backups() -> None:
    """List backups (newest first) with their schema revision."""
    from datetime import datetime as dt

    from .db.session import backup_revision, list_backups

    rows = [{"file": str(p), "modified": dt.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
             "mb": round(p.stat().st_size / 1048576, 1), "revision": backup_revision(p)}
            for p in list_backups(get_settings())]
    if _out(rows):
        return
    for r in rows:
        typer.echo(f"{r['modified']}  {r['mb']:>7} MB  rev {r['revision']}  {r['file']}")


@app.command()
def restore(backup_file: Path, yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask")) -> None:
    """Replace the library with a backup (the app and Claude Desktop must be closed)."""
    from .db.session import RestoreError, restore_database

    if not yes and not typer.confirm(_("cli.restore_confirm", backup=str(backup_file))):
        raise typer.Exit(1)
    try:
        pre = restore_database(get_settings(), backup_file)
    except RestoreError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from None
    typer.echo(_("cli.restore_done", pre=str(pre)))


@app.command()
def backup() -> None:
    """Copy the database into the backups directory."""
    from .db.session import backup_database

    typer.echo(_("cli.backup_done", path=backup_database(get_settings(), tag="manual")))


@app.command()
def diag(output: Path = typer.Option(None, "-o")) -> None:
    """Export a diagnostic bundle (logs, settings without secrets, counts). No paper content."""
    import sqlite3

    from . import __version__
    from .services.views import home_stats

    st = get_settings()
    output = output or Path(f"rhizome-diag-{datetime.now():%Y%m%d-%H%M%S}.zip")
    from . import rawstore

    with _local_session() as s:
        stats = home_stats(s)
        raw = rawstore.check(s) if _state["snapshot"] is None else None
    info = {"version": __version__, "python": sys.version, "platform": sys.platform,
            "sqlite": sqlite3.sqlite_version, "snapshot": str(_state["snapshot"] or ""), "stats": stats, "raw": raw,
            "settings": {**st.model_dump(mode="json", exclude={"contact_email", "remotes"}),
                         "database_url": redact_url(st.database_url)},
            "remotes": [r.name for r in st.remotes]}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, indent=2, ensure_ascii=False, default=str))
        for log in sorted(st.logs_dir.glob("rhizome*.log*")):
            z.write(log, f"logs/{log.name}")
    typer.echo(f"Rhizome {__version__} · Python {sys.version.split()[0]} · SQLite {sqlite3.sqlite_version}")
    if raw and not raw["ok"]:
        typer.secho(_("cli.raw_problems", missing=len(raw["missing"]), empty=len(raw["empty"]),
                      tmp=len(raw["stray_tmp"])), fg="yellow")
    typer.echo(_("cli.diag_done", path=output))


@app.command()
def recover(from_raw: bool = typer.Option(False, "--from-raw", help="Replay the raw/ folder into this library")) -> None:
    """Rebuild a library from its raw/ folder alone (the captured exports, PDFs and metadata
    responses), e.g. into a fresh data dir after the database was lost. Offline; keeps the
    original capture times; skips captures the database already has."""
    from .db.session import init_db
    from .pipeline.ingest import recover_from_raw

    _require_writable()
    if not from_raw:
        typer.echo(_("cli.recover_usage"))
        raise typer.Exit(2)
    init_db()
    from .db.session import session_scope

    with session_scope() as s:
        out = recover_from_raw(s)
    if _out(out):
        return
    typer.echo(_("cli.recover_done", **{k: out[k] for k in ("recovered", "already_present", "failed")}))
    for pr in out["problems"]:
        typer.secho(f"  {pr['sha'][:12]}  {pr['error']}", fg="red", err=True)
    if out["failed"]:
        raise typer.Exit(1)


@app.command()
def gc() -> None:
    """Compact the library: drop cached vectors nothing uses any more and reclaim the space."""
    from sqlalchemy import text as sql

    from .db.session import init_db, session_scope
    from .pipeline.graph import prune_vector_cache

    _require_writable()
    init_db()
    with session_scope() as s:
        pruned = prune_vector_cache(s)
    with session_scope() as s:
        before = s.execute(sql("select page_count * page_size from pragma_page_count(), pragma_page_size()")).scalar_one()
        s.commit()
        s.connection().exec_driver_sql("COMMIT")  # VACUUM needs no open transaction
        s.connection().exec_driver_sql("VACUUM")
        after = s.execute(sql("select page_count * page_size from pragma_page_count(), pragma_page_size()")).scalar_one()
    _out({"vector_cache_pruned": pruned, "bytes_before": before, "bytes_after": after}) or typer.echo(
        _("cli.gc_done", pruned=pruned, mb=round((before - after) / 1e6, 1)))


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
    from .rxf.guide import instructions

    typer.echo(instructions(lang))


# ---- settings / models -------------------------------------------------------------------------

@settings_app.command("show")
def settings_show() -> None:
    typer.echo(json.dumps(get_settings().model_dump(mode="json"), indent=2, ensure_ascii=False))


@settings_app.command("set")
def settings_set(key: str, value: str) -> None:
    """Set a setting, e.g. `rhz settings set language zh_CN` or `thresholds.merge_auto 0.92`.
    `rhz settings set <key> default` goes back to the built-in default."""
    _require_writable()
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
    from .client import HttpClient

    from .api.app import SettingsPatch

    c = _client(prefer_http=True)
    live = isinstance(c, HttpClient)
    try:
        if live and parts[0] in SettingsPatch.model_fields:
            c.patch_settings(patch)  # the running app writes it and uses it right away
        else:
            update_settings(patch)
            if live:
                typer.secho(_("cli.restart_app_for_setting", key=key), fg="yellow")
    except ValueError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from None
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


def run() -> None:
    """Console entry point (`rhz`, rhz.exe): known user-facing failures print their message, not a
    traceback."""
    from .config import LibraryNotFound
    from .db.session import SchemaTooNew

    try:
        app(prog_name="rhz")
    except SchemaTooNew as e:
        typer.secho(str(e), fg="red", err=True)
        raise SystemExit(3) from None
    except LibraryNotFound as e:  # the desktop shell offers Retry / Use default on this exit code
        typer.secho(str(e), fg="red", err=True)
        raise SystemExit(4) from None
