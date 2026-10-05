# SPDX-License-Identifier: Apache-2.0
"""Parse and validate RXF documents: YAML -> JSON -> JSON Schema (by rxf_version) -> Pydantic."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import jsonschema
import yaml

from ..i18n import _
from .repair import repair as _repair
from .schema import SCHEMAS, RxfDocument, declared_ids, json_schema, reference_problems

# a fenced block (``` or ~~~, any info string) anywhere in the text: chat clients wrap exports in
# prose ("Here is the export:" ... "Let me know if ...")
_FENCE_BLOCK = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n(.*?)\n[ \t]*\1[ \t]*$", re.S | re.M)
_RXF_LINE = re.compile(r"^[ \t]*rxf_version[ \t]*:", re.M)


@dataclass
class ValidationProblem:
    path: str
    message: str
    # unexpected_field | missing_field | bad_value | bad_type | bad_reference | not_a_node | duplicate_id
    # | yaml | other ; `field` / `value` feed the grouped report
    kind: str = "other"
    field: str | None = None
    value: str | None = None


@dataclass
class LoadResult:
    doc: RxfDocument | None
    data: dict[str, Any] | None
    problems: list[ValidationProblem] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)  # applied (repair=True) ...
    repairable: list[str] = field(default_factory=list)  # ... or that would make a failed document valid
    known_ids: list[str] = field(default_factory=list)  # ids the document declares (for reference errors)

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


class RxfEncodingError(ValueError):
    """The bytes are not UTF-8 (or UTF-16 with a BOM): the user must save the export as UTF-8."""


_CJK_BYTES = re.compile(rb"[\x81-\xfe][\x40-\xfe]")


def decode_rxf(data: bytes) -> str:
    """Text of an export file: UTF-8 (with or without BOM) or UTF-16 with a BOM. A file that is
    clearly GB18030 (a Chinese editor's default) is decoded with that codec; anything else is a
    localised error telling the user to save as UTF-8, instead of a traceback."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16").lstrip("\ufeff")
        except UnicodeDecodeError as e:
            raise RxfEncodingError(_("rxf.encoding", detail="UTF-16")) from e
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        if len(_CJK_BYTES.findall(data)) >= 4:
            try:
                return data.decode("gb18030")
            except UnicodeDecodeError:
                pass
        raise RxfEncodingError(_("rxf.encoding", detail=f"byte {e.start}")) from e


def strip_fences(text: str) -> str:
    """The RXF document inside the text: the first fenced block that contains rxf_version, else
    (no fences) the text from its rxf_version line on, so prose around an export is ignored."""
    for m in _FENCE_BLOCK.finditer(text):
        if _RXF_LINE.search(m.group(2)):
            return m.group(2)
    if "```" not in text and "~~~" not in text:
        m = _RXF_LINE.search(text)
        if m and m.start() > 0:
            return text[m.start():]
    return text


@lru_cache(maxsize=None)
def schema_for(version: int) -> dict:
    """Built from the Pydantic models, so there is no second copy that could drift."""
    return json_schema(version)


def _enum_of(schema: dict) -> list[str]:
    if "enum" in schema:
        return [str(x) for x in schema["enum"]]
    for sub in schema.get("anyOf", []):
        if "enum" in sub:
            return [str(x) for x in sub["enum"]]
    return []


def _path(parts) -> str:
    parts = [str(p) for p in parts]
    return ".".join(parts) if parts else "(root)"


def _problems(err: jsonschema.ValidationError) -> list[ValidationProblem]:
    """One structured problem per cause (an object with three unknown keys gives three)."""
    path = list(err.absolute_path)
    v, inst = err.validator, err.instance
    if v == "additionalProperties" and isinstance(inst, dict):
        allowed = set((err.schema or {}).get("properties", {}))
        return [ValidationProblem(_path(path), err.message, "unexpected_field", field=str(k))
                for k in inst if k not in allowed]
    if v == "required" and isinstance(inst, dict):
        props = (err.schema or {}).get("properties", {})
        out = []
        for k in err.validator_value:
            if k in inst:
                continue
            allowed = _enum_of(props.get(k, {}))  # "add relation" alone costs the model a round trip
            out.append(ValidationProblem(_path(path), err.message, "missing_field", field=str(k),
                                         value=", ".join(allowed) if allowed else None))
        return out
    name = str(path[-1]) if path and not isinstance(path[-1], int) else None
    if v == "type" and path and isinstance(path[-1], int):
        # an item of a list is of the wrong type: name the list and what an item needs
        name = str(path[-2]) if len(path) > 1 else None
        req = (err.schema or {}).get("required") or []
        expected = str(err.validator_value) + (f" with {', '.join(req)}" if req else "")
        return [ValidationProblem(_path(path), err.message, "bad_type", field=name, value=expected)]
    if v == "enum":
        return [ValidationProblem(_path(path), err.message, "bad_value", field=name,
                                  value=", ".join(str(x) for x in err.validator_value))]
    if v == "const":
        return [ValidationProblem(_path(path), err.message, "bad_value", field=name, value=str(err.validator_value))]
    if v == "type":
        return [ValidationProblem(_path(path), err.message, "bad_type", field=name, value=str(err.validator_value))]
    if v in ("anyOf", "oneOf"):  # optional fields are anyOf[<real schema>, null]: report the real one
        real = [e for e in err.context or [] if not (e.validator == "type" and e.validator_value == "null")]
        if real:
            sub = min(real, key=lambda e: len(list(e.relative_schema_path)))
            if sub.validator == "type":
                return [ValidationProblem(_path(path), err.message, "bad_type", field=name, value=str(sub.validator_value))]
            return _problems(sub)
    return [ValidationProblem(_path(path), err.message, "other", field=name)]


def load_rxf(text: str, repair: bool = False) -> LoadResult:
    """Parse and validate. ``repair=True`` applies the known-drift fixes first (see repair.py)."""
    text = strip_fences(text.lstrip("\ufeff"))
    try:
        data = yaml.load(text, Loader=_NoDatesLoader)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "(yaml)"
        return LoadResult(None, None, [ValidationProblem(where, _("rxf.yaml_error", detail=str(e)), "yaml")])
    if not isinstance(data, dict):
        return LoadResult(None, None, [ValidationProblem("(root)", _("rxf.not_mapping"), "yaml")])

    # YAML -> JSON round trip normalises types the schema should see (no Python-only objects).
    data = json.loads(json.dumps(data, default=str))
    version = data.get("rxf_version")
    if version not in SCHEMAS:
        return LoadResult(None, data, [ValidationProblem("rxf_version", _("rxf.unknown_version", version=version),
                                                         "other", field="rxf_version")])
    applied: list[str] = []
    if repair:
        data, applied = _repair(data)
    res = _validate(version, data)
    res.repairs = applied
    if not res.ok and not repair:
        fixed, codes = _repair(data)
        if codes and _validate(version, fixed).ok:
            res.repairable = codes
    return res


def _validate(version: int, data: dict[str, Any]) -> LoadResult:
    validator = jsonschema.Draft202012Validator(schema_for(version))
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    problems = [p for e in errors for p in _problems(e)]
    problems += [ValidationProblem(path, kind, kind, value=value) for path, kind, value in reference_problems(data)]
    if problems:
        return LoadResult(None, data, problems, known_ids=list(declared_ids(data)))
    try:
        doc = SCHEMAS[version].model_validate(data)
    except Exception as e:  # pydantic validators the JSON Schema cannot express
        from pydantic import ValidationError

        if isinstance(e, ValidationError):
            return LoadResult(None, data, [ValidationProblem(_path(err["loc"]), err["msg"]) for err in e.errors()])
        raise
    return LoadResult(doc, data, [])


# ---- grouped error report -------------------------------------------------------------
# The report is pasted back into the chat so the AI can fix the export: one line per cause
# (path pattern + field), not one line per occurrence.

_IDX = re.compile(r"(?<=\.)\d+(?=\.|$)|^\d+(?=\.|$)")

# (path pattern, kind, field) -> specific fix hint key for known export drift
_KNOWN = {
    ("topics.*", "unexpected_field", "new"): "rxf.fix.topic_new",
    # misplaced or renamed, never "remove": the data is right, the place or the key is wrong
    ("(root)", "unexpected_field", "datasets"): "rxf.fix.move_assets",
    ("(root)", "unexpected_field", "methods"): "rxf.fix.move_assets",
    ("(root)", "unexpected_field", "ideas"): "rxf.fix.move_assets",
    ("claims.*", "unexpected_field", "claim"): "rxf.fix.claim_text",
    ("claims.*", "unexpected_field", "statement"): "rxf.fix.claim_text",
    ("review_cards.*", "unexpected_field", "question"): "rxf.fix.card_q",
    ("review_cards.*", "unexpected_field", "answer"): "rxf.fix.card_a",
    ("paper", "unexpected_field", "journal"): "rxf.fix.paper_venue",
    ("user_insights.*", "unexpected_field", "links"): "rxf.fix.links_to",
    ("assets.ideas.*", "unexpected_field", "type"): "rxf.fix.transfer",
    ("assets.ideas.*", "unexpected_field", "to"): "rxf.fix.transfer",
    ("assets.ideas.*", "unexpected_field", "barrier"): "rxf.fix.transfer",
    ("assets.ideas.*", "unexpected_field", "transfer_type"): "rxf.fix.transfer",
    ("assets.ideas.*", "unexpected_field", "transfer_to"): "rxf.fix.transfer",
    ("assets.ideas.*", "unexpected_field", "transfer_barrier"): "rxf.fix.transfer",
    ("assets.ideas.*.transfer", "bad_type", "transfer"): "rxf.fix.transfer",
    ("claims.*", "missing_field", "evidence"): "rxf.fix.claim_evidence",
}


def _pattern(path: str) -> str:
    return _IDX.sub("*", path)


def group_problems(problems: list[ValidationProblem]) -> list[dict[str, Any]]:
    """Merge problems with the same cause: [{pattern, paths, kind, field, values, message}] in first-seen order."""
    groups: dict[tuple, dict[str, Any]] = {}
    for p in problems:
        pat = _pattern(p.path)
        same_value = p.kind in ("bad_value", "bad_type")  # the allowed set/type is part of the cause
        key = (pat, p.kind, p.field, p.value if same_value else None,
               p.message if p.kind in ("other", "yaml") else None)
        g = groups.setdefault(key, {"pattern": pat, "paths": [], "kind": p.kind, "field": p.field,
                                    "values": [], "message": p.message})
        g["paths"].append(p.path)
        if p.value is not None and p.value not in g["values"]:
            g["values"].append(p.value)
    return list(groups.values())


def _merge_known(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One known drift shows up as several schema errors (a flattened transfer: two unknown fields
    plus a wrong type); fold them into one cause, counted per affected item."""
    out: list[dict[str, Any]] = []
    merged: dict[str, dict[str, Any]] = {}
    for g in groups:
        hint = _KNOWN.get((g["pattern"], g["kind"], g["field"]))
        if hint != "rxf.fix.transfer":
            out.append(g)
            continue
        items = [p.removesuffix(".transfer") for p in g["paths"]]
        m = merged.get(hint)
        if m is None:
            m = merged[hint] = {"pattern": "assets.ideas.*", "paths": [], "kind": "transfer", "field": "transfer",
                                "values": [], "message": ""}
            out.append(m)
        m["paths"] += [p for p in items if p not in m["paths"]]
        if g["field"] not in m["values"]:
            m["values"].append(g["field"])
    return out


def _describe(g: dict[str, Any]) -> tuple[str, str | None]:
    kind, f, vals = g["kind"], g["field"] or "", g["values"]
    if kind == "transfer":
        return _("rxf.problem.transfer", values=", ".join(vals)), _("rxf.fix.transfer", field="transfer")
    if kind in ("unexpected_field", "missing_field", "bad_reference", "not_a_node", "duplicate_id"):
        what = _(f"rxf.problem.{kind}", field=f, values=", ".join(vals))
        hint = _KNOWN.get((g["pattern"], kind, f), f"rxf.fix.{kind}")
        if kind == "missing_field" and vals and hint == "rxf.fix.missing_field":
            hint = "rxf.fix.missing_field_values"
        return what, _(hint, field=f, values=" | ".join(vals))
    if kind == "bad_value":
        return _("rxf.problem.bad_value", field=f, values=" | ".join(vals)), None
    if kind == "bad_type":
        hint = _KNOWN.get((g["pattern"], kind, f))
        return _("rxf.problem.bad_type", field=f, values=", ".join(vals)), (_(hint, field=f) if hint else None)
    return g["message"], None


def error_report(filename: str, problems: list[ValidationProblem], repairable: list[str] | None = None,
                 known_ids: list[str] | None = None) -> str:
    groups = _merge_known(group_problems(problems))
    lines = [_("rxf.report_title", file=filename), _("rxf.report_summary", problems=len(problems), causes=len(groups)), ""]
    for g in groups:
        what, hint = _describe(g)
        where = g["paths"][0] if len(g["paths"]) == 1 else g["pattern"]
        count = "" if len(g["paths"]) == 1 else _("rxf.report_count", n=len(g["paths"]))
        lines.append(f"- {where}: {what}{count}")
        if hint:
            lines.append(f"  {_('rxf.report_fix')}{hint}")
    if known_ids and any(g["kind"] == "bad_reference" for g in groups):
        lines += ["", _("rxf.report_known_ids", ids=", ".join(known_ids))]
    lines += ["", _("rxf.report_hint")]
    if len(groups) >= 4:  # many causes: the model is better off re-reading the skeleton
        lines.append(_("rxf.report_skeleton"))
    if repairable:
        lines += ["", _("rxf.report_repairable", fixes="; ".join(_(f"rxf.repair.{c}") for c in repairable),
                        file=filename)]
    return "\n".join(lines) + "\n"


def report_for(filename: str, res: LoadResult) -> str:
    return error_report(filename, res.problems, res.repairable, res.known_ids)
