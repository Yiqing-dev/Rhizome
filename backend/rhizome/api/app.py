# SPDX-License-Identifier: Apache-2.0
"""REST API. Every capability is here first; the UI, MCP server and CLI are thin clients."""

from __future__ import annotations

import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import __version__, jobs
from ..config import Settings, get_settings, set_settings, settings_problems, token_path, ui_language, update_settings
from ..db.models import EDGE_TYPES, Entity, HumanDecision, Job
from ..db.session import session_scope
from ..i18n import _
from ..services import datasets as dataset_service

log = logging.getLogger(__name__)


# ---- auth ----------------------------------------------------------------------------

def get_or_create_token(settings: Settings) -> str:
    """The desktop shell generates the token and passes it in RHIZOME_API_TOKEN; it is written to the
    data dir as well so the CLI and the MCP server (other processes) can reach the running app."""
    p = token_path(settings)
    env = os.environ.get("RHIZOME_API_TOKEN")
    if not env and p.exists():
        return p.read_text("utf-8-sig").strip()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    tok = env or secrets.token_urlsafe(32)
    p.write_text(tok, "utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:  # pragma: no cover - Windows ACLs
        pass
    return tok


# ---- request models -------------------------------------------------------------------

class IngestBody(BaseModel):
    text: str
    filename: str = "inline.yaml"
    repair: bool = False  # apply the known-drift fixes (recorded in L1), never done silently
    replace: bool = False  # this export supersedes the paper's earlier ones (they are retracted)


class InboxRetryBody(BaseModel):
    repair: bool = False


class RecallBody(BaseModel):
    text: str
    limit: int | None = None


class TopicBody(BaseModel):
    name: str
    definition: str | None = None
    examples: list[str] = Field(default_factory=list)
    counter_examples: list[str] = Field(default_factory=list)
    parent: str | None = None
    aliases: list[str] = Field(default_factory=list)
    retro_tag: bool = True
    k: int | None = None


class DecisionBody(BaseModel):
    op: str
    payload: dict[str, Any]


class ResolveBody(BaseModel):
    action: str
    note: str | None = None


class GradeBody(BaseModel):
    rating: int = Field(ge=1, le=4)


class SuspendBody(BaseModel):
    suspended: bool = True
    entity: bool = False  # also: never make cards for this asset again


class DismissBody(BaseModel):
    kind: str
    topic: str | None = None


class JobBody(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SettingsPatch(BaseModel):
    language: str | None = None
    offline: bool | None = None
    contact_email: str | None = None
    inbox_dir: str | None = None
    embedder: str | None = None
    reranker: str | None = None
    nli: str | None = None
    inference_backend: str | None = None
    local_llm_path: str | None = None
    backup_dir: str | None = None
    backup_keep_daily: int | None = None
    review_daily_new: int | None = None
    review_daily_max: int | None = None
    remotes: list[dict[str, Any]] | None = None
    thresholds: dict[str, Any] | None = None


# ---- app -----------------------------------------------------------------------------

def create_app(settings: Settings | None = None, read_only: bool = False, start_worker: bool = True,
               token: str | None = None, watch_inbox: bool = False) -> FastAPI:
    if settings is not None:
        set_settings(settings)
    st = get_settings()
    if not read_only:
        st.ensure_dirs()
        from ..db.session import init_db

        init_db(st)
    api_token = token or get_or_create_token(st)
    worker: jobs.Worker | None = None
    inbox_stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal worker
        stop = inbox_stop
        if start_worker and not read_only:
            worker = jobs.Worker()
            worker.start()
        if watch_inbox and not read_only:
            from ..inbox import watch

            threading.Thread(target=watch, kwargs={"stop": stop}, daemon=True, name="rhizome-inbox").start()
        if not read_only:  # the first search should not pay for loading the vector index
            from ..pipeline.graph import VECTORS

            threading.Thread(target=VECTORS.preload, args=(settings,), daemon=True, name="rhizome-preload").start()
        yield
        stop.set()
        if worker:
            worker.stop()

    app = FastAPI(title="Rhizome", version=__version__, lifespan=lifespan)

    from fastapi.responses import JSONResponse

    from ..ml import ModelUnavailable

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """uvicorn prints unhandled errors to stderr only; put them in the log with an id the
        user can quote, and answer with that id instead of a bare 500."""
        rid = secrets.token_hex(4)
        log.exception("request %s %s failed [%s]", request.method, request.url.path, rid)
        return JSONResponse(status_code=500, content={"detail": _("api.internal_error", id=rid), "request_id": rid})

    @app.exception_handler(ModelUnavailable)
    async def _model_unavailable(request: Request, exc: ModelUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "model_unavailable": True})
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                                                      "tauri://localhost", "http://tauri.localhost"],
                       allow_methods=["*"], allow_headers=["*"])

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else request.headers.get("x-rhizome-token")
        if not supplied or not secrets.compare_digest(supplied, api_token):
            raise HTTPException(401, _("api.unauthorized"))

    def db() -> Iterator[Session]:
        with session_scope(read_only=read_only) as s:
            yield s

    def writable(request: Request) -> None:
        if read_only:
            raise HTTPException(403, _("api.read_only"))
        if getattr(app.state, "moved_to", None):  # the library was copied elsewhere: no more writes here
            raise HTTPException(409, _("api.library_moved", path=app.state.moved_to))
        # During a rebuild the write lock is held for a long time: answer at once instead of
        # making the caller wait for the 30 s busy timeout and then fail. Queueing jobs and the
        # desktop-integration endpoints don't need the lock.
        path = request.url.path
        if path.startswith(("/jobs", "/system")):
            return
        with session_scope(read_only=False) as s:
            busy = jobs.running(s, "rebuild")
        if busy:
            raise HTTPException(503, _("api.rebuilding"), headers={"Retry-After": "30"})

    A = [Depends(auth)]
    W = [Depends(auth), Depends(writable)]

    @app.get("/health")
    def health() -> dict[str, Any]:
        moved = getattr(app.state, "moved_to", None)
        return {"ok": True, "version": __version__, "read_only": read_only or bool(moved), "moved_to": moved,
                "language": ui_language()}

    # ---- ingest ----
    @app.post("/ingest", dependencies=W)
    async def ingest(request: Request, s: Session = Depends(db)) -> dict[str, Any]:
        from ..pipeline.ingest import ingest_text

        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/"):
            form = await request.form()
            f = form.get("file")
            if f is None or isinstance(f, str):
                raise HTTPException(422, "file is required")
            text = (await f.read()).decode("utf-8-sig")
            pdf_part = form.get("pdf")
            pdf = await pdf_part.read() if pdf_part is not None and not isinstance(pdf_part, str) else None
            filename = f.filename or "upload.yaml"
            repair = str(form.get("repair", "")).lower() in ("1", "true", "yes")
            replace = str(form.get("replace", "")).lower() in ("1", "true", "yes")
        else:
            body = IngestBody.model_validate(await request.json())
            text, filename, pdf, repair, replace = body.text, body.filename, None, body.repair, body.replace
        # network lookups and model inference are blocking: keep them off the event loop
        res = await run_in_threadpool(lambda: ingest_text(s, text, filename, pdf, repair=repair, replace=replace))
        if not res.ok:
            raise HTTPException(422, res.to_dict())
        return res.to_dict()

    # ---- inbox files that failed (error/) ----
    def _failed_file(name: str) -> Path:
        err = get_settings().inbox / "error"
        p = err / name
        if Path(name).name != name or not p.is_file() or name.endswith(".error.txt"):
            raise HTTPException(404, "no such file")
        return p

    @app.get("/inbox/failed", dependencies=A)
    def inbox_failed() -> dict[str, Any]:
        from ..pipeline.ingest import read_inbox_file
        from ..rxf.loader import load_rxf

        err = get_settings().inbox / "error"
        files = []
        for p in sorted(err.iterdir(), key=lambda q: q.stat().st_mtime, reverse=True) if err.is_dir() else []:
            if not p.is_file() or p.name.endswith(".error.txt") or p.suffix.lower() == ".pdf":
                continue
            rep = p.with_name(p.name + ".error.txt")
            try:
                repairable = load_rxf(read_inbox_file(p)[0]).repairable
            except (OSError, UnicodeDecodeError):
                repairable = []
            files.append({"name": p.name, "report": rep.read_text("utf-8") if rep.exists() else None,
                          "repairable": repairable, "has_pdf": p.with_suffix(".pdf").exists(),
                          "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()})
        return {"files": files}

    @app.post("/inbox/failed/{name}", dependencies=W)
    async def inbox_retry(name: str, body: InboxRetryBody) -> dict[str, Any]:
        """Import a failed file again (after editing it, or with repair=true); it moves to done/ on success."""
        from ..pipeline.ingest import ingest_file

        res = await run_in_threadpool(ingest_file, _failed_file(name), body.repair)
        if not res.ok:
            raise HTTPException(422, res.to_dict())
        return res.to_dict()

    # ---- search / entities ----
    @app.get("/search", dependencies=A)
    def search(q: str = "", types: str | None = None, edge_type: str | None = None, organism: str | None = None,
               modality: str | None = None, year_min: int | None = None, year_max: int | None = None,
               tier: int | None = None, topic: int | None = None, limit: int = Query(20, le=100),
               offset: int = 0, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.search import DEFAULT_TYPES, Filters, search as do_search

        f = Filters(types=tuple(types.split(",")) if types else DEFAULT_TYPES, edge_type=edge_type,
                    organism=organism, modality=modality, year_min=year_min, year_max=year_max, tier=tier,
                    topic=topic)
        return {"results": do_search(s, q, f, limit=limit, offset=offset)}

    # touch=false: a machine reader (Claude's rhz_get) looking something up is not you seeing it,
    # so it must not reset the forgetting clock recall relies on
    @app.get("/entity/by-key", dependencies=A)
    def entity_by_key(key: str, touch: bool = True, s: Session = Depends(db)) -> dict[str, Any]:
        from ..pipeline.graph import Graph
        from ..services.views import entity_card

        e = Graph(s).by_key(key)
        if e is None:
            raise HTTPException(404, _("api.not_found"))
        return entity_card(s, e.id, touch_access=touch and not read_only)

    @app.get("/entity/{entity_id}", dependencies=A)
    def entity(entity_id: int, touch: bool = True, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import entity_card

        card = entity_card(s, entity_id, touch_access=touch and not read_only)
        if card is None:
            raise HTTPException(404, _("api.not_found"))
        return card

    @app.get("/entity/{entity_id}/neighbors", dependencies=A)
    def neighbors(entity_id: int, hops: int = 1, edge_types: str | None = None, limit: int = Query(300, le=300),
                  offset: int = 0, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import neighbors as nb

        types = [t for t in edge_types.split(",") if t in EDGE_TYPES] if edge_types else None
        return nb(s, entity_id, hops=hops, edge_types=types, limit=limit, offset=offset)

    @app.get("/entity/{entity_id}/related", dependencies=A)
    def related(entity_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.recall import related_to_work, recall
        from ..pipeline.graph import entity_text

        e = s.get(Entity, entity_id)
        if e is None:
            raise HTTPException(404, _("api.not_found"))
        if e.type == "work":
            return {"related": related_to_work(s, entity_id)}
        return {"related": [h for h in recall(s, entity_text(e), limit=8, min_relevance=0.0) if h["id"] != e.id]}

    @app.get("/entity/{entity_id}/edges", dependencies=A)
    def entity_edges(entity_id: int, type: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                     s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import entity_edges as ee

        return ee(s, entity_id, type, offset, limit)

    # ---- topics ----
    @app.get("/topic/{topic_id}/assets", dependencies=A)
    def topic_assets(topic_id: int, role: str | None = None, column: str | None = None,
                     offset: int = Query(0, ge=0), limit: int = Query(150, ge=1, le=500),
                     s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import topic_assets as ta

        out = ta(s, topic_id, role=role, column=column, offset=offset, limit=limit)
        if out is None:
            raise HTTPException(404, _("api.not_found"))
        return out

    @app.get("/topic/{topic_id}/changes", dependencies=A)
    def topic_changes(topic_id: int, since: str | None = None, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import topic_changes as tc

        since_dt = datetime.fromisoformat(since) if since else datetime.utcnow() - timedelta(days=30)
        return tc(s, topic_id, since_dt)

    @app.get("/topic-map", dependencies=A)
    def topic_map(include_candidates: bool = False, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import topic_map as tm

        return tm(s, include_candidates)

    @app.post("/topic", dependencies=W)
    def create_topic(body: TopicBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..pipeline import decisions
        from ..pipeline.canonicalize import free_key
        from ..pipeline.graph import Graph

        g = Graph(s)
        payload = body.model_dump(exclude={"retro_tag", "k"})
        d = decisions.record(g, "create_topic", payload)
        topic = g.by_alias("topic", body.name) or g.by_key(free_key("topic", body.name))
        job = jobs.enqueue(s, "retro_tag", {"topic": topic.key, "k": body.k}) if body.retro_tag else None
        return {"topic_id": topic.id, "key": topic.key, "decision_id": d.id, "job_id": job.id if job else None}

    @app.get("/rxf/instructions", dependencies=A, response_class=PlainTextResponse)
    def rxf_instructions(lang: str | None = None) -> str:
        from ..rxf.guide import instructions

        return instructions(lang)

    @app.get("/vocab", dependencies=A, response_class=PlainTextResponse)
    def vocab(include_candidates: bool = False, s: Session = Depends(db)) -> str:
        from ..services.vocab import export_vocab

        return export_vocab(s, include_candidates)

    # ---- review queue & decisions ----
    @app.get("/review", dependencies=A)
    def review(kind: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
               s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.review import list_items

        return list_items(s, kind, limit, offset)

    @app.post("/review/dismiss", dependencies=W)
    def dismiss_items(body: DismissBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.review import dismiss

        return {"dismissed": dismiss(s, body.kind, body.topic)}

    # declared after /review/dismiss: a literal path must not be read as an item id
    @app.post("/review/{item_id}", dependencies=W)
    def resolve(item_id: int, body: ResolveBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.review import resolve as do_resolve

        try:
            return do_resolve(s, item_id, body.action, body.note)
        except LookupError:
            raise HTTPException(404, _("api.not_found"))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/decision", dependencies=W)
    def decision(body: DecisionBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..pipeline import decisions
        from ..pipeline.graph import Graph

        try:
            d = decisions.record(Graph(s), body.op, body.payload)
        except decisions.DecisionError as e:
            raise HTTPException(422, str(e))
        job = jobs.enqueue(s, "rebuild", {}).id if body.op in decisions.NEEDS_REBUILD else None
        return {"id": d.id, "op": d.op, "rebuild_job": job}

    @app.get("/decisions", dependencies=A)
    def list_decisions(limit: int = 100, offset: int = 0, s: Session = Depends(db)) -> dict[str, Any]:
        from sqlalchemy import select

        rows = s.execute(select(HumanDecision).order_by(HumanDecision.id.desc()).offset(offset).limit(limit)).scalars()
        return {"decisions": [{"id": d.id, "op": d.op, "payload": d.payload, "created_at": d.created_at.isoformat(),
                               "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None} for d in rows]}

    @app.delete("/decision/{decision_id}", dependencies=W)
    def revoke(decision_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        from ..pipeline import decisions
        from ..pipeline.graph import Graph

        try:
            changed = decisions.revoke(Graph(s), decision_id)
        except decisions.DecisionError:
            raise HTTPException(404, _("api.not_found"))
        job = jobs.enqueue(s, "rebuild", {}) if changed else None  # coalesced with a queued one
        return {"revoked": decision_id, "changed": changed, "rebuild_job": job.id if job else None}

    # ---- recall / digest / data ----
    @app.post("/recall", dependencies=A)
    def recall(body: RecallBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.recall import recall as do_recall

        return {"results": do_recall(s, body.text, body.limit)}

    @app.post("/recall/code", dependencies=A)
    def recall_code(body: RecallBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.recall import context_from_code, recall as do_recall

        ctx = context_from_code(body.text)
        return {"context": ctx, "results": do_recall(s, ctx, body.limit)}

    @app.get("/digest", dependencies=A)
    def digest(days: int = 7, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.synthesis import weekly_digest

        return weekly_digest(s, days)

    @app.get("/data/{accession}", dependencies=A)
    def data(accession: str, s: Session = Depends(db)) -> dict[str, Any]:
        out = dataset_service.dataset_info(s, accession)
        if out is None:
            raise HTTPException(404, _("api.not_found"))
        return out

    @app.get("/stats", dependencies=A)
    def stats(s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import home_stats

        return home_stats(s)

    # ---- review cards (FSRS) ----
    @app.get("/cards/due", dependencies=A)
    def cards_due(limit: int = 20, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.cards import due_cards

        return {"cards": due_cards(s, limit)}

    @app.post("/cards/{card_id}/suspend", dependencies=W)
    def suspend_card(card_id: str, body: SuspendBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.cards import suspend as do_suspend

        try:
            return do_suspend(s, card_id, body.suspended, entity=body.entity)
        except LookupError:
            raise HTTPException(404, _("api.not_found"))

    @app.post("/cards/{card_id}/grade", dependencies=W)
    def grade(card_id: str, body: GradeBody, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.cards import grade as do_grade

        try:
            return do_grade(s, card_id, body.rating)
        except LookupError:
            raise HTTPException(404, _("api.not_found"))

    # ---- jobs ----
    @app.post("/jobs", dependencies=W)
    def create_job(body: JobBody, s: Session = Depends(db)) -> dict[str, Any]:
        try:
            return jobs.job_view(jobs.enqueue(s, body.kind, body.payload))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/synthesis/threshold", dependencies=W)
    def reset_synthesis_threshold(s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.synthesis import reset_threshold

        return {"threshold": reset_threshold(s)}

    @app.get("/jobs", dependencies=A)
    def list_jobs(active: bool = False, limit: int = Query(20, le=200), s: Session = Depends(db)) -> dict[str, Any]:
        """Active jobs (queued / running), or the most recent ones; plus the last failure."""
        from sqlalchemy import select

        if active:
            rows = jobs.active_jobs(s)
        else:
            rows = list(s.execute(select(Job).order_by(Job.id.desc()).limit(limit)).scalars())
        failed = s.execute(select(Job).where(Job.status == "failed").order_by(Job.id.desc()).limit(1)).scalar_one_or_none()
        return {"jobs": [jobs.job_view(j) for j in rows], "last_failed": jobs.job_view(failed) if failed else None}

    @app.get("/jobs/{job_id}", dependencies=A)
    def job(job_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        j = s.get(Job, job_id)
        if j is None:
            raise HTTPException(404, _("api.not_found"))
        return jobs.job_view(j)

    # ---- settings ----
    @app.get("/settings", dependencies=A)
    def get_settings_ep() -> dict[str, Any]:
        cur = get_settings()
        data = cur.model_dump(mode="json")
        data["ui_language"] = ui_language()
        data["problems"] = settings_problems(cur)
        return data

    @app.patch("/settings", dependencies=W)
    def patch_settings(body: SettingsPatch) -> dict[str, Any]:
        # only the fields the client sent; null resets one to its default
        patch = body.model_dump(exclude_unset=True)
        from ..ml.registry import BUILTIN, NEEDS, _importable

        for kind in BUILTIN:
            if patch.get(kind) not in (None, BUILTIN[kind]) and not _importable(NEEDS[kind]):
                raise HTTPException(422, _("ml.unavailable", kind=kind, model=patch[kind], module=NEEDS[kind]))
        old_embedder = get_settings().embedder
        try:
            update_settings(patch)
        except ValueError as e:
            raise HTTPException(422, str(e))
        rebuild_job = None
        if get_settings().embedder != old_embedder:  # new vectors for everything: rebuild now
            with session_scope() as s:
                rebuild_job = jobs.enqueue(s, "rebuild", {}).id
        from ..inference import reset_backend
        from ..ml import reset_models

        reset_models()
        reset_backend()
        return {**get_settings_ep(), "rebuild_job": rebuild_job}

    # ---- desktop integration ----
    from .. import system as sysint

    @app.get("/system", dependencies=A)
    def system_info() -> dict[str, Any]:
        return sysint.info()

    @app.post("/system/open/{target}", dependencies=W)
    def system_open(target: str) -> dict[str, Any]:
        try:
            return {"opened": str(sysint.open_folder(target))}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/system/backup", dependencies=W)
    def system_backup() -> dict[str, Any]:
        from ..db.session import backup_database, backup_status

        try:
            path = backup_database(get_settings(), tag="manual")
        except OSError as e:
            raise HTTPException(500, _("api.backup_failed", error=str(e))) from e
        return {"path": str(path) if path else None, **backup_status(get_settings())}

    @app.post("/system/claude-desktop", dependencies=W)
    def system_claude() -> dict[str, Any]:
        return sysint.install_claude_desktop()

    @app.post("/system/data-dir", dependencies=W)
    def system_data_dir(body: dict[str, Any]) -> dict[str, Any]:
        try:
            out = sysint.move_data_dir(body.get("path"), copy=bool(body.get("copy", True)))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except OSError as e:  # disk full, permission denied, path too long
            raise HTTPException(422, _("api.move_failed", error=str(e))) from e
        if out.get("restart_required"):
            # from now on this process would write into the old copy: stop writing here
            app.state.moved_to = out["data_dir"]
            inbox_stop.set()
            if worker:
                worker.stop()
        return out

    @app.post("/system/restart", dependencies=A)
    def system_restart() -> dict[str, Any]:
        """Ask the desktop shell to restart the app (it relaunches on exit code 75)."""
        if not os.environ.get("RHIZOME_PARENT_PID"):
            raise HTTPException(409, _("api.restart_manually"))
        sysint.exit_for_restart()
        return {"restarting": True}

    # ---- static frontend ----
    dist = Path(os.environ.get("RHIZOME_FRONTEND_DIST", Path(__file__).resolve().parent.parent / "web"))
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/ui/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            return FileResponse(dist / "index.html")

    app.state.token = api_token
    app.state.read_only = read_only
    return app
