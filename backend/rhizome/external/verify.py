# SPDX-License-Identifier: Apache-2.0
"""Existence checks against NCBI E-utilities, BioStudies (ArrayExpress) and the GitHub API.

Returns one of: ``verified`` | ``not_found`` | ``unverified`` (offline, unsupported database or
network error). Only ``not_found`` (and bad formats) block an ID from entering the graph.
"""

from __future__ import annotations

import logging

import httpx

from ..config import get_settings
from .http import client

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"


def _esearch(db: str, term: str) -> str:
    with client() as c:
        r = c.get(EUTILS, params={"db": db, "term": term, "retmode": "json"})
        r.raise_for_status()
        count = int(r.json()["esearchresult"].get("count", 0))
    return "verified" if count > 0 else "not_found"


def check_accession(accession: str, database: str | None) -> str:
    if get_settings().offline:
        return "unverified"
    try:
        if database == "GEO" or accession.startswith(("GSE", "GDS")):
            return _esearch("gds", f"{accession}[ACCN]")
        if database in ("SRA", "ENA") or accession[:3] in ("SRP", "SRR", "SRX", "ERP", "ERR", "DRP", "PRJ"):
            if accession.startswith("PRJ"):
                return _esearch("bioproject", f"{accession}[PRJA]")
            return _esearch("sra", f"{accession}[All Fields]")
        if database == "ArrayExpress" or accession.startswith("E-"):
            with client() as c:
                r = c.get(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{accession}")
            return "verified" if r.status_code == 200 else ("not_found" if r.status_code == 404 else "unverified")
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("accession check failed for %s: %s", accession, e)
    return "unverified"


def check_repo(normalized: str) -> str:
    """normalized: host/owner/name as produced by ids.normalize_repo."""
    if get_settings().offline or not normalized.startswith("github.com/"):
        return "unverified"
    _, owner, name = normalized.split("/", 2)
    try:
        with client() as c:
            r = c.get(f"https://api.github.com/repos/{owner}/{name}")
        if r.status_code == 200:
            return "verified"
        if r.status_code == 404:
            return "not_found"
    except httpx.HTTPError as e:
        log.warning("repo check failed for %s: %s", normalized, e)
    return "unverified"


def taxonomy_id(name: str) -> str | None:
    from .organisms import lookup_builtin

    tid = lookup_builtin(name)
    if tid or get_settings().offline:
        return tid
    try:
        with client() as c:
            r = c.get(EUTILS, params={"db": "taxonomy", "term": f"{name}[Scientific Name]", "retmode": "json"})
            ids = r.json()["esearchresult"].get("idlist", [])
        return ids[0] if len(ids) == 1 else None
    except (httpx.HTTPError, KeyError, ValueError):
        return None
