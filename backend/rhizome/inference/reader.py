# SPDX-License-Identifier: Apache-2.0
"""Read a new paper with the API: identify it, check the library for it, review it, export RXF,
validate, ingest.

This is the one place where paper text leaves the machine for more than a narrow judgement, so it
is opt-in twice: the Anthropic backend must be selected and a key stored, and every run is started
by the user for a named file. One conversation carries the whole paper (the PDF is sent once as a
document block and cached): a short identification answer first, so a paper the library already
has costs one small request; then the in-depth review under the user's own deep-review prompt (deep-review-v2, shipped in rhizome/data); then the RXF
export under the same instructions the user's chat Project holds, with the library vocabulary, so
topics merge instead of multiplying. The YAML is validated like any inbox file and sent back once
with the error report when it is not; the ingest is the ordinary one (L0 keeps the bytes, the PDF
is attached, recall runs). The review report is kept beside the export and shown on the paper.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import date
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Entity, Extraction
from . import prompts
from .agent import FALLBACK_BETA

log = logging.getLogger(__name__)

STEPS = ("identify", "dedupe", "review", "export", "validate", "ingest")
PROMPT_VERSION = "deep-review-v2"  # the user's review prompt, rhizome/data/deep-review-v2.md
MAX_OUTPUT = 16000
# A request may be 32 MB; an inline PDF is base64 (4/3 of its size) plus the prompts, so PDFs above
# this go through the Files API instead (one upload, referenced by id, deleted when the read ends).
INLINE_PDF_BYTES = 22 * 1024 * 1024
REPAIR_ROUNDS = 1


class ReaderUnavailable(RuntimeError):
    """The feature cannot run here; ``code`` says why (settings page wording keys off it)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ReaderError(RuntimeError):
    """The API refused or failed; the message never contains the key."""


def availability() -> dict[str, Any]:
    """Whether a read can start now: the Anthropic backend selected, the package present, a key."""
    from ..config import get_settings
    from ..secrets import get_secret
    from .anthropic_api import available

    st = get_settings()
    if st.inference_backend != "anthropic":
        return {"enabled": False, "reason": "backend"}
    if not available():
        return {"enabled": False, "reason": "sdk_missing"}
    if not get_secret("anthropic_api_key"):
        return {"enabled": False, "reason": "no_key"}
    return {"enabled": True, "reason": None, "model": st.anthropic_model}


def slug_of(filename: str) -> str:
    stem = re.sub(r"\.pdf$", "", filename, flags=re.I)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or "paper"
    return stem[:80]


class _Usage:
    def __init__(self) -> None:
        self.input = self.output = self.cache_read = self.cache_write = 0
        self.calls = 0

    def add(self, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        self.calls += 1
        self.input += getattr(u, "input_tokens", 0) or 0
        self.output += getattr(u, "output_tokens", 0) or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

    def to_dict(self) -> dict[str, int]:
        return {"calls": self.calls, "input": self.input, "output": self.output,
                "cache_read": self.cache_read, "cache_write": self.cache_write}


def _text(resp: Any) -> str:
    return "".join(b.text for b in getattr(resp, "content", []) if getattr(b, "type", None) == "text")


def _strip_fence(text: str) -> str:
    m = re.search(r"```(?:ya?ml)?\s*\n(.*?)\n```", text, flags=re.S)
    return (m.group(1) if m else text).strip() + "\n"


def find_existing(s: Session, ident: dict[str, Any]) -> Entity | None:
    """The work this paper already is in the library, by DOI, arXiv id or title."""
    from ..external.ids import normalize_doi
    from ..pipeline.graph import Graph
    from ..text import norm

    g = Graph(s)
    dois = []
    if ident.get("doi"):
        dois.append(normalize_doi(str(ident["doi"])))
    if ident.get("arxiv_id"):
        dois.append(normalize_doi(f"10.48550/arXiv.{str(ident['arxiv_id']).strip()}"))
    for d in dois:
        if not d:
            continue
        hit = g.by_key(f"work:doi:{d}") or g.by_alias("work", d) or g.by_external_id("work", d)
        if hit is not None:
            return hit
    title = (ident.get("title") or "").strip()
    if title:
        hit = g.by_alias("work", title)
        if hit is not None:
            return hit
        want = norm(title)
        if want:
            for w in s.execute(select(Entity).where(Entity.type == "work", Entity.status != "rejected")).scalars():
                if norm(w.canonical_name) == want:
                    return w
    return None


def _call(client: Any, usage: _Usage, **kw: Any) -> Any:
    import anthropic

    try:
        resp = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kw)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError,
            anthropic.NotFoundError, anthropic.RateLimitError, anthropic.APIStatusError,
            anthropic.APIConnectionError) as e:
        raise ReaderError(f"{type(e).__name__}: {getattr(e, 'message', None) or e}") from e
    usage.add(resp)
    if resp.stop_reason == "refusal":
        raise ReaderError("refusal")
    return resp


def _upload(client: Any, pdf: bytes, filename: str) -> str:
    import anthropic

    try:
        return client.files.upload(file=(filename, pdf, "application/pdf")).id
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        raise ReaderError(f"upload: {type(e).__name__}: {getattr(e, 'message', None) or e}") from e


def _delete_quietly(client: Any, file_id: str) -> None:
    try:
        client.files.delete(file_id)
    except Exception as e:  # the read is done either way; a leftover file only costs storage
        log.warning("could not delete uploaded file %s: %s", file_id, e)


def read_paper(s: Session, pdf: bytes, filename: str, *, force: bool = False, client: Any = None,
               model: str | None = None, lang: str | None = None, today: date | None = None,
               progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Run the whole pipeline for one PDF. Returns ``{"status": "duplicate" | "done" | "invalid", ...}``;
    raises ReaderUnavailable / ReaderError. ``client`` and ``model`` are for tests and the CLI.
    A PDF up to INLINE_PDF_BYTES travels inline; a bigger one is uploaded once with the Files API,
    referenced by id in every turn, and deleted when the read ends however it ends."""
    from ..config import get_settings
    from ..pipeline.ingest import MAX_PDF_BYTES
    from ..secrets import get_secret

    if not pdf.startswith(b"%PDF"):
        raise ReaderError("not a PDF")
    if len(pdf) > MAX_PDF_BYTES:
        raise ReaderError("PDF too large")
    model = model or get_settings().anthropic_model
    if client is None:
        avail = availability()
        if not avail["enabled"]:
            raise ReaderUnavailable(avail["reason"])
        import anthropic

        client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"), max_retries=2, timeout=600.0)
    file_id = None
    try:
        if len(pdf) > INLINE_PDF_BYTES:
            file_id = _upload(client, pdf, filename)
            source: dict[str, Any] = {"type": "file", "file_id": file_id}
        else:
            source = {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(pdf).decode("ascii")}
        out = _read(s, pdf, filename, source, force=force, client=client, model=model, lang=lang, today=today,
                    progress=progress)
    finally:
        if file_id:
            _delete_quietly(client, file_id)
    out["transport"] = "file" if file_id else "inline"
    return out


def _read(s: Session, pdf: bytes, filename: str, source: dict[str, Any], *, force: bool, client: Any, model: str,
          lang: str | None, today: date | None, progress: Callable[[str], None] | None) -> dict[str, Any]:
    from ..config import ui_language
    from ..pipeline.ingest import ingest_text
    from ..rxf.guide import instructions
    from ..rxf.loader import error_report, load_rxf
    from ..services.vocab import export_vocab

    lang = lang or ui_language()
    step = progress or (lambda _s: None)
    usage = _Usage()
    slug = slug_of(filename)
    language = "zh" if lang.lower().startswith("zh") else "en"

    system = [
        {"type": "text", "text": prompts.READER_SYSTEM.format(language=prompts.LANGUAGE_NAME[language])},
        {"type": "text", "text": "The RXF export instructions the user's chat Project holds (follow them when asked to export):\n\n"
                                 + instructions(lang) + "\n\nThe library vocabulary (rhizome-vocab.yaml):\n\n" + export_vocab(s),
         "cache_control": {"type": "ephemeral"}},
    ]
    doc_block = {"type": "document", "source": source, "title": filename, "cache_control": {"type": "ephemeral"}}
    messages: list[dict[str, Any]] = [{"role": "user", "content": [doc_block, {"type": "text", "text": prompts.READER_IDENTIFY}]}]

    # 1. identify
    step("identify")
    resp = _call(client, usage, model=model, max_tokens=1024, system=system, messages=messages,
                 output_config={"effort": "low", "format": {"type": "json_schema", "schema": prompts.READER_IDENTITY_SCHEMA}})
    try:
        ident = json.loads(_text(resp))
    except json.JSONDecodeError:
        raise ReaderError("unparseable identification") from None
    if not isinstance(ident, dict):
        raise ReaderError("unparseable identification")
    messages.append({"role": "assistant", "content": resp.content})

    # 2. the library already has it?
    step("dedupe")
    existing = find_existing(s, ident)
    if existing is not None and not force:
        return {"status": "duplicate", "paper": ident, "work": {"id": existing.id, "key": existing.key, "name": existing.canonical_name},
                "usage": usage.to_dict(), "model": model}

    # 3. review
    step("review")
    messages.append({"role": "user", "content": prompts.review_prompt()})
    resp = _call(client, usage, model=model, max_tokens=MAX_OUTPUT, system=system, messages=messages,
                 output_config={"effort": "high"})
    report = _text(resp).strip()
    if not report or resp.stop_reason == "max_tokens":
        raise ReaderError("review " + ("truncated" if resp.stop_reason == "max_tokens" else "empty"))
    messages.append({"role": "assistant", "content": resp.content})

    # 4. export, 5. validate (one repair round)
    step("export")
    messages.append({"role": "user", "content": prompts.READER_EXPORT.format(
        today=(today or date.today()).isoformat(), language=language, prompt_version=PROMPT_VERSION)})
    yaml_text, res = "", None
    for attempt in range(REPAIR_ROUNDS + 1):
        resp = _call(client, usage, model=model, max_tokens=MAX_OUTPUT, system=system, messages=messages,
                     output_config={"effort": "medium"})
        yaml_text = _strip_fence(_text(resp))
        step("validate")
        res = load_rxf(yaml_text)
        if res.ok or attempt == REPAIR_ROUNDS:
            break
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": prompts.READER_REPAIR.format(
            report=error_report(f"{slug}.yaml", res.problems))})
        step("export")
    assert res is not None
    if not res.ok:
        return {"status": "invalid", "paper": ident, "report": report, "rxf": yaml_text,
                "errors": error_report(f"{slug}.yaml", res.problems), "usage": usage.to_dict(), "model": model}

    # 6. ingest like any inbox file, then keep the report beside the export
    step("ingest")
    out = ingest_text(s, yaml_text, f"{slug}.yaml", pdf)
    if not out.ok:
        return {"status": "invalid", "paper": ident, "report": report, "rxf": yaml_text, "errors": out.report,
                "usage": usage.to_dict(), "model": model}
    _attach_report(s, out.work_key, slug, report, model, usage)
    return {"status": "done", "paper": ident, "ingest": out.to_dict(), "work": {"id": out.work_id, "key": out.work_key},
            "usage": usage.to_dict(), "model": model}


def _attach_report(s: Session, work_key: str | None, slug: str, report: str, model: str, usage: _Usage) -> None:
    from .. import rawstore

    if not work_key:
        return
    ex = s.execute(select(Extraction).where(Extraction.work_key == work_key, Extraction.kind == "rxf", Extraction.is_current)
                   .order_by(Extraction.id.desc())).scalars().first()
    if ex is None:
        return
    sha = rawstore.put(s, report.encode("utf-8"), "report", f"{slug}.md", work_key)
    ex.meta = {**(ex.meta or {}), "report_sha": sha,
               "reader": {"model": model, "prompt_version": PROMPT_VERSION, "usage": usage.to_dict(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}}
