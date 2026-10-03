# SPDX-License-Identifier: Apache-2.0
"""MCP server for Claude Desktop (local stdio). A thin wrapper over the REST API; falls back to
in-process calls when the desktop app is not running.

Claude Desktop config (claude_desktop_config.json):
  {"mcpServers": {"rhizome": {"command": "rhizome-mcp"}}}
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import Client, connect

INSTRUCTIONS = """Rhizome is the user's personal literature asset graph (papers split into datasets, methods,
ideas and claims). Use rhz_recall before discussing an analysis plan or when the user pastes a draft
paragraph; prefer citing returned assets with their source paper and evidence location.
After discussing a paper, when the user says "save to Rhizome" / "存入 Rhizome", produce an RXF v1
YAML document following the Project instructions and call rhz_ingest. If validation fails, fix the
listed fields and call rhz_ingest again. For maintenance ("work through this week's review queue"),
page through rhz_queue, propose a decision per item with a one-line reason, and only call
rhz_decide after the user confirms."""

mcp = FastMCP("rhizome", instructions=INSTRUCTIONS)
_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = connect()
    return _client


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@mcp.tool()
def rhz_ingest(rxf_yaml: str, filename: str = "claude-desktop.yaml") -> str:
    """Store a paper discussion in Rhizome. `rxf_yaml` is a complete RXF v1 YAML document.
    Returns the work key, suspected hallucinated IDs, and related papers the user has read;
    on validation failure returns the error report to fix and resubmit."""
    return _j(client().ingest(rxf_yaml, filename))


@mcp.tool()
def rhz_recall(context: str, limit: int = 5) -> str:
    """Find assets the user has read but may have forgotten that are relevant to `context`
    (an analysis plan, a code snippet, a draft paragraph). Ranked by relevance x forgetting."""
    return _j(client().recall(context, limit))


@mcp.tool()
def rhz_search(query: str, types: str | None = None, organism: str | None = None, modality: str | None = None,
               year_min: int | None = None, limit: int = 10) -> str:
    """Hybrid search. `types`: comma-separated subset of work,dataset,method,idea,claim,topic."""
    return _j(client().search(query, types=types, organism=organism, modality=modality, year_min=year_min,
                              limit=limit))


@mcp.tool()
def rhz_get(entity: str) -> str:
    """Get a paper or asset card by numeric id or key (e.g. 'dataset:GSE12345')."""
    c = client()
    card = c.get(int(entity)) if entity.isdigit() else c.get_by_key(entity)
    if card and card.get("exports"):
        for ex in card["exports"]:
            ex.pop("raw", None)  # keep responses small; the structured fields are included
    return _j(card)


@mcp.tool()
def rhz_related(entity_id: int) -> str:
    """Papers / assets related to an entity (for a paper: which read papers relate and along which dimension)."""
    return _j(client().related(entity_id))


@mcp.tool()
def rhz_queue(kind: str | None = None, limit: int = 20, offset: int = 0) -> str:
    """Page through the review queue. kinds: merge, topic_relation, contradiction, retro_tag, synthesis.
    Each item lists the allowed actions and both sides' aliases and connections."""
    return _j(client().queue(kind, limit, offset))


@mcp.tool()
def rhz_decide(item_id: int, action: str, note: str | None = None) -> str:
    """Resolve a review item after the user confirmed. For synthesis items marked useful, `note`
    becomes the text of the new Idea (what the connection is and which problem it may help)."""
    return _j(client().resolve(item_id, action, note))


@mcp.tool()
def rhz_digest(days: int = 7) -> str:
    """Weekly digest: unlinked cross-field asset pairs and new contradictions, for you to explain."""
    return _j(client().digest(days))


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
    warm_up()
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
