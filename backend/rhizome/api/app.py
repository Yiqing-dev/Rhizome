# SPDX-License-Identifier: Apache-2.0
"""REST API. Every capability is here first; the UI, MCP server and CLI are thin clients."""

from __future__ import annotations

import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
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
from ..config import Settings, get_settings, save_settings, set_settings, ui_language
from ..db.models import EDGE_TYPES, Entity, HumanDecision, Job
from ..db.session import session_scope
from ..i18n import _
from ..services import datasets as dataset_service

log = logging.getLogger(__name__)


# ---- auth ----------------------------------------------------------------------------

def token_path(settings: Settings) -> Path:
    return settings.data_dir / "token"


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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal worker
        stop = threading.Event()
        if start_worker and not read_only:
            worker = jobs.Worker()
            worker.start()
        if watch_inbox and not read_only:
            from ..inbox import watch

            threading.Thread(target=watch, kwargs={"stop": stop}, daemon=True, name="rhizome-inbox").start()
        yield
        stop.set()
        if worker:
            worker.stop()

    app = FastAPI(title="Rhizome", version=__version__, lifespan=lifespan)
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

    def writable() -> None:
        if read_only:
            raise HTTPException(403, _("api.read_only"))

    A = [Depends(auth)]
    W = [Depends(auth), Depends(writable)]

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "read_only": read_only, "language": ui_language()}

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
        else:
            body = IngestBody.model_validate(await request.json())
            text, filename, pdf = body.text, body.filename, None
        # network lookups and model inference are blocking: keep them off the event loop
        res = await run_in_threadpool(ingest_text, s, text, filename, pdf)
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

    @app.get("/entity/by-key", dependencies=A)
    def entity_by_key(key: str, s: Session = Depends(db)) -> dict[str, Any]:
        from ..pipeline.graph import Graph
        from ..services.views import entity_card

        e = Graph(s).by_key(key)
        if e is None:
            raise HTTPException(404, _("api.not_found"))
        return entity_card(s, e.id, touch_access=not read_only)

    @app.get("/entity/{entity_id}", dependencies=A)
    def entity(entity_id: int, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import entity_card

        card = entity_card(s, entity_id, touch_access=not read_only)
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

    # ---- topics ----
    @app.get("/topic/{topic_id}/assets", dependencies=A)
    def topic_assets(topic_id: int, role: str | None = None, s: Session = Depends(db)) -> dict[str, Any]:
        from ..services.views import topic_assets as ta

        out = ta(s, topic_id, role=role)
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
        return {"id": d.id, "op": d.op}

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
            decisions.revoke(Graph(s), decision_id)
        except decisions.DecisionError:
            raise HTTPException(404, _("api.not_found"))
        job = jobs.enqueue(s, "rebuild", {})
        return {"revoked": decision_id, "rebuild_job": job.id}

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
        return data

    @app.patch("/settings", dependencies=W)
    def patch_settings(body: SettingsPatch) -> dict[str, Any]:
        cur = get_settings()
        merged = cur.model_dump()
        for k, v in body.model_dump(exclude_none=True).items():
            if k == "thresholds":
                merged["thresholds"] = {**merged["thresholds"], **v}
            else:
                merged[k] = v
        new = Settings(**merged)
        save_settings(new)
        set_settings(new)
        from ..inference import reset_backend
        from ..ml import reset_models

        reset_models()
        reset_backend()
        return get_settings_ep()

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

    @app.post("/system/claude-desktop", dependencies=W)
    def system_claude() -> dict[str, Any]:
        return sysint.install_claude_desktop()

    @app.post("/system/data-dir", dependencies=W)
    def system_data_dir(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return sysint.move_data_dir(body.get("path"), copy=bool(body.get("copy", True)))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

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
