# SPDX-License-Identifier: Apache-2.0
"""External-ID format checks and normalisation. Existence checks live in ``verify``.

An ID that fails the format check *and* matches no known database, or that a database reports as
missing, is not stored and the extraction is flagged as a suspected hallucination: fabricated
accessions are the main risk at this layer. A real ID with the wrong database label (models mix
GEO / SRA / ENA up) is accepted and checked against the database it actually belongs to.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

ACCESSION_PATTERNS: dict[str, re.Pattern[str]] = {
    "GEO": re.compile(r"^(GSE|GSM|GDS|GPL)\d+$"),
    "SRA": re.compile(r"^(SRP|SRR|SRX|SRS|SRA|ERP|ERR|ERX|ERS|DRP|DRR|DRX|DRS|PRJNA|PRJEB|PRJDB)\d+$"),
    "ENA": re.compile(r"^(PRJEB|ERP|ERR|ERX|ERS|SAMEA)\d+$"),
    # China National Center for Bioinformation / NGDC Genome Sequence Archive (and OMIX, BioSample)
    "GSA": re.compile(r"^(PRJCA|CRA|CRR|CRX|HRA|HRR|HRX|OMIX|SAMC)\d+$"),
    "CNGB": re.compile(r"^(CNP|CNX|CNR|CNS|CNA)\d+$"),
    "ArrayExpress": re.compile(r"^E-[A-Z]{4}-\d+$"),
    "Zenodo": re.compile(r"^(10\.5281/zenodo\.)?\d+$"),
}

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
REPO_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "gitee.com", "codeberg.org")
BIOTOOLS_RE = re.compile(r"^[a-z0-9_\-]{2,64}$", re.I)


def guess_database(accession: str) -> str | None:
    for db, rx in ACCESSION_PATTERNS.items():
        if db != "Zenodo" and rx.match(accession):
            return db
    return None


def effective_database(accession: str, database: str | None) -> str | None:
    """The database an accession belongs to: the label when the id fits it, else the database
    the id's own format names (a mislabelled real id), else the label."""
    accession = accession.strip()
    rx = ACCESSION_PATTERNS.get(database or "")
    if rx is not None and rx.match(accession):
        return database
    return guess_database(accession) or database


def accession_format_ok(accession: str, database: str | None) -> bool:
    accession = accession.strip()
    if guess_database(accession) is not None:
        return True  # a known format, whatever the label says
    if database in (None, "other"):
        return database == "other"
    rx = ACCESSION_PATTERNS.get(database)
    return rx is None or bool(rx.match(accession))


def normalize_zenodo(accession: str) -> str:
    """'10.5281/zenodo.123' and '123' are the same record."""
    a = accession.strip().lower()
    return a.split("zenodo.", 1)[1] if "zenodo." in a else a


_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/",
                 "doi.org/", "dx.doi.org/", "doi:", "doi ")


def normalize_doi(doi: str) -> str:
    """Lower-case DOI without resolver prefixes, 'DOI:' labels or trailing punctuation picked up
    from a sentence ("... 10.1/abc.") or an unbalanced closing bracket."""
    d = doi.strip()
    low = d.lower()
    changed = True
    while changed:
        changed = False
        for p in _DOI_PREFIXES:
            if low.startswith(p):
                d, low, changed = d[len(p):].strip(), low[len(p):].strip(), True
    d = d.rstrip(".,;:")
    while d.endswith((")", "]")) and d.count(d[-1]) > d.count("(" if d[-1] == ")" else "["):
        d = d[:-1].rstrip(".,;:")
    return d.lower()


def doi_ok(doi: str) -> bool:
    return bool(DOI_RE.match(normalize_doi(doi)))


def normalize_repo(url: str) -> str | None:
    """host/owner/name for a code repository on a known forge (https, ssh, /tree/... links), or
    None when it is not one."""
    u = url.strip()
    m = re.match(r"^(?:ssh://)?git@([\w.-]+)[:/](.+)$", u)
    if m:
        host, path = m.group(1).lower(), m.group(2)
    else:
        if "://" not in u:
            u = "https://" + u
        p = urlparse(u)
        host, path = (p.hostname or "").lower(), p.path
    host = host.removeprefix("www.")
    parts = [x for x in path.split("/") if x]
    if host not in REPO_HOSTS or len(parts) < 2:
        return None
    owner, name = parts[0], parts[1].removesuffix(".git")
    if not re.fullmatch(r"[\w.-]+", owner) or not re.fullmatch(r"[\w.-]+", name):
        return None
    return f"{host}/{owner.lower()}/{name.lower()}"


def is_url(text: str) -> bool:
    p = urlparse(text.strip())
    return p.scheme in ("http", "https") and bool(p.hostname)
