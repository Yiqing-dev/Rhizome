# SPDX-License-Identifier: Apache-2.0
"""Parse and validate RXF documents: YAML -> JSON -> JSON Schema (by rxf_version) -> Pydantic."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

import jsonschema
import yaml

from ..i18n import _
from .schema import SCHEMAS, RxfDocument

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.S)


@dataclass
class ValidationProblem:
    path: str
    message: str


@dataclass
class LoadResult:
    doc: RxfDocument | None
    data: dict[str, Any] | None
    problems: list[ValidationProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.doc is not None and not self.problems


class _NoDatesLoader(yaml.SafeLoader):
    """Keep dates and yes/no-like scalars as written where it matters (accessions, years)."""


# Drop the timestamp resolver so "2025-01-01" stays a string.
_NoDatesLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def strip_fences(text: str) -> str:
    m = _FENCE.match(text)
    return m.group(1) if m else text


@lru_cache(maxsize=None)
def schema_for(version: int) -> dict:
    name = f"rxf-v{version}.schema.json"
    try:
        return json.loads(resources.files("rhizome.rxf").joinpath("schemas", name).read_text("utf-8"))
    except FileNotFoundError:  # pragma: no cover - dev checkout before `rhz rxf schema`
        from .schema import json_schema

        return json_schema(version)


def _path(err: jsonschema.ValidationError) -> str:
    parts = [str(p) for p in err.absolute_path]
    return ".".join(parts) if parts else "(root)"


def load_rxf(text: str) -> LoadResult:
    text = strip_fences(text.lstrip("﻿"))
    try:
        data = yaml.load(text, Loader=_NoDatesLoader)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "(yaml)"
        return LoadResult(None, None, [ValidationProblem(where, _("rxf.yaml_error", detail=str(e)))])
    if not isinstance(data, dict):
        return LoadResult(None, None, [ValidationProblem("(root)", _("rxf.not_mapping"))])

    # YAML -> JSON round trip normalises types the schema should see (no Python-only objects).
    data = json.loads(json.dumps(data, default=str))
    version = data.get("rxf_version")
    if version not in SCHEMAS:
        return LoadResult(None, data, [ValidationProblem("rxf_version",
                                                         _("rxf.unknown_version", version=version))])

    validator = jsonschema.Draft202012Validator(schema_for(version))
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    problems = [ValidationProblem(_path(e), e.message) for e in errors]
    if problems:
        return LoadResult(None, data, problems)
    try:
        doc = SCHEMAS[version].model_validate(data)
    except Exception as e:  # pydantic validators the JSON Schema cannot express
        from pydantic import ValidationError

        if isinstance(e, ValidationError):
            return LoadResult(None, data, [
                ValidationProblem(".".join(str(x) for x in err["loc"]) or "(root)", err["msg"])
                for err in e.errors()
            ])
        raise
    return LoadResult(doc, data, [])


def error_report(filename: str, problems: list[ValidationProblem]) -> str:
    lines = [_("rxf.report_title", file=filename), ""]
    lines += [f"- {p.path}: {p.message}" for p in problems]
    lines += ["", _("rxf.report_hint")]
    return "\n".join(lines) + "\n"
