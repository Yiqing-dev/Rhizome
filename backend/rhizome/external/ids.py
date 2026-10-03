# SPDX-License-Identifier: Apache-2.0
"""External-ID format checks (regex). Existence checks live in ``verify``.

An ID that fails either check is NOT stored and the extraction is flagged as a suspected
hallucination: fabricated accessions are the main risk at this layer.
"""

from __future__ import annotations

import re

ACCESSION_PATTERNS: dict[str, re.Pattern[str]] = {
    "GEO": re.compile(r"^(GSE|GSM|GDS|GPL)\d+$"),
    "SRA": re.compile(r"^(SRP|SRR|SRX|SRS|SRA|ERP|ERR|ERX|ERS|DRP|DRR|DRX|DRS|PRJNA|PRJEB|PRJDB)\d+$"),
    "ENA": re.compile(r"^(PRJEB|ERP|ERR|ERX|ERS|SAMEA)\d+$"),
    "CNGB": re.compile(r"^(CNP|CNX|CNR|CNS|CNA)\d+$"),
    "ArrayExpress": re.compile(r"^E-[A-Z]{4}-\d+$"),
    "Zenodo": re.compile(r"^(10\.5281/zenodo\.)?\d+$"),
}

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
REPO_RE = re.compile(r"^(?:https?://)?(?:www\.)?(github\.com|gitlab\.com|bitbucket\.org)/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$",
                     re.I)
BIOTOOLS_RE = re.compile(r"^[a-z0-9_\-]{2,64}$", re.I)


def guess_database(accession: str) -> str | None:
    for db, rx in ACCESSION_PATTERNS.items():
        if db != "Zenodo" and rx.match(accession):
            return db
    return None


def accession_format_ok(accession: str, database: str | None) -> bool:
    accession = accession.strip()
    if database in (None, "other"):
        return guess_database(accession) is not None or database == "other"
    rx = ACCESSION_PATTERNS.get(database)
    if rx is None:
        return True
    if rx.match(accession):
        return True
    # SRA-family ids are often labelled GEO/ENA interchangeably by models; accept if any SRA form matches
    return database in ("SRA", "ENA") and bool(ACCESSION_PATTERNS["SRA"].match(accession))


def normalize_doi(doi: str) -> str:
    return doi.strip().lower()


def doi_ok(doi: str) -> bool:
    return bool(DOI_RE.match(doi.strip()))


def normalize_repo(url: str) -> str | None:
    m = REPO_RE.match(url.strip())
    if not m:
        return None
    host, owner, name = m.groups()
    return f"{host.lower()}/{owner.lower()}/{name.lower()}"
