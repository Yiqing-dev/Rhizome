# SPDX-License-Identifier: Apache-2.0
from rhizome.external.ids import accession_format_ok, doi_ok, guess_database, normalize_repo


def test_accessions():
    assert accession_format_ok("GSE123456", "GEO")
    assert not accession_format_ok("GSE12A", "GEO")
    assert accession_format_ok("SRR000001", "SRA")
    assert accession_format_ok("E-MTAB-1234", "ArrayExpress")
    assert accession_format_ok("CNP0001234", "CNGB")
    assert guess_database("PRJNA12345") == "SRA"
    assert not accession_format_ok("made-up", None)


def test_doi_and_repo():
    assert doi_ok("10.1101/2024.01.01.123456")
    assert not doi_ok("doi.org/abc")
    assert normalize_repo("https://github.com/Owner/Repo.git") == "github.com/owner/repo"
    assert normalize_repo("github.com/a/b/") == "github.com/a/b"
    assert normalize_repo("https://example.com/a/b") is None
