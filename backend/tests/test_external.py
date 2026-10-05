# SPDX-License-Identifier: Apache-2.0
"""External lookups over a fake network: NCBI, BioStudies, forges, OpenAlex; etiquette and rate
limits; an ingest that survives a 5xx."""

import httpx
import pytest

from conftest import example


@pytest.fixture()
def online(settings, monkeypatch):
    """Offline off, every request answered by `routes` (a dict of URL substrings -> handler)."""
    from rhizome.config import set_settings
    from rhizome.external import http, verify

    set_settings(settings.model_copy(update={"offline": False, "contact_email": "me@example.org"}))
    routes: dict[str, object] = {}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for frag, h in routes.items():
            if frag in str(request.url):
                return h(request) if callable(h) else httpx.Response(h[0], json=h[1]) if isinstance(h, tuple) else h
        return httpx.Response(599)

    fake = lambda timeout=15.0: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    monkeypatch.setattr(http, "client", fake)
    monkeypatch.setattr(verify, "client", fake)  # imported by name into verify
    monkeypatch.setattr(verify, "_github_reset_at", 0.0)
    verify._ncbi_cache.clear()
    return routes, seen


def test_ncbi_answers_and_etiquette(online, monkeypatch):
    from rhizome.external import verify

    routes, seen = online
    routes["esearch.fcgi"] = (200, {"esearchresult": {"count": "1", "idlist": ["1"]}})
    assert verify.check_accession("GSE1", "GEO") == "verified"
    req = seen[-1]
    assert req.url.params["tool"] == "rhizome" and req.url.params["email"] == "me@example.org"
    assert verify.check_accession("GSE1", "GEO") == "verified" and len(seen) == 1  # cached per run
    routes["esearch.fcgi"] = (200, {"esearchresult": {"count": "0", "idlist": []}})
    assert verify.check_accession("GSE2", "GEO") == "not_found"
    assert verify.check_accession("ERR2", "ENA") == "unverified"  # ENA ids reach NCBI late
    routes["esearch.fcgi"] = (200, {"esearchresult": {"ERROR": "backend down"}})
    assert verify.check_accession("GSE3", "GEO") == "unverified"
    routes["esearch.fcgi"] = (503, {})
    assert verify.check_accession("GSE4", "GEO") == "unverified"
    times = []
    monkeypatch.setattr(verify.time, "sleep", lambda t: times.append(t))
    monkeypatch.setattr(verify, "_ncbi_last", verify.time.monotonic())
    routes["esearch.fcgi"] = (200, {"esearchresult": {"count": "1", "idlist": ["9"]}})
    verify.check_accession("GSE5", "GEO")
    assert times and 0 < times[0] <= verify.NCBI_SPACING  # 3 requests per second at most


def test_biostudies_zenodo_and_forges(online):
    from rhizome.external import verify

    routes, seen = online
    routes["biostudies"] = httpx.Response(404)
    assert verify.check_accession("E-MTAB-1", "ArrayExpress") == "not_found"
    routes["zenodo.org"] = httpx.Response(200, json={})
    assert verify.check_accession("10.5281/zenodo.42", "Zenodo") == "verified"
    routes["api.github.com"] = httpx.Response(200, json={})
    assert verify.check_repo("github.com/a/b") == "verified"
    routes["api.github.com"] = httpx.Response(404)
    assert verify.check_repo("github.com/a/c") == "not_found"
    routes["api.github.com"] = httpx.Response(403, headers={"X-RateLimit-Reset": str(int(verify.time.time()) + 3600)})
    n = len(seen)
    assert verify.check_repo("github.com/a/d") == "unverified"
    assert verify.check_repo("github.com/a/e") == "unverified" and len(seen) == n + 1  # quota spent: no call
    routes["gitlab.com"] = httpx.Response(500)
    assert verify.check_repo("gitlab.com/a/b") == "unverified"


def test_taxonomy_and_openalex(online):
    from rhizome.external import openalex, verify

    routes, _ = online
    routes["esearch.fcgi"] = (200, {"esearchresult": {"count": "1", "idlist": ["3702"]}})
    assert verify.taxonomy_lookup("Arabidopsis thaliana") == ("3702", "verified")  # built-in table, no call needed
    assert verify.taxonomy_lookup("Rhizomia fictus") == ("3702", "verified")
    routes["esearch.fcgi"] = (200, {"esearchresult": {"count": "2", "idlist": ["1", "2"]}})
    assert verify.taxonomy_lookup("Mus")[1] == "unverified"
    routes["api.openalex.org"] = httpx.Response(404)
    assert openalex.fetch(doi="10.1000/x") == (None, "not_found")
    routes["api.openalex.org"] = httpx.Response(503)
    assert openalex.fetch(doi="10.1000/x") == (None, "transient")
    routes["api.openalex.org"] = httpx.Response(200, json={"id": "https://openalex.org/W7", "title": "T", "publication_year": 2020,
                                                           "abstract_inverted_index": {"b": [1], "a": [0]}, "authorships": []})
    rec, status = openalex.fetch(doi="10.1000/x")
    assert status == "ok" and rec["id"] == "W7" and rec["abstract"] == "a b"
    assert openalex.fetch(doi="not a doi") == (None, "invalid")


def test_ingest_survives_an_openalex_outage(online, session):
    from rhizome.pipeline.ingest import ingest_text

    routes, _ = online
    routes["api.openalex.org"] = httpx.Response(502)
    routes["esearch.fcgi"] = (200, {"esearchresult": {"count": "1", "idlist": ["1"]}})
    routes["api.github.com"] = httpx.Response(200, json={})
    r = ingest_text(session, example("deep-grn-atlas.yaml"), "a.yaml")
    assert r.ok, r.report
    from sqlalchemy import select

    from rhizome.db.models import Extraction

    ex = session.execute(select(Extraction).where(Extraction.kind == "rxf")).scalars().first()
    assert ex.meta["openalex"] == "transient"  # the enrich job retries it later
