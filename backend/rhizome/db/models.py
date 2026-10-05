# SPDX-License-Identifier: Apache-2.0
"""SQLAlchemy models shared by the SQLite (default) and PostgreSQL backends.

Layers:
  L0 raw_object         content-addressed, append-only
  L1 extraction         versioned; recompute adds rows and flips is_current
  L2 entity/alias/work  rebuilt from current L1 rows
  L3 edge/embedding     rebuilt from current L1 rows
  human_decision        applied last on every rebuild, never overwritten

Durable identity: ``entity.key`` (e.g. ``dataset:GSE12345``, ``topic:grn inference``) is stable
across rebuilds, so human decisions, review cards and access history reference keys, not ids.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ---- entity / edge vocabularies -------------------------------------------------

ENTITY_TYPES = ("work", "dataset", "method", "idea", "claim", "topic", "organism", "modality")
ASSET_TYPES = ("dataset", "method", "idea", "claim")
EDGE_TYPES = (
    "about", "applicable_to", "proposes", "uses", "produces", "evaluates", "supports",
    "contradicts", "extends", "cites", "is_a", "of_organism", "of_modality",
    "relates_to",  # a user insight pointing at the claim / method / ... it is about
)
EDGE_STATUS = ("auto", "confirmed", "rejected")


# ---- L0 -------------------------------------------------------------------------

class RawObject(Base):
    __tablename__ = "raw_object"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # rxf | pdf
    uri: Mapped[str] = mapped_column(Text)  # original file name
    work_key: Mapped[str | None] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---- L1 -------------------------------------------------------------------------

class Extraction(Base):
    __tablename__ = "extraction"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # rxf | openalex | retro_tag
    work_key: Mapped[str | None] = mapped_column(String(255), index=True)
    tier: Mapped[int] = mapped_column(SmallInteger, default=1)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    input_hashes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    output: Mapped[dict[str, Any]] = mapped_column(JSON)
    # resolution results captured at ingest so rebuilds need no network:
    # {"openalex_id":..., "checks": {"GSE1": "verified"}, "suspect": [...]}
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---- L2 -------------------------------------------------------------------------

class Entity(Base):
    __tablename__ = "entity"
    __table_args__ = (UniqueConstraint("type", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    key: Mapped[str] = mapped_column(String(255), unique=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255))
    # topic: candidate | active ; others: active
    status: Mapped[str] = mapped_column(String(16), default="active")
    attrs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EntityAlias(Base):
    __tablename__ = "entity_alias"
    __table_args__ = (UniqueConstraint("entity_id", "norm"), Index("ix_alias_norm", "norm"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entity.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(Text)
    norm: Mapped[str] = mapped_column(String(512))
    lang: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32), default="extraction")


class Work(Base):
    __tablename__ = "work"
    entity_id: Mapped[int] = mapped_column(ForeignKey("entity.id", ondelete="CASCADE"), primary_key=True)
    openalex_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    dois: Mapped[list[Any]] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    tier: Mapped[int] = mapped_column(SmallInteger, default=0)


# ---- L3 -------------------------------------------------------------------------

class Edge(Base):
    __tablename__ = "edge"
    __table_args__ = (
        UniqueConstraint("src", "dst", "type"),
        Index("ix_edge_dst_type", "dst", "type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    src: Mapped[int] = mapped_column(ForeignKey("entity.id", ondelete="CASCADE"), index=True)
    dst: Mapped[int] = mapped_column(ForeignKey("entity.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(24))
    attrs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    extraction_id: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Embedding(Base):
    __tablename__ = "embedding"
    entity_id: Mapped[int] = mapped_column(ForeignKey("entity.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    dim: Mapped[int] = mapped_column(Integer)
    vec: Mapped[bytes] = mapped_column(LargeBinary)  # float32, L2-normalised


class VectorCache(Base):
    """Embeddings by text hash, so rebuilds do not recompute unchanged vectors."""

    __tablename__ = "vector_cache"
    text_sha: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    vec: Mapped[bytes] = mapped_column(LargeBinary)


# ---- human decisions ---------------------------------------------------------------

class HumanDecision(Base):
    __tablename__ = "human_decision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    op: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---- operational tables -----------------------------------------------------------

class ReviewItem(Base):
    """Audit queue: normalisation merges, is_a candidates, contradictions, retro tags, synthesis."""

    __tablename__ = "review_item"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(512), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class Job(Base):
    __tablename__ = "job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    owner_pid: Mapped[int | None] = mapped_column(Integer)  # process running it (stale-job recovery)


class ReviewCard(Base):
    """FSRS card. Keyed by entity key so scheduling state survives rebuilds."""

    __tablename__ = "review_card"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(entity_key + q)[:32]
    entity_key: Mapped[str] = mapped_column(String(255), index=True)
    q: Mapped[str] = mapped_column(Text)
    a: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16))  # rxf | template | local_llm
    priority: Mapped[int] = mapped_column(Integer, default=0)  # user ideas first
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    due: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    introduced_at: Mapped[datetime | None] = mapped_column(DateTime)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)


class ReviewLog(Base):
    __tablename__ = "review_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[str] = mapped_column(String(64), index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AccessLog(Base):
    """Last time the user looked at / reviewed an entity (drives the forgetting signal)."""

    __tablename__ = "access_log"
    entity_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    count: Mapped[int] = mapped_column(Integer, default=0)


class KV(Base):
    """Small persistent state: tuned thresholds, last nightly run, community assignments."""

    __tablename__ = "kv"
    k: Mapped[str] = mapped_column(String(128), primary_key=True)
    v: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON)


DERIVED_TABLES = ("edge", "embedding", "entity_alias", "work", "entity")
