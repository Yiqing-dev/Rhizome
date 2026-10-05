# SPDX-License-Identifier: Apache-2.0
"""Existence checks against NCBI E-utilities, BioStudies (ArrayExpress), Zenodo and forge APIs.

Returns one of: ``verified`` | ``not_found`` | ``unverified``. Only an explicit "does not exist"
answer is ``not_found`` (which keeps a fabricated ID out of the graph); offline, a database that
cannot be queried (GSA, CNGB), an error payload, a rate limit or a network problem is
``unverified`` and never drops anything.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..config import get_settings
from .http import client, record_failure, record_success
from .ids import effective_database, normalize_zenodo

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
RETRY_STATUSES = (429, 500, 502, 503, 504)


def get(url: str, params: dict[str, Any] | None = None, retries: int = 1) -> httpx.Response:
    """GET with one retry on rate limits / server errors / network errors (honours Retry-After up
    to 5 s). Failures are counted per host for the network status in Settings."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with client() as c:
                r = c.get(url, params=params)
            if r.status_code in RETRY_STATUSES and attempt < retries:
                wait = r.headers.get("Retry-After", "1")
                time.sleep(min(5.0, float(wait) if wait.replace(".", "", 1).isdigit() else 1.0))
                continue
            if r.status_code in RETRY_STATUSES:
                record_failure(url, f"HTTP {r.status_code}")
            else:
                record_success(url)
            return r
        except httpx.HTTPError as e:
            last = e
            if attempt < retries:
                time.sleep(1.0)
    record_failure(url, str(last))
    raise last  # type: ignore[misc]


def _esearch(db: str, term: str) -> str:
    st = get_settings()
    params = {"db": db, "term": term, "retmode": "json", "tool": "rhizome"}
    if st.contact_email:
        params["email"] = st.contact_email
    r = get(EUTILS, params)
    if r.status_code != 200:
        return "unverified"
    res = r.json().get("esearchresult") or {}
    # an NCBI backend error comes back as HTTP 200 with ERROR and no count: not an answer
    if "ERROR" in res or "count" not in res:
        return "unverified"
    return "verified" if int(res["count"]) > 0 else "not_found"


def check_accession(accession: str, database: str | None) -> str:
    if get_settings().offline:
        return "unverified"
    acc = accession.strip()
    db = effective_database(acc, database)
    try:
        if db == "GEO":
            return _esearch("gds", f"{acc}[ACCN]")
        if db in ("SRA", "ENA"):
            if acc.startswith(("PRJNA", "PRJEB", "PRJDB")):
                return _esearch("bioproject", f"{acc}[PRJA]")
            if acc.startswith("SAMEA"):
                return "unverified"  # BioSample ids: not in the SRA index
            out = _esearch("sra", f"{acc}[All Fields]")
            # ENA / DDBJ records reach NCBI with a delay: "not there yet" is not "made up"
            return "unverified" if out == "not_found" and acc[0] in "ED" else out
        if db == "ArrayExpress":
            r = get(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{acc}")
            return "verified" if r.status_code == 200 else ("not_found" if r.status_code == 404 else "unverified")
        if db == "Zenodo":
            r = get(f"https://zenodo.org/api/records/{normalize_zenodo(acc)}")
            return "verified" if r.status_code == 200 else ("not_found" if r.status_code in (404, 410) else "unverified")
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("accession check failed for %s: %s", acc, e)
    return "unverified"  # GSA / OMIX, CNGB and others have no lookup we can use


def check_repo(normalized: str) -> str:
    """normalized: host/owner/name as produced by ids.normalize_repo."""
    if get_settings().offline:
        return "unverified"
    host, owner, name = normalized.split("/", 2)
    api = {"github.com": f"https://api.github.com/repos/{owner}/{name}",
           "gitlab.com": f"https://gitlab.com/api/v4/projects/{owner}%2F{name}",
           "gitee.com": f"https://gitee.com/api/v5/repos/{owner}/{name}"}.get(host)
    if api is None:
        return "unverified"
    try:
        r = get(api)
        if r.status_code == 200:
            return "verified"
        if r.status_code == 404:
            return "not_found"
    except httpx.HTTPError as e:
        log.warning("repo check failed for %s: %s", normalized, e)
    return "unverified"  # 403 = GitHub's unauthenticated rate limit, not an answer


def taxonomy_lookup(name: str) -> tuple[str | None, str]:
    """(taxid, status): built-in table first (works offline), then NCBI Taxonomy."""
    from .organisms import lookup_builtin

    tid = lookup_builtin(name)
    if tid:
        return tid, "verified"
    if get_settings().offline:
        return None, "unverified"
    try:
        st = get_settings()
        params = {"db": "taxonomy", "term": f"{name}[Scientific Name]", "retmode": "json", "tool": "rhizome"}
        if st.contact_email:
            params["email"] = st.contact_email
        r = get(EUTILS, params)
        res = (r.json().get("esearchresult") or {}) if r.status_code == 200 else {}
        if "ERROR" in res or "count" not in res:
            return None, "unverified"
        ids = res.get("idlist", [])
        if len(ids) == 1:
            return ids[0], "verified"
        return None, ("not_found" if int(res["count"]) == 0 else "unverified")  # 0 or ambiguous
    except (httpx.HTTPError, KeyError, ValueError):
        return None, "unverified"


def taxonomy_id(name: str) -> str | None:
    return taxonomy_lookup(name)[0]
