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


def test_chinese_national_and_mislabelled_accessions():
    from rhizome.external.ids import effective_database

    for acc in ("PRJCA012345", "CRA001234", "HRA000123", "OMIX001234", "SAMC123456"):
        assert accession_format_ok(acc, None) and guess_database(acc) == "GSA"
    assert accession_format_ok("CRA001234", "GEO")  # wrong label, real format
    assert effective_database("GSE123456", "SRA") == "GEO"
    assert effective_database("PRJCA012345", "SRA") == "GSA"  # not routed to NCBI bioproject


def test_doi_normalisation():
    from rhizome.external.ids import normalize_doi

    for raw in ("https://doi.org/10.1101/ABC.1", "DOI: 10.1101/abc.1", "http://dx.doi.org/10.1101/abc.1",
                "10.1101/abc.1.", "doi:10.1101/abc.1;"):
        assert normalize_doi(raw) == "10.1101/abc.1", raw
    assert normalize_doi("10.1002/(sici)1097-0258(19980815)17:15") == "10.1002/(sici)1097-0258(19980815)17:15"
    assert normalize_doi("(see 10.1/x)".split(" ", 1)[1]) == "10.1/x"


def test_repo_urls_in_the_wild():
    assert normalize_repo("https://github.com/Owner/Repo/tree/main/src") == "github.com/owner/repo"
    assert normalize_repo("git@github.com:Owner/Repo.git") == "github.com/owner/repo"
    assert normalize_repo("https://gitee.com/lab/tool") == "gitee.com/lab/tool"
    assert normalize_repo("https://huggingface.co/org/model") is None
