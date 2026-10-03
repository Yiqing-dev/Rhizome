# SPDX-License-Identifier: Apache-2.0
"""T0: OpenAlex metadata, abstract and references."""

from __future__ import annotations

import logging

import httpx

from ..config import get_settings
from .http import client

log = logging.getLogger(__name__)
API = "https://api.openalex.org"


def _abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos: list[tuple[int, str]] = [(i, w) for w, idxs in inv.items() for i in idxs]
    return " ".join(w for _, w in sorted(pos))


def short_id(openalex_url: str) -> str:
    return openalex_url.rsplit("/", 1)[-1]


def fetch_work(doi: str | None = None, openalex_id: str | None = None) -> dict | None:
    """Return a trimmed record (stored verbatim as an L1 ``openalex`` extraction) or None."""
    s = get_settings()
    if s.offline or not (doi or openalex_id):
        return None
    ref = openalex_id or f"doi:{doi}"
    params = {"mailto": s.contact_email} if s.contact_email else {}
    try:
        with client() as c:
            r = c.get(f"{API}/works/{ref}", params=params)
        if r.status_code != 200:
            return None
        w = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("OpenAlex lookup failed for %s: %s", ref, e)
        return None
    return {
        "id": short_id(w["id"]),
        "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None,
        "title": w.get("title") or w.get("display_name"),
        "year": w.get("publication_year"),
        "type": w.get("type"),
        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "authors": [a["author"]["display_name"] for a in w.get("authorships", [])[:50]],
        "abstract": _abstract(w.get("abstract_inverted_index")),
        "referenced_works": [short_id(x) for x in w.get("referenced_works", [])],
        "oa_url": (w.get("open_access") or {}).get("oa_url"),
        "ids": {k: v for k, v in (w.get("ids") or {}).items() if isinstance(v, str)},
    }
