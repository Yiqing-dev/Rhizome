# SPDX-License-Identifier: Apache-2.0
"""One client interface over the API, used by the CLI and the MCP server.

* HttpClient  - talks to a running Rhizome (desktop app / `rhz serve`) over REST.
* LocalClient - runs the same service functions in-process; used when no server is running and
                on remote machines against a read-only snapshot (no API reachable there).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import Settings, get_settings


class Client(Protocol):
    def ingest(self, text: str, filename: str = "inline.yaml", pdf: bytes | None = None,
               repair: bool = False, replace: bool = False) -> dict[str, Any]: ...
    def decisions(self, limit: int = 50, offset: int = 0) -> dict[str, Any]: ...
    def revoke(self, decision_id: int) -> dict[str, Any]: ...
    def search(self, q: str, **filters: Any) -> list[dict[str, Any]]: ...
    def get(self, entity_id: int) -> dict[str, Any] | None: ...
    def get_by_key(self, key: str) -> dict[str, Any] | None: ...
    def related(self, entity_id: int) -> list[dict[str, Any]]: ...
    def neighbors(self, entity_id: int, hops: int = 1) -> dict[str, Any]: ...
    def recall(self, text: str, limit: int | None = None) -> list[dict[str, Any]]: ...
    def recall_code(self, source: str, limit: int | None = None) -> dict[str, Any]: ...
    def data(self, accession: str) -> dict[str, Any] | None: ...
    def queue(self, kind: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]: ...
    def resolve(self, item_id: int, action: str, note: str | None = None) -> dict[str, Any]: ...
    def decide(self, op: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def digest(self, days: int = 7) -> dict[str, Any]: ...
    def topic_assets(self, topic_id: int, role: str | None = None) -> dict[str, Any] | None: ...
    def create_topic(self, **body: Any) -> dict[str, Any]: ...
    def stats(self) -> dict[str, Any]: ...
    def vocab(self) -> str: ...
    def run_job(self, kind: str, payload: dict[str, Any] | None = None, wait: bool = True) -> dict[str, Any]: ...


class HttpClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self._c = httpx.Client(base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {token}"})

    def _get(self, path: str, **params: Any) -> Any:
        r = self._c.get(path, params={k: v for k, v in params.items() if v is not None})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        r = self._c.post(path, json=body)
        if r.status_code == 422:
            return {"ok": False, "error": r.json().get("detail")}
        r.raise_for_status()
        return r.json()

    def ingest(self, text, filename="inline.yaml", pdf=None, repair=False, replace=False):
        if pdf is not None:
            files = {"file": (filename, text.encode("utf-8"), "application/yaml"),
                     "pdf": (Path(filename).with_suffix(".pdf").name, pdf, "application/pdf")}
            r = self._c.post("/ingest", files=files, data={"repair": str(repair).lower(), "replace": str(replace).lower()})
        else:
            r = self._c.post("/ingest", json={"text": text, "filename": filename, "repair": repair, "replace": replace})
        if r.status_code == 422:
            return r.json()["detail"]
        r.raise_for_status()
        return r.json()

    def search(self, q, **filters):
        return self._get("/search", q=q, **filters)["results"]

    def get(self, entity_id):
        return self._get(f"/entity/{entity_id}")

    def get_by_key(self, key):
        return self._get("/entity/by-key", key=key)

    def related(self, entity_id):
        r = self._get(f"/entity/{entity_id}/related")
        return r["related"] if r else []

    def neighbors(self, entity_id, hops=1):
        return self._get(f"/entity/{entity_id}/neighbors", hops=hops)

    def recall(self, text, limit=None):
        return self._post("/recall", {"text": text, "limit": limit})["results"]

    def recall_code(self, source, limit=None):
        return self._post("/recall/code", {"text": source, "limit": limit})

    def data(self, accession):
        return self._get(f"/data/{accession}")

    def queue(self, kind=None, limit=20, offset=0):
        return self._get("/review", kind=kind, limit=limit, offset=offset)

    def resolve(self, item_id, action, note=None):
        return self._post(f"/review/{item_id}", {"action": action, "note": note})

    def decide(self, op, payload):
        return self._post("/decision", {"op": op, "payload": payload})

    def decisions(self, limit=50, offset=0):
        return self._get("/decisions", limit=limit, offset=offset)

    def revoke(self, decision_id):
        r = self._c.delete(f"/decision/{decision_id}")
        if r.status_code == 404:
            return {"ok": False, "error": "not found"}
        r.raise_for_status()
        return r.json()

    def digest(self, days=7):
        return self._get("/digest", days=days)

    def topic_assets(self, topic_id, role=None):
        return self._get(f"/topic/{topic_id}/assets", role=role)

    def create_topic(self, **body):
        return self._post("/topic", body)

    def stats(self):
        return self._get("/stats")

    def vocab(self):
        r = self._c.get("/vocab")
        r.raise_for_status()
        return r.text

    def run_job(self, kind, payload=None, wait=True, poll=1.0):
        """Queue on the running app's worker (never a second writer next to it) and follow it."""
        import time

        j = self._post("/jobs", {"kind": kind, "payload": payload or {}})
        while wait and j.get("status") in ("queued", "running"):
            time.sleep(poll)
            j = self._get(f"/jobs/{j['id']}")
        return j

    def patch_settings(self, patch):
        r = self._c.patch("/settings", json=patch)
        if r.status_code == 422:
            raise ValueError(r.json().get("detail"))
        r.raise_for_status()
        return r.json()


class LocalClient:
    def __init__(self, settings: Settings | None = None, read_only: bool = False):
        from .config import set_settings
        from .db.session import init_db

        if settings is not None:
            set_settings(settings)
        self.read_only = read_only
        if not read_only:
            get_settings().ensure_dirs()
            init_db()

    def _s(self):
        from .db.session import session_scope

        return session_scope(read_only=self.read_only)

    def _rw(self) -> None:
        if self.read_only:
            from .i18n import _

            raise PermissionError(_("cli.read_only"))

    def ingest(self, text, filename="inline.yaml", pdf=None, repair=False, replace=False):
        self._rw()
        from .pipeline.ingest import ingest_text

        with self._s() as s:
            out = ingest_text(s, text, filename, pdf, repair=repair, replace=replace).to_dict()
        if out.get("rebuild_job"):
            from . import jobs

            jobs.run_job(out["rebuild_job"])
        return out

    def search(self, q, **filters):
        from .services.search import DEFAULT_TYPES, Filters, search

        types = filters.pop("types", None)
        limit = filters.pop("limit", 20)
        offset = filters.pop("offset", 0)
        f = Filters(types=tuple(types.split(",")) if isinstance(types, str) else (tuple(types) if types else DEFAULT_TYPES),
                    **{k: v for k, v in filters.items() if v is not None})
        with self._s() as s:
            return search(s, q, f, limit=limit, offset=offset)

    def get(self, entity_id):
        from .services.views import entity_card

        with self._s() as s:
            return entity_card(s, entity_id, touch_access=not self.read_only)

    def get_by_key(self, key):
        from .pipeline.graph import Graph
        from .services.views import entity_card

        with self._s() as s:
            e = Graph(s).by_key(key)
            return entity_card(s, e.id, touch_access=not self.read_only) if e else None

    def related(self, entity_id):
        from .db.models import Entity
        from .pipeline.graph import entity_text
        from .services.recall import recall, related_to_work

        with self._s() as s:
            e = s.get(Entity, entity_id)
            if e is None:
                return []
            if e.type == "work":
                return related_to_work(s, entity_id)
            return [h for h in recall(s, entity_text(e), limit=8, min_relevance=0.0) if h["id"] != e.id]

    def neighbors(self, entity_id, hops=1):
        from .services.views import neighbors

        with self._s() as s:
            return neighbors(s, entity_id, hops=hops)

    def recall(self, text, limit=None):
        from .services.recall import recall

        with self._s() as s:
            return recall(s, text, limit)

    def recall_code(self, source, limit=None):
        from .services.recall import context_from_code, recall

        ctx = context_from_code(source)
        with self._s() as s:
            return {"context": ctx, "results": recall(s, ctx, limit)}

    def data(self, accession):
        from .services.datasets import dataset_info

        with self._s() as s:
            return dataset_info(s, accession)

    def queue(self, kind=None, limit=20, offset=0):
        from .services.review import list_items

        with self._s() as s:
            return list_items(s, kind, limit, offset)

    def resolve(self, item_id, action, note=None):
        self._rw()
        from .services.review import resolve

        with self._s() as s:
            return resolve(s, item_id, action, note)

    def decide(self, op, payload):
        self._rw()
        from .pipeline import decisions
        from .pipeline.graph import Graph

        try:
            with self._s() as s:
                d = decisions.record(Graph(s), op, payload)
                job = None
                if op in decisions.NEEDS_REBUILD:
                    from . import jobs

                    job = jobs.enqueue(s, "rebuild", {}).id
                out = {"id": d.id, "op": d.op, "rebuild_job": job}
        except decisions.DecisionError as e:  # same shape as the API's 422
            return {"ok": False, "error": str(e)}
        if job:
            from . import jobs

            jobs.run_job(job)
        return out

    def decisions(self, limit=50, offset=0):
        from sqlalchemy import select

        from .db.models import HumanDecision

        with self._s() as s:
            rows = s.execute(select(HumanDecision).order_by(HumanDecision.id.desc()).offset(offset).limit(limit)).scalars()
            return {"decisions": [{"id": d.id, "op": d.op, "payload": d.payload, "created_at": d.created_at.isoformat(),
                                   "revoked_at": d.revoked_at.isoformat() if d.revoked_at else None} for d in rows]}

    def revoke(self, decision_id):
        self._rw()
        from . import jobs
        from .pipeline import decisions
        from .pipeline.graph import Graph

        with self._s() as s:
            try:
                changed = decisions.revoke(Graph(s), decision_id)
            except decisions.DecisionError:
                return {"ok": False, "error": "not found"}
            job = jobs.enqueue(s, "rebuild", {}).id if changed else None
        if job:
            jobs.run_job(job)
        return {"revoked": decision_id, "changed": changed, "rebuild_job": job}

    def digest(self, days=7):
        from .services.synthesis import weekly_digest

        with self._s() as s:
            return weekly_digest(s, days)

    def topic_assets(self, topic_id, role=None):
        from .services.views import topic_assets

        with self._s() as s:
            return topic_assets(s, topic_id, role)

    def create_topic(self, **body):
        self._rw()
        from . import jobs
        from .pipeline import decisions
        from .pipeline.canonicalize import free_key
        from .pipeline.graph import Graph

        retro = body.pop("retro_tag", True)
        k = body.pop("k", None)
        with self._s() as s:
            g = Graph(s)
            d = decisions.record(g, "create_topic", body)
            t = g.by_alias("topic", body["name"]) or g.by_key(free_key("topic", body["name"]))
            job = jobs.enqueue(s, "retro_tag", {"topic": t.key, "k": k}) if retro else None
            out = {"topic_id": t.id, "key": t.key, "decision_id": d.id, "job_id": job.id if job else None}
        if retro:
            jobs.run_job(out["job_id"])
        return out

    def stats(self):
        from .services.views import home_stats

        with self._s() as s:
            return home_stats(s)

    def vocab(self):
        from .services.vocab import export_vocab

        with self._s() as s:
            return export_vocab(s)

    def run_job(self, kind, payload=None, wait=True):
        self._rw()
        from . import jobs
        from .db.models import Job

        with self._s() as s:
            jid = jobs.enqueue(s, kind, payload).id
        if wait:
            jobs.run_job(jid)
        with self._s() as s:
            return jobs.job_view(s.get(Job, jid))


def connect(settings: Settings | None = None, snapshot: Path | None = None, prefer_http: bool = True) -> Client:
    """HTTP if a server is up, else in-process. ``snapshot`` forces read-only local mode."""
    if snapshot is not None:
        from .config import Settings as S

        st = S(data_dir=snapshot.parent, database_url=f"sqlite:///{snapshot.resolve().as_posix()}")
        return LocalClient(st, read_only=True)
    st = settings or get_settings()
    if prefer_http:
        from .api.app import token_path

        from .system import running_server_url

        tp = token_path(st)
        if tp.exists():
            for base in dict.fromkeys(filter(None, [running_server_url(), st.base_url])):
                try:
                    r = httpx.get(base + "/health", timeout=2.0)
                    if r.status_code == 200 and not r.json().get("read_only"):
                        return HttpClient(base, tp.read_text("utf-8-sig").strip())
                except httpx.HTTPError:
                    continue
    return LocalClient(st)
