# SPDX-License-Identifier: Apache-2.0
"""RXF v1 (Rhizome Exchange Format).

The Pydantic models are the source of truth; ``rxf-spec/schema/rxf-v1.schema.json`` is generated
from them (``rhz rxf schema``) and a contract test asserts the two never drift.
Field names are fixed English; values may be in any language.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RXF_VERSION = 1


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


PaperType = Literal["research", "tool", "resource", "benchmark", "review", "protocol"]


class Paper(_Strict):
    doi: str | None = Field(None, description="DOI without the https://doi.org/ prefix")
    title: str
    year: int | None = Field(None, ge=1600, le=2100)
    type: list[PaperType] = Field(default_factory=list)
    organisms: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    url: str | None = None

    @field_validator("doi")
    @classmethod
    def _strip_doi(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "DOI:"):
            if v.startswith(prefix):
                v = v[len(prefix):]
        return v


class TopicRef(_Strict):
    name: str
    relation: Literal["about", "applicable_to"]


class Claim(_Strict):
    text: str
    evidence_type: Literal["causal", "correlational", "speculative"]
    evidence: str = Field(description="Location in the paper, e.g. 'Fig. 3B' (required)")
    boundary: str | None = None
    logic_jump: bool = False
    stance: Literal["supports", "contradicts"] = "supports"


class Dataset(_Strict):
    accession: str | None = None
    database: Literal["GEO", "SRA", "CNGB", "ArrayExpress", "ENA", "Zenodo", "other"] | None = None
    name: str | None = None
    organism: str | None = None
    tissue: str | None = None
    modality: str | None = None
    scale: str | int | None = None
    public: bool | None = None
    role: Literal["uses", "produces"] = "uses"
    evidence: str | None = None

    @model_validator(mode="after")
    def _needs_identity(self) -> "Dataset":
        if not (self.accession or self.name):
            raise ValueError("dataset needs an accession or a name")
        return self


class Method(_Strict):
    name: str
    repo: str | None = None
    biotools: str | None = None
    role: Literal["proposes", "uses", "evaluates"] = "uses"
    io: str | None = None
    maintained: Literal["yes", "no", "unknown"] | bool | None = None
    modality: str | None = None
    extends: list[str] = Field(default_factory=list)
    evidence: str | None = None


class Transfer(_Strict):
    type: Literal["cross-species", "cross-modality", "cross-problem"]
    to: str | None = None
    barrier: str | None = None


class Idea(_Strict):
    text: str
    transfer: Transfer | None = None
    origin: Literal["model", "user"] = "model"


class Assets(_Strict):
    datasets: list[Dataset] = Field(default_factory=list)
    methods: list[Method] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list)


class Issue(_Strict):
    severity: Literal["minor", "major", "critical"]
    location: str | None = None
    text: str
    test: str | None = None


class UserInsight(_Strict):
    text: str
    links_to: list[str] = Field(default_factory=list)


class ReviewCard(_Strict):
    q: str
    a: str
    about: str | None = None


class RxfDocument(_Strict):
    rxf_version: Literal[1]
    prompt_version: str | None = None
    depth: Literal["light", "deep"] = "light"
    paper: Paper
    tldr: list[str] = Field(default_factory=list, max_length=5)
    topics: list[TopicRef] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    assets: Assets = Field(default_factory=Assets)
    issues: list[Issue] = Field(default_factory=list)
    user_insights: list[UserInsight] = Field(default_factory=list)
    review_cards: list[ReviewCard] = Field(default_factory=list)


SCHEMAS: dict[int, type[RxfDocument]] = {1: RxfDocument}


def json_schema(version: int = RXF_VERSION) -> dict:
    schema = SCHEMAS[version].model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://rhizome.local/rxf/v{version}.schema.json"
    schema["title"] = f"RXF v{version}"
    return schema
