# SPDX-License-Identifier: Apache-2.0
"""MCP server for Claude Desktop (local stdio). A thin wrapper over the REST API; falls back to
in-process calls when the desktop app is not running.

Claude Desktop keeps this process alive for its whole session, while the desktop app starts and
stops (with a new port and token each time). So the connection is re-resolved whenever the app's
marker (server.json) or token changes, and a refused connection or a 401 reconnects once.

Claude Desktop config (claude_desktop_config.json):
  {"mcpServers": {"rhizome": {"command": "rhizome-mcp"}}}
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import httpx
from mcp.server.fastmcp import FastMCP

from .client import Client, connect
from .config import get_settings

log = logging.getLogger(__name__)

INSTRUCTIONS = """Rhizome is the user's personal literature asset graph (papers split into datasets, methods,
ideas and claims). Use rhz_recall before discussing an analysis plan or when the user pastes a draft
paragraph; prefer citing returned assets with their source paper and evidence location.
After discussing a paper, when the user says "save to Rhizome" / "存入 Rhizome", produce an RXF v1
YAML document following the Project instructions (if this conversation has none, call rhz_rxf_guide
first: it returns the RXF skeleton, the rules and the user's topic vocabulary) and call rhz_ingest. If validation fails, fix the
listed fields and call rhz_ingest again. For maintenance ("work through this week's review queue"),
page through rhz_queue, propose a decision per item with a one-line reason, and only call
rhz_decide after the user confirms."""

mcp = FastMCP("rhizome", instructions=INSTRUCTIONS)
_client: Client | None = None
_signature: tuple | None = None


def _app_signature() -> tuple:
    """What identifies the running desktop app: its marker (url, pid) and its token."""
    from .config import token_path

    st = get_settings()
    out: list[str | None] = []
    for p in (st.data_dir / "server.json", token_path(st)):
        try:
            out.append(p.read_text("utf-8-sig"))
        except OSError:
            out.append(None)
    return tuple(out)


def client(force: bool = False) -> Client:
    global _client, _signature
    sig = _app_signature()
    if force or _client is None or sig != _signature:
        _client, _signature = connect(), sig
        log.info("rhizome-mcp: using %s", type(_client).__name__)
        if type(_client).__name__ == "LocalClient":  # in-process searches: warm the index now
            import threading

            from .pipeline.graph import VECTORS

            threading.Thread(target=VECTORS.preload, daemon=True, name="rhizome-preload").start()
    return _client


def _call(fn: Callable[[Client], Any]) -> Any:
    """Run a tool against the current client. A refused connection (the app was closed or restarted)
    or a 401 (new token) never reached the handler, so reconnecting and retrying once is safe even
    for ingest; timeouts and 5xx are not retried."""
    try:
        return fn(client())
    except httpx.ConnectError:
        pass
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 401:
            raise
    return fn(client(force=True))


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@mcp.tool()
def rhz_ingest(rxf_yaml: str, filename: str = "claude-desktop.yaml", replace: bool = False) -> str:
    """Store a paper discussion in Rhizome. `rxf_yaml` is a complete RXF v1 YAML document.
    Returns the work key, suspected hallucinated IDs, and related papers the user has read;
    on validation failure returns the error report to fix and resubmit. If the result lists
    `existing_exports`, the paper was stored before: when this export is a correction of that one
    (the user fixed something), ask the user and call again with replace=true."""
    return _j(_call(lambda c: c.ingest(rxf_yaml, filename, replace=replace)))


@mcp.tool()
def rhz_rxf_guide(lang: str | None = None) -> str:
    """The RXF export instructions (skeleton + rules) and the user's current topic vocabulary.
    Call it before writing an RXF document when the conversation has no Rhizome Project
    instructions. `lang`: zh_CN or en (default: the user's interface language)."""
    from .rxf.guide import instructions

    vocab = _call(lambda c: c.vocab())
    return instructions(lang) + "\n\n## rhizome-vocab.yaml\n\n```yaml\n" + vocab.strip() + "\n```\n"


@mcp.tool()
def rhz_recall(context: str, limit: int = 5) -> str:
    """Find assets the user has read but may have forgotten that are relevant to `context`
    (an analysis plan, a code snippet, a draft paragraph). Ranked by relevance x forgetting."""
    return _j(_call(lambda c: c.recall(context, limit)))


@mcp.tool()
def rhz_search(query: str, types: str | None = None, organism: str | None = None, modality: str | None = None,
               year_min: int | None = None, limit: int = 10) -> str:
    """Hybrid search. `types`: comma-separated subset of work,dataset,method,idea,claim,topic."""
    return _j(_call(lambda c: c.search(query, types=types, organism=organism, modality=modality,
                                       year_min=year_min, limit=limit)))


@mcp.tool()
def rhz_get(entity: str) -> str:
    """Get a paper or asset card by key (e.g. 'dataset:GSE12345'; keys are the durable handles) or
    by a numeric id from an earlier result."""
    card = _call(lambda c: c.get(int(entity)) if entity.isdigit() else c.get_by_key(entity))
    if card and card.get("exports"):
        for ex in card["exports"]:
            ex.pop("raw", None)  # keep responses small; the structured fields are included
    return _j(card)


@mcp.tool()
def rhz_related(entity: str) -> str:
    """Papers / assets related to an entity (for a paper: which read papers relate and along which
    dimension). `entity` is a key (e.g. 'work:doi:10.1/x', 'method:repo:github.com/a/b'; keys are
    the durable handles) or a numeric id from an earlier result."""
    def run(c):
        if entity.isdigit():
            return c.related(int(entity))
        card = c.get_by_key(entity)
        return c.related(card["id"]) if card else []
    return _j(_call(run))


@mcp.tool()
def rhz_queue(kind: str | None = None, limit: int = 20, offset: int = 0) -> str:
    """Page through the review queue. kinds: merge, topic_relation, contradiction, retro_tag, synthesis.
    Each item lists the allowed actions and both sides' aliases and connections."""
    return _j(_call(lambda c: c.queue(kind, limit, offset)))


@mcp.tool()
def rhz_decide(item_id: int, action: str, note: str | None = None) -> str:
    """Resolve a review item after the user confirmed. For synthesis items marked useful, `note`
    becomes the text of the new Idea (what the connection is and which problem it may help)."""
    return _j(_call(lambda c: c.resolve(item_id, action, note)))


@mcp.tool()
def rhz_correct(op: str, payload: dict) -> str:
    """Correct the library, only after the user explicitly confirmed this exact change.
    op / payload (entities by key, e.g. 'method:repo:github.com/a/b'):
      rename {key, name} · add_alias {key, alias} · edit_text {key, text} ·
      merge {from, into} (same type) · reject_entity {key} (a wrong / hallucinated asset).
    Returns the decision id; tell the user it can be undone with rhz_undo."""
    if op not in ("rename", "add_alias", "edit_text", "merge", "reject_entity"):
        return _j({"ok": False, "error": f"op {op} is not available here"})
    return _j(_call(lambda c: c.decide(op, payload)))


@mcp.tool()
def rhz_undo(decision_id: int) -> str:
    """Revert one of the user's decisions (a merge, rename, rejection, ...), after the user asked
    for it. The library is rebuilt without it."""
    return _j(_call(lambda c: c.revoke(decision_id)))


@mcp.tool()
def rhz_digest(days: int = 7) -> str:
    """Weekly digest: unlinked cross-field asset pairs and new contradictions, for you to explain."""
    return _j(_call(lambda c: c.digest(days)))


def warm_up() -> None:
    """Load native extensions and open the library *before* the stdio transport starts.

    On Windows, lazily importing numpy's C extension inside the first tool call, while the stdio
    reader thread is blocked reading the stdin pipe, deadlocks the process (seen in CI). Importing
    everything up front also makes the first tool call fast."""
    import numpy  # noqa: F401

    from .pipeline import graph  # noqa: F401  (numpy-backed vector index)
    from .services import recall, search, synthesis, views  # noqa: F401

    client()


def main() -> None:
    import sys

    from .config import LibraryNotFound
    from .db.session import SchemaTooNew
    from .logging_setup import setup_logging

    try:
        setup_logging(get_settings(), role="mcp")
        warm_up()
    except (SchemaTooNew, LibraryNotFound) as e:  # Claude Desktop shows the server's stderr in its MCP log
        print(str(e), file=sys.stderr)
        raise SystemExit(3 if isinstance(e, SchemaTooNew) else 4) from None
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
