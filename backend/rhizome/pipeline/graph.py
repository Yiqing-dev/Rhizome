# SPDX-License-Identifier: Apache-2.0
"""Low-level graph store operations on L2/L3 used by materialisation, decisions and rebuilds."""

from __future__ import annotations

import logging
from collections import OrderedDict
import threading
from datetime import datetime
from typing import Any, Iterable

import numpy as np
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from ..db.models import (
    KV,
    AccessLog,
    Edge,
    Embedding,
    Entity,
    EntityAlias,
    Extraction,
    HumanDecision,
    RawObject,
    ReviewCard,
    ReviewItem,
    VectorCache,
    Work,
    utcnow,
)
from ..db.session import has_fts
from ..ml import get_embedder
from ..text import lang_of, norm, sha256

log = logging.getLogger(__name__)

STATUS_RANK = {"auto": 0, "confirmed": 1, "rejected": 2}


# ---- embedding text ---------------------------------------------------------------

def entity_text(e: Entity) -> str:
    a = e.attrs or {}
    parts: list[str] = [e.canonical_name]
    if e.type == "work":
        parts += a.get("tldr", []) + ([a["abstract"][:1500]] if a.get("abstract") else [])
    elif e.type == "dataset":
        parts += [str(a[k]) for k in ("accession", "name", "organism", "tissue", "modality", "scale") if a.get(k)]
    elif e.type == "method":
        parts += [str(a[k]) for k in ("io", "modality") if a.get(k)]
    elif e.type == "idea":
        t = a.get("transfer") or {}
        parts += [str(t[k]) for k in ("type", "to", "barrier") if t.get(k)]
    elif e.type == "claim":
        parts += [a["boundary"]] if a.get("boundary") else []
    elif e.type == "topic":
        parts += [a.get("definition") or ""] + list(a.get("examples", []))
    return "\n".join(p for p in parts if p)


# ---- full-text rows -------------------------------------------------------------------
# entity_fts rowid = entity id (one row per entity): updates and deletes are rowid lookups.
FTS_DDL = "create virtual table entity_fts using fts5(text, tokenize='trigram')"


def fts_body(e, aliases: Iterable[str]) -> str:
    return entity_text(e) + "\n" + "\n".join(aliases)


def rebuild_fts(conn) -> int:
    """Re-derive every FTS row from entities and aliases (migrations, snapshots, repairs).
    ``conn`` is a SQLAlchemy Connection or Session."""
    from types import SimpleNamespace

    aliases: dict[int, list[str]] = {}
    for eid, alias in conn.execute(text("select entity_id, alias from entity_alias order by id")):
        aliases.setdefault(eid, []).append(alias)
    conn.execute(text("delete from entity_fts"))
    n = 0
    for eid, etype, name, attrs in conn.execute(text("select id, type, canonical_name, attrs from entity")).all():
        if isinstance(attrs, str):
            import json

            attrs = json.loads(attrs or "{}")
        e = SimpleNamespace(type=etype, canonical_name=name, attrs=attrs or {})
        conn.execute(text("insert into entity_fts(rowid, text) values (:i, :t)"),
                     {"i": eid, "t": fts_body(e, aliases.get(eid, []))})
        n += 1
    return n


# ---- in-process vector index --------------------------------------------------------

class _VectorCache:
    """Brute-force cosine index per (database, model).

    Loaded lazily from the ``embedding`` table and kept in step with in-process writes through
    ``upsert`` / ``remove``; a cheap DB signature (row count, id sum, version) catches rows added by
    another process (e.g. the CLI while the app is running). Rows live in preallocated buffers
    with a logical length, so an upsert during ingest or rebuild is an O(1) append (amortised), not
    a copy of the whole matrix; a removal swaps the last row in. The load streams rows into a
    buffer sized from the count (no second copy at the peak) and is single-flight per key. At
    10^5 assets x 1024 dims this is ~400 MB float32 and a few ms per query on CPU; sqlite-vec /
    pgvector can replace it behind ``knn``.
    """

    GROW = 1.5

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loading: dict[tuple[str, str], threading.Lock] = {}
        # key -> [signature, ids, types, mat, n]; the arrays may be longer than n
        self._data: dict[tuple[str, str], list] = {}
        self.loads = 0  # for tests and diagnostics

    @staticmethod
    def _key(s: Session, model: str) -> tuple[str, str]:
        return str(s.get_bind().url), model

    def _signature(self, s: Session, model: str) -> tuple[int, int, int]:
        count, id_sum = s.execute(
            select(func.count(), func.coalesce(func.sum(Embedding.entity_id), 0)).where(Embedding.model == model)
        ).one()
        return int(count), int(id_sum), embedding_version(s)

    def get(self, s: Session, model: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        key = self._key(s, model)
        sig = self._signature(s, model)
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] == sig:
                return hit[1][:hit[4]], hit[2][:hit[4]], hit[3][:hit[4]]
            flight = self._loading.setdefault(key, threading.Lock())
        with flight:  # one loader per key; the others wait and reuse its result
            with self._lock:
                hit = self._data.get(key)
                if hit and hit[0] == sig:
                    return hit[1][:hit[4]], hit[2][:hit[4]], hit[3][:hit[4]]
            ids, types, mat, n = self._load(s, model, sig[0])
            with self._lock:
                self._data[key] = [sig, ids, types, mat, n]
            self.loads += 1
            return ids[:n], types[:n], mat[:n]

    def _load(self, s: Session, model: str, count: int):
        q = (select(Embedding.entity_id, Entity.type, Embedding.vec, Embedding.dim)
             .join(Entity, Entity.id == Embedding.entity_id).where(Embedding.model == model))
        cap = max(count, 16)
        ids = np.zeros(cap, dtype=np.int64)
        types = np.empty(cap, dtype=object)
        mat: np.ndarray | None = None
        n = 0
        for eid, etype, blob, dim in s.execute(q.execution_options(yield_per=1024)):
            vec = np.frombuffer(blob, dtype=np.float32)
            if mat is None:
                mat = np.zeros((cap, vec.shape[0]), dtype=np.float32)
            if vec.shape[0] != mat.shape[1]:
                continue  # a row of another dimension (half-switched model): never mixed in
            if n == cap:  # rows were added while we streamed
                ids, types, mat = self._grown(ids, types, mat, n)
                cap = len(ids)
            ids[n], types[n], mat[n] = eid, etype, vec
            n += 1
        if mat is None:
            mat = np.zeros((cap, 1), dtype=np.float32)
        return ids, types, mat, n

    def _grown(self, ids, types, mat, n):
        cap = int(max(len(ids) * self.GROW, n + 16))
        nid = np.zeros(cap, dtype=np.int64)
        nid[:n] = ids[:n]
        nty = np.empty(cap, dtype=object)
        nty[:n] = types[:n]
        nmat = np.zeros((cap, mat.shape[1]), dtype=np.float32)
        nmat[:n] = mat[:n]
        return nid, nty, nmat

    def upsert(self, s: Session, model: str, entity_id: int, etype: str, vec: np.ndarray, version: int) -> None:
        """Keep a loaded index current after an embedding is written or replaced (rename, edit)."""
        key = self._key(s, model)
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return
            _, ids, types, mat, n = hit
            where = np.nonzero(ids[:n] == entity_id)[0]
            if len(where):
                i = where[0]
                if mat.shape[1] == vec.shape[0]:
                    mat[i] = vec
                types[i] = etype
            else:
                if mat.shape[1] != vec.shape[0]:
                    if n:  # vectors of another dimension: this index is for the other model
                        return
                    mat = np.zeros((len(ids), vec.shape[0]), dtype=np.float32)
                if n == len(ids):
                    ids, types, mat = self._grown(ids, types, mat, n)
                ids[n], types[n], mat[n] = entity_id, etype, vec.astype(np.float32)
                n += 1
            self._data[key] = [(n, int(ids[:n].sum()), version), ids, types, mat, n]

    def remove(self, s: Session, model: str, entity_id: int, version: int) -> None:
        key = self._key(s, model)
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return
            _, ids, types, mat, n = hit
            where = np.nonzero(ids[:n] == entity_id)[0]
            if len(where):
                i, last = where[0], n - 1
                ids[i], types[i], mat[i] = ids[last], types[last], mat[last]
                n = last
            self._data[key] = [(n, int(ids[:n].sum()), version), ids, types, mat, n]

    def buffer_id(self, s: Session, model: str) -> int | None:
        """Identity of the matrix buffer (tests: appends must not reallocate every time)."""
        with self._lock:
            hit = self._data.get(self._key(s, model))
            return id(hit[3]) if hit else None

    def preload(self, settings=None) -> int:
        """Load the index in the background at startup so the first search does not pay for it."""
        from ..db.session import session_scope

        try:
            with session_scope(settings, read_only=True) as s:
                model = index_model(s) or get_embedder().name
                return len(self.get(s, model)[0])
        except Exception as e:  # noqa: BLE001 - best effort
            log.info("vector index preload skipped: %s", e)
            return 0

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


VECTORS = _VectorCache()

EMB_VERSION_KEY = "embedding_version"
INDEX_MODEL_KEY = "embedding_model"  # which embedder the library's vectors belong to


def index_model(s: Session) -> str | None:
    """The embedder the stored vectors were made with (older libraries: inferred from the rows)."""
    row = s.get(KV, INDEX_MODEL_KEY)
    if row and row.v and row.v.get("model"):
        return row.v["model"]
    models = set(s.execute(select(Embedding.model).distinct()).scalars())
    return next(iter(models)) if len(models) == 1 else (None if not models else "mixed")


def set_index_model(s: Session, model: str) -> None:
    row = s.get(KV, INDEX_MODEL_KEY)
    if row is None:
        s.add(KV(k=INDEX_MODEL_KEY, v={"model": model}))
    else:
        row.v = {"model": model}


def check_index_model(s: Session) -> str:
    """The configured embedder must be the one the library is indexed with. Another process may
    have switched it (settings.json): reload once; if they still differ, a rebuild is needed."""
    from ..ml import IndexStale, reset_models

    im = index_model(s)
    if im is None or im == get_embedder().name:
        return get_embedder().name
    from ..config import adopt_file_embedder

    adopt_file_embedder()
    reset_models()
    if im == get_embedder().name:
        return im
    from ..i18n import _

    raise IndexStale(_("ml.index_stale", index=im, current=get_embedder().name))


def embedding_version(s: Session) -> int:
    row = s.get(KV, EMB_VERSION_KEY)
    return int((row.v or {}).get("v", 0)) if row else 0


def bump_embedding_version(s: Session) -> int:
    """Called on every embedding write/delete so other processes' caches notice in-place changes."""
    row = s.get(KV, EMB_VERSION_KEY)
    v = embedding_version(s) + 1
    if row is None:
        s.add(KV(k=EMB_VERSION_KEY, v={"v": v}))
    else:
        row.v = {"v": v}
    return v


def knn(s: Session, query_vec: np.ndarray, types: Iterable[str] | None = None, k: int = 10,
        exclude: set[int] | None = None) -> list[tuple[int, float]]:
    model = check_index_model(s)  # never search a library indexed with another model
    ids, etypes, mat = VECTORS.get(s, model)
    if len(ids) == 0 or mat.shape[1] != query_vec.shape[0]:
        return []
    sims = mat @ query_vec
    mask = np.ones(len(ids), dtype=bool)
    if types is not None:
        mask &= np.isin(etypes, list(types))
    if exclude:
        mask &= ~np.isin(ids, list(exclude))
    sims = np.where(mask, sims, -np.inf)
    k = min(k, int(mask.sum()))
    if k <= 0:
        return []
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    return [(int(ids[i]), float(sims[i])) for i in top]


_QUERY_LRU: "OrderedDict[tuple[str, str], np.ndarray]" = OrderedDict()
_QUERY_LRU_MAX = 1024


def embed_texts(s: Session, texts: list[str], persist: bool = True) -> np.ndarray:
    """Embed with the configured model, using the text-hash cache. ``persist=False`` (search and
    recall queries) keeps new vectors in an in-process LRU instead of writing them, so a read
    request never needs the database write lock (which a long rebuild may hold)."""
    emb = get_embedder()
    shas = [sha256(t) for t in texts]
    if not persist:
        hit = [_QUERY_LRU.get((emb.name, h)) for h in shas]
        if all(v is not None for v in hit):
            for h in shas:
                _QUERY_LRU.move_to_end((emb.name, h))
            return np.stack(hit) if hit else np.zeros((0, emb.dim), dtype=np.float32)
    cached = {
        r.text_sha: np.frombuffer(r.vec, dtype=np.float32)
        for r in s.execute(select(VectorCache).where(VectorCache.model == emb.name,
                                                     VectorCache.text_sha.in_(set(shas)))).scalars()
    } if shas else {}
    missing = [i for i, h in enumerate(shas) if h not in cached]
    if missing:
        new = emb.embed([texts[i] for i in missing])
        for j, i in enumerate(missing):
            if shas[i] not in cached:
                cached[shas[i]] = new[j]
                if not persist:
                    _QUERY_LRU[(emb.name, shas[i])] = new[j]
                    if len(_QUERY_LRU) > _QUERY_LRU_MAX:
                        _QUERY_LRU.popitem(last=False)
                    continue
                if s.info.get("read_only"):
                    continue
                s.merge(VectorCache(text_sha=shas[i], model=emb.name, vec=new[j].astype(np.float32).tobytes()))
    return np.stack([cached[h] for h in shas]) if shas else np.zeros((0, emb.dim), dtype=np.float32)


def prune_vector_cache(s: Session, keep_texts: Iterable[str] | None = None) -> int:
    """Drop cached vectors of other models and of texts no current entity has (every query used to
    be cached; old models stay after a switch). Called by rebuild and `rhz gc`. Returns rows deleted."""
    model = get_embedder().name
    n = s.execute(delete(VectorCache).where(VectorCache.model != model)).rowcount or 0
    if keep_texts is None:
        keep_texts = (entity_text(e) for e in s.execute(select(Entity)).scalars())
    keep = {sha256(t) for t in keep_texts}
    stale = [h for h in s.execute(select(VectorCache.text_sha).where(VectorCache.model == model)).scalars()
             if h not in keep]
    for i in range(0, len(stale), 500):
        n += s.execute(delete(VectorCache).where(VectorCache.model == model,
                                                 VectorCache.text_sha.in_(stale[i:i + 500]))).rowcount or 0
    return n


# ---- graph -----------------------------------------------------------------------------

class Graph:
    def __init__(self, session: Session):
        self.s = session
        self._fts = has_fts(session)
        self._redirects: dict[str, str] | None = None
        # When set (replaying an extraction), new entities and edges are stamped with this time.
        self.clock: datetime | None = None
        # Set by rebuild: entities and review items get back the ids they had before (the UI, CLI
        # and MCP use ids as handles); new ones get ids above every id ever handed out.
        self.id_plan: dict[str, int] | None = None
        self.next_id = 0
        self.item_plan: dict[str, int] | None = None
        self.next_item_id = 0
        # organism name (normalised) -> taxid known before a rebuild, for extractions made before
        # taxa were stored in their metadata
        self.taxa_fallback: dict[str, str] = {}

    def _planned(self, model, plan: dict[str, int] | None, key: str, attr: str) -> int | None:
        if plan is None:
            return None
        want = plan.get(key)
        if want is not None and self.s.get(model, want) is None:
            return want
        nid = getattr(self, attr)
        setattr(self, attr, nid + 1)
        return nid

    # -- keys / redirects (from human merge decisions) --
    def redirects(self) -> dict[str, str]:
        if self._redirects is None:
            self._redirects = {}
            for d in self.s.execute(select(HumanDecision).where(HumanDecision.op == "merge",
                                                                HumanDecision.revoked_at.is_(None))
                                    .order_by(HumanDecision.id)).scalars():
                self._redirects[d.payload["from"]] = d.payload["into"]
        return self._redirects

    def resolve_key(self, key: str) -> str:
        seen = set()
        r = self.redirects()
        while key in r and key not in seen:
            seen.add(key)
            key = r[key]
        return key

    def invalidate_redirects(self) -> None:
        self._redirects = None

    def at(self, when: datetime | None):
        """``with g.at(ex.created_at):`` stamps what is created inside with that time."""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            prev = self.clock
            self.clock = when
            try:
                yield self
            finally:
                self.clock = prev
        return _ctx()

    def pinned_by_decision(self, key: str) -> bool:
        """``key`` takes part in a human merge whose other side does not exist (yet): during a
        rebuild the other export may simply come later. The entity is then created under its own
        key and left alone, so the replayed merge finds both sides; probing neighbours could
        auto-merge one of them elsewhere and silently lose the human decision."""
        r = self.redirects()
        target = self.resolve_key(key)
        if target != key:
            return self.s.execute(select(Entity.id).where(Entity.key == target)).first() is None
        return key in set(r.values())

    # -- entities --
    def by_key(self, key: str) -> Entity | None:
        return self.s.execute(select(Entity).where(Entity.key == self.resolve_key(key))).scalar_one_or_none()

    def by_id(self, entity_id: int) -> Entity | None:
        return self.s.get(Entity, entity_id)

    def resolve_ref(self, ref: str) -> Entity | None:
        """What a person or Claude types for an entity: a key, a DOI, an accession or repository
        (external id), or a name / alias of any type (works first, then assets)."""
        from ..external.ids import DOI_RE, normalize_doi, normalize_repo

        ref = ref.strip()
        if not ref:
            return None
        e = self.by_key(ref)
        if e is not None:
            return e
        doi = ref.removeprefix("https://doi.org/").removeprefix("doi:")
        if DOI_RE.match(doi):
            e = self.by_key(f"work:doi:{normalize_doi(doi)}") or self.by_alias("work", normalize_doi(doi))
            if e is not None:
                return e
        repo = normalize_repo(ref)
        if repo:
            e = self.by_key(f"method:repo:{repo}") or self.by_external_id("method", repo)
            if e is not None:
                return e
        e = self.s.execute(select(Entity).where(Entity.external_id == ref).order_by(Entity.id)).scalars().first()
        if e is not None:
            return e
        for etype in ("work", "dataset", "method", "topic", "idea", "claim", "organism", "modality"):
            e = self.by_alias(etype, ref)
            if e is not None:
                return e
        return None

    def by_external_id(self, etype: str, external_id: str) -> Entity | None:
        return self.s.execute(select(Entity).where(Entity.type == etype, Entity.external_id == external_id)
                              ).scalar_one_or_none()

    def anchor(self, e: Entity, external_id: str) -> None:
        """Upgrade a free concept to an anchored one once an external ID becomes known."""
        if e.external_id is None and self.by_external_id(e.type, external_id) is None:
            e.external_id = external_id

    def by_alias(self, etype: str, name: str) -> Entity | None:
        n = norm(name)
        if not n:
            return None
        return self.s.execute(
            select(Entity).join(EntityAlias, EntityAlias.entity_id == Entity.id)
            .where(Entity.type == etype, EntityAlias.norm == n).limit(1)
        ).scalar_one_or_none()

    def create(self, etype: str, key: str, name: str, *, external_id: str | None = None,
               status: str = "active", attrs: dict[str, Any] | None = None,
               aliases: Iterable[str] = (), embed: bool = True) -> Entity:
        e = Entity(id=self._planned(Entity, self.id_plan, key, "next_id"), type=etype, key=key,
                   canonical_name=name, external_id=external_id, status=status,
                   attrs=attrs or {}, created_at=self.clock or utcnow())
        self.s.add(e)
        self.s.flush()
        for a in [name, *aliases]:
            self.add_alias(e, a, index=False)  # reindex() below writes the FTS row once
        if etype == "work":
            self.s.add(Work(entity_id=e.id, dois=[], tier=0))
        self.reindex(e, embed=embed)
        return e

    def add_alias(self, e: Entity, alias: str | None, source: str = "extraction", lang: str | None = None,
                  index: bool = True) -> bool:
        """Add an alias; the entity's full-text row is refreshed so the alias is searchable at once
        (``index=False`` when the caller reindexes anyway). Returns whether it was new."""
        if not alias:
            return False
        n = norm(alias)
        if not n or len(n) > 500:
            return False
        exists = self.s.execute(select(EntityAlias.id).where(EntityAlias.entity_id == e.id,
                                                             EntityAlias.norm == n)).first()
        if exists:
            return False
        self.s.add(EntityAlias(entity_id=e.id, alias=alias.strip(), norm=n, lang=lang or lang_of(alias),
                               source=source))
        self.s.flush()
        if index:
            self._refresh_fts(e)
        return True

    def _refresh_fts(self, e: Entity) -> None:
        if not self._fts:
            return
        aliases = self.s.execute(select(EntityAlias.alias).where(EntityAlias.entity_id == e.id)
                                 .order_by(EntityAlias.id)).scalars().all()
        self.s.execute(text("delete from entity_fts where rowid = :i"), {"i": e.id})
        self.s.execute(text("insert into entity_fts(rowid, text) values (:i, :t)"),
                       {"i": e.id, "t": fts_body(e, aliases)})

    def fill_attrs(self, e: Entity, attrs: dict[str, Any] | None, skip: tuple[str, ...] = ("origin", "weight")) -> bool:
        """Add attributes the entity does not have yet (a later paper describing a known method's
        input/output, a dataset's tissue, ...); existing values are never overwritten. Reindexes
        when something was added, since attributes are part of the indexed text."""
        cur = dict(e.attrs or {})
        new = {k: v for k, v in (attrs or {}).items()
               if k not in skip and v not in (None, [], "", {}) and cur.get(k) in (None, [], "", {})}
        if not new:
            return False
        cur.update(new)
        e.attrs = cur
        self.reindex(e)
        return True

    REPORTED_SKIP = ("origin", "weight", "verified", "reported", "id")

    def merge_reported(self, e: Entity, attrs: dict[str, Any] | None, authoritative: bool = False) -> bool:
        """Attributes several papers report about one dataset or method (tissue, organism, scale,
        io ...): an empty slot is filled; a conflicting value is kept in ``attrs.reported[k]`` and
        replaces the current one only when this paper is the producer (``authoritative``) and the
        current value did not come from a producer. Returns whether anything changed."""
        cur = dict(e.attrs or {})
        reported: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (cur.get("reported") or {}).items()}
        changed = False
        for k, v in (attrs or {}).items():
            if k in self.REPORTED_SKIP or v in (None, [], "", {}):
                continue
            old = cur.get(k)
            if old in (None, [], "", {}):
                cur[k] = v
                changed = True
                continue
            if old == v or (isinstance(v, str) and isinstance(old, str) and norm(old) == norm(v)):
                continue
            rec = reported.setdefault(k, {"values": [old]})
            if v not in rec["values"]:
                rec["values"].append(v)
            if authoritative and not rec.get("producer"):
                rec["producer"] = v
                cur[k] = v
            changed = True
        if reported:
            cur["reported"] = reported
        if changed:
            e.attrs = cur
        return changed

    def update_attrs(self, e: Entity, **attrs: Any) -> None:
        merged = dict(e.attrs or {})
        merged.update({k: v for k, v in attrs.items() if v not in (None, [], "")})
        e.attrs = merged

    def reindex(self, e: Entity, embed: bool = True) -> None:
        """Refresh full-text row and embedding of one entity."""
        self._refresh_fts(e)
        if embed and e.type not in ("organism", "modality"):
            if not self.s.info.get("index_model_checked"):
                check_index_model(self.s)
                if self.s.get(KV, INDEX_MODEL_KEY) is None:
                    set_index_model(self.s, get_embedder().name)
                self.s.info["index_model_checked"] = True
            vec = embed_texts(self.s, [entity_text(e)])[0]
            model = get_embedder().name
            self.s.merge(Embedding(entity_id=e.id, model=model, dim=int(vec.shape[0]),
                                   vec=vec.astype(np.float32).tobytes()))
            self.s.flush()
            VECTORS.upsert(self.s, model, e.id, e.type, vec, bump_embedding_version(self.s))

    def delete_entity(self, e: Entity) -> None:
        if self._fts:
            self.s.execute(text("delete from entity_fts where rowid = :i"), {"i": e.id})
        self.s.execute(delete(Edge).where((Edge.src == e.id) | (Edge.dst == e.id)))
        self.s.execute(delete(EntityAlias).where(EntityAlias.entity_id == e.id))
        self.s.execute(delete(Embedding).where(Embedding.entity_id == e.id))
        self.s.execute(delete(Work).where(Work.entity_id == e.id))
        self.s.delete(e)
        self.s.flush()
        VECTORS.remove(self.s, get_embedder().name, e.id, bump_embedding_version(self.s))

    # -- edges --
    def edge(self, src: Entity, dst: Entity, etype: str) -> Edge | None:
        return self.s.execute(select(Edge).where(Edge.src == src.id, Edge.dst == dst.id, Edge.type == etype)
                              ).scalar_one_or_none()

    def upsert_edge(self, src: Entity, dst: Entity, etype: str, *, confidence: float = 1.0,
                    extraction_id: int | None = None, evidence: str | None = None,
                    attrs: dict[str, Any] | None = None, status: str = "auto") -> Edge | None:
        if src.id == dst.id:
            return None
        ed = self.edge(src, dst, etype)
        if ed is None:
            ed = Edge(src=src.id, dst=dst.id, type=etype, confidence=confidence, extraction_id=extraction_id,
                      evidence=evidence, attrs=attrs or {}, status=status, created_at=self.clock or utcnow())
            self.s.add(ed)
            self.s.flush()
            return ed
        if extraction_id is not None and ed.extraction_id is not None and extraction_id != ed.extraction_id:
            # another export (a deep re-export, a merged paper): its evidence, confidence and
            # attributes replace the record as one unit; the previous record is kept, never mixed
            self._fold_edge(ed, confidence=confidence, extraction_id=extraction_id, evidence=evidence,
                            attrs=attrs, status=status)
            return ed
        ed.confidence = max(ed.confidence or 0.0, confidence)
        if attrs:
            merged = dict(ed.attrs or {})
            merged.update(attrs)
            ed.attrs = merged
        ed.evidence = ed.evidence or evidence
        ed.extraction_id = ed.extraction_id or extraction_id
        if STATUS_RANK[status] > STATUS_RANK[ed.status]:
            ed.status = status
        return ed

    EDGE_RECORD_KEYS = ("evidence_type", "strength", "logic_jump", "boundary", "via", "origin", "transfer_type",
                        "barrier")

    @staticmethod
    def edge_record(ed: Edge) -> dict[str, Any]:
        a = ed.attrs or {}
        return {"extraction_id": ed.extraction_id, "evidence": ed.evidence, "confidence": ed.confidence,
                **{k: a[k] for k in Graph.EDGE_RECORD_KEYS if k in a}}

    def _fold_edge(self, ed: Edge, *, confidence: float, extraction_id: int | None, evidence: str | None,
                   attrs: dict[str, Any] | None, status: str) -> None:
        """Replace an edge's provenance with another extraction's, keeping the old one in
        ``attrs.evidence_records`` (also used when a merge folds two edges into one)."""
        old = self.edge_record(ed)
        records = [r for r in (ed.attrs or {}).get("evidence_records", []) if r.get("extraction_id") != extraction_id]
        if old.get("extraction_id") != extraction_id:
            records.append(old)
        keep = {k: v for k, v in (ed.attrs or {}).items()
                if k not in self.EDGE_RECORD_KEYS and k != "evidence_records"}
        ed.attrs = {**keep, **(attrs or {}), "evidence_records": records}
        ed.evidence = evidence
        ed.extraction_id = extraction_id
        ed.confidence = confidence
        if STATUS_RANK[status] > STATUS_RANK[ed.status]:
            ed.status = status

    # -- merge / split --
    def merge(self, src: Entity, into: Entity) -> None:
        if src.id == into.id:
            return
        for ed in self.s.execute(select(Edge).where((Edge.src == src.id) | (Edge.dst == src.id))).scalars().all():
            new_src = into if ed.src == src.id else self.by_id(ed.src)
            new_dst = into if ed.dst == src.id else self.by_id(ed.dst)
            if new_src is None or new_dst is None or new_src.id == new_dst.id:
                self.s.delete(ed)
                continue
            existing = self.edge(new_src, new_dst, ed.type)
            if existing is not None:
                if ed.extraction_id is not None and ed.extraction_id != existing.extraction_id:
                    # keep the merged-away edge's provenance as a record of the surviving edge
                    records = list((existing.attrs or {}).get("evidence_records", []))
                    records.append(self.edge_record(ed))
                    existing.attrs = {**(existing.attrs or {}), "evidence_records": records}
                    if not existing.evidence and ed.evidence:
                        existing.evidence = ed.evidence
                if STATUS_RANK[ed.status] > STATUS_RANK[existing.status]:
                    existing.status = ed.status
                existing.confidence = max(existing.confidence, ed.confidence)
                self.s.delete(ed)
            else:
                ed.src, ed.dst = new_src.id, new_dst.id
        self.s.flush()
        for al in self.s.execute(select(EntityAlias).where(EntityAlias.entity_id == src.id)).scalars().all():
            self.add_alias(into, al.alias, source="merge", lang=al.lang)
        if src.type == "work" and into.type == "work":
            w_src, w_into = self.s.get(Work, src.id), self.s.get(Work, into.id)
            if w_src and w_into:
                w_into.dois = sorted(set(w_into.dois or []) | set(w_src.dois or []))
                w_into.tier = max(w_into.tier, w_src.tier)
                w_into.year = w_into.year or w_src.year
                if not w_into.openalex_id and w_src.openalex_id:
                    oid = w_src.openalex_id
                    w_src.openalex_id = None
                    self.s.flush()
                    w_into.openalex_id = oid
            self.s.execute(update(Extraction).where(Extraction.work_key == src.key).values(work_key=into.key))
            self.s.execute(update(RawObject).where(RawObject.work_key == src.key).values(work_key=into.key))
        merged_attrs = dict(src.attrs or {})
        merged_attrs.update(into.attrs or {})
        if (src.attrs or {}).get("origin") == "user" and (into.attrs or {}).get("origin") != "user":
            # the user's own idea survives a merge with a model's: their wording and origin stay
            merged_attrs.update(origin="user", weight=max(float((src.attrs or {}).get("weight") or 2.0),
                                                           float((into.attrs or {}).get("weight") or 0)))
            self.add_alias(into, into.canonical_name, source="merge")
            into.canonical_name = src.canonical_name
        into.attrs = merged_attrs
        if src.status == "active" and into.status == "candidate":
            into.status = "active"
        from .materialize import rekey_card  # cards keep their history under the surviving entity

        for card in self.s.execute(select(ReviewCard).where(ReviewCard.entity_key == src.key)).scalars().all():
            rekey_card(self.s, card, into.key)
        acc = self.s.get(AccessLog, src.key)
        if acc is not None:
            if self.s.get(AccessLog, into.key) is None:
                self.s.add(AccessLog(entity_key=into.key, last_seen_at=acc.last_seen_at, count=acc.count))
            self.s.delete(acc)
        self.delete_entity(src)
        self.reindex(into)

    # -- review queue --
    def queue(self, kind: str, payload: dict[str, Any], dedupe: str, score: float = 0.0) -> ReviewItem | None:
        existing = self.s.execute(select(ReviewItem).where(ReviewItem.dedupe_key == dedupe)).scalar_one_or_none()
        if existing is not None:
            return None
        item = ReviewItem(id=self._planned(ReviewItem, self.item_plan, dedupe, "next_item_id"),
                          kind=kind, payload=payload, dedupe_key=dedupe, score=score)
        self.s.add(item)
        self.s.flush()
        return item

    def distinct_pairs(self) -> set[frozenset[str]]:
        """Pairs a human said are two things: explicit `distinct` decisions and every `split`."""
        out: set[frozenset[str]] = set()
        for d in self.s.execute(select(HumanDecision).where(HumanDecision.op.in_(("distinct", "split")),
                                                            HumanDecision.revoked_at.is_(None))).scalars():
            if d.op == "distinct":
                out.add(frozenset((d.payload["a"], d.payload["b"])))
            elif d.payload.get("new_key"):
                out.add(frozenset((d.payload["key"], d.payload["new_key"])))
        return out
