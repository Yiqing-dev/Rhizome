# SPDX-License-Identifier: Apache-2.0
"""T0: OpenAlex metadata, abstract and references."""

from __future__ import annotations

import logging

import httpx

from ..config import get_settings

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
    return fetch(doi, openalex_id)[0]


def fetch(doi: str | None = None, openalex_id: str | None = None) -> tuple[dict | None, str]:
    """(record, status) with status ok | not_found | transient | offline | invalid. A transient
    failure (rate limit, 5xx, network) is retried once here and later by the `enrich` job, never
    treated as 'this paper has no metadata'."""
    from urllib.parse import quote

    from .ids import doi_ok, normalize_doi
    from .verify import get

    s = get_settings()
    if s.offline:
        return None, "offline"
    if openalex_id:
        ref = openalex_id
    elif doi and doi_ok(doi):
        ref = "doi:" + quote(normalize_doi(doi), safe="/()")  # '#' or '?' in a DOI must not cut the URL
    else:
        return None, "invalid"
    params = {"mailto": s.contact_email} if s.contact_email else {}
    try:
        r = get(f"{API}/works/{ref}", params)
        if r.status_code == 404:
            return None, "not_found"
        if r.status_code != 200:
            log.warning("OpenAlex lookup for %s: HTTP %s", ref, r.status_code)
            return None, "transient"
        w = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("OpenAlex lookup failed for %s: %s", ref, e)
        return None, "transient"
    return _trim(w), "ok"


def _trim(w: dict) -> dict:
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
