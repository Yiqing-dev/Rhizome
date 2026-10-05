# SPDX-License-Identifier: Apache-2.0
"""External checks: only an explicit 'does not exist' drops an ID; lookups never happen during
materialisation or rebuild."""

import httpx
import pytest

from conftest import example
from rhizome.external import verify


class Resp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self._p, self.headers = status, payload or {}, headers or {}

    def json(self):
        return self._p


@pytest.fixture()
def online(settings, monkeypatch):
    from rhizome.config import set_settings

    set_settings(settings.model_copy(update={"offline": False}))
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)


def test_ncbi_error_payload_is_not_a_hallucination(online, monkeypatch):
    monkeypatch.setattr(verify, "get", lambda *a, **k: Resp(200, {"esearchresult": {"ERROR": "backend down"}}))
    assert verify.check_accession("GSE123456", "GEO") == "unverified"
    monkeypatch.setattr(verify, "get", lambda *a, **k: Resp(200, {"esearchresult": {"count": "0", "idlist": []}}))
    assert verify.check_accession("GSE123456", "GEO") == "not_found"
    assert verify.check_accession("ERR123456", "ENA") == "unverified"  # ENA ids may not be at NCBI yet


def test_databases_we_cannot_query_are_unverified(online, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not query NCBI for GSA / CNGB ids")

    monkeypatch.setattr(verify, "get", boom)
    assert verify.check_accession("PRJCA012345", None) == "unverified"
    assert verify.check_accession("CRA001234", "SRA") == "unverified"
    assert verify.check_accession("CNP0001234", "CNGB") == "unverified"


def test_rate_limits_are_retried_then_unverified(online, monkeypatch):
    calls = []

    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            calls.append(url)
            return Resp(429, headers={"Retry-After": "2"})

    monkeypatch.setattr(verify, "client", lambda: C())
    assert verify.check_accession("GSE123456", "GEO") == "unverified"
    assert len(calls) == 2
    from rhizome.external.http import network_status

    assert network_status()["eutils.ncbi.nlm.nih.gov"]["failed"] >= 1


def test_network_errors_are_unverified(online, monkeypatch):
    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            raise httpx.ConnectError("no route")

    monkeypatch.setattr(verify, "client", lambda: C())
    assert verify.check_accession("GSE1", "GEO") == "unverified"
    assert verify.check_repo("github.com/a/b") == "unverified"
    assert verify.taxonomy_lookup("Nicotiana benthamiana") == (None, "unverified")


def test_taxa_resolved_at_ingest_and_never_during_rebuild(settings, session, monkeypatch):
    import rhizome.pipeline.ingest as ing
    from rhizome.db.models import Extraction
    from rhizome.pipeline.graph import Graph
    from rhizome.pipeline.ingest import ingest_text
    from rhizome.pipeline.rebuild import rebuild

    monkeypatch.setattr(ing, "taxonomy_lookup",
                        lambda n: ("4100", "verified") if n == "Nicotiana benthamiana" else verify.taxonomy_lookup(n))
    text = example("light-spatial-domains.yaml").replace("organisms: [", "organisms: [Nicotiana benthamiana, ", 1)
    assert "Nicotiana" in text
    r = ingest_text(session, text, "x.yaml")
    assert r.ok
    ex = session.query(Extraction).filter(Extraction.kind == "rxf").one()
    assert ex.meta["taxa"]["Nicotiana benthamiana"] == "4100"
    session.commit()

    def online_lookup(*a, **k):
        raise AssertionError("rebuild must not look anything up online")

    monkeypatch.setattr(verify, "taxonomy_lookup", online_lookup)
    monkeypatch.setattr(verify, "get", online_lookup)
    rebuild(session, backup=False)
    session.flush()
    assert Graph(session).by_key("organism:taxon:4100") is not None


def test_links_on_other_hosts_are_kept(settings, session):
    from rhizome.pipeline.graph import Graph
    from rhizome.pipeline.ingest import ingest_text

    text = example("deep-grn-atlas.yaml").replace("{name: ArchR, role: uses",
                                                   "{name: ArchR, repo: https://huggingface.co/lab/archr, role: uses")
    r = ingest_text(session, text, "x.yaml")
    assert r.ok and "https://huggingface.co/lab/archr" not in r.suspect
    assert Graph(session).by_alias("method", "ArchR").attrs.get("repo") == "https://huggingface.co/lab/archr"


def test_failed_openalex_lookup_is_backfilled_later(settings, session, monkeypatch):
    from rhizome.config import set_settings
    from rhizome.db.models import Extraction, Work
    from rhizome.external import openalex
    from rhizome.pipeline.graph import Graph
    from rhizome.pipeline.ingest import enrich_missing, ingest_text

    r = ingest_text(session, example("deep-grn-atlas.yaml"), "a.yaml")  # offline at ingest
    ex = session.query(Extraction).filter(Extraction.kind == "rxf").one()
    assert ex.meta["openalex"] == "offline"
    session.commit()
    set_settings(settings.model_copy(update={"offline": False}))
    rec = {"id": "W123", "doi": "10.5555/rhz.example.0001", "title": "A single-nucleus multiome atlas",
           "year": 2025, "type": "article", "venue": "Synthetic Journal", "authors": ["A. Author"],
           "abstract": "We profiled root nuclei.", "referenced_works": [], "oa_url": None, "ids": {}}
    monkeypatch.setattr(openalex, "fetch", lambda doi=None, openalex_id=None: (rec, "ok"))
    out = enrich_missing(session)
    assert out["ok"] == 1 and out["missing_before"] == 1
    session.flush()
    assert session.query(Extraction).filter(Extraction.kind == "openalex").count() == 1
    w = Graph(session).by_id(r.work_id)
    assert w.attrs.get("abstract") == "We profiled root nuclei." and session.get(Work, w.id).openalex_id == "W123"
    assert enrich_missing(session)["missing_before"] == 0  # nothing left to do


def test_doi_with_special_characters_is_encoded(online, monkeypatch):
    from rhizome.external import openalex

    seen = []
    monkeypatch.setattr(verify, "get", lambda url, params=None, retries=1: seen.append(url) or Resp(404))
    assert openalex.fetch(doi="https://doi.org/10.1002/abc#sec2?x=1.")[1] == "not_found"
    assert seen and "#" not in seen[0] and "?" not in seen[0] and seen[0].endswith("abc%23sec2%3Fx%3D1")
    assert openalex.fetch(doi="not a doi")[1] == "invalid"
