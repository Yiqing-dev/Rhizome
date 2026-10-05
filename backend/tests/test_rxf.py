# SPDX-License-Identifier: Apache-2.0
"""RXF contract tests: the spec examples, JSON Schema and instructions never drift."""

import json

import pytest

from conftest import EXAMPLES, ROOT
from rhizome.rxf import json_schema, load_rxf

VALID = sorted(p.name for p in EXAMPLES.glob("*.yaml"))
INVALID = sorted(p.name for p in (EXAMPLES / "invalid").glob("*.yaml"))


@pytest.mark.parametrize("name", VALID)
def test_examples_valid(name):
    r = load_rxf((EXAMPLES / name).read_text("utf-8"))
    assert r.ok, r.problems


@pytest.mark.parametrize("name", INVALID)
def test_invalid_examples_rejected(name):
    r = load_rxf((EXAMPLES / "invalid" / name).read_text("utf-8"))
    assert not r.ok and r.problems


def test_published_schema_matches_models():
    generated = json_schema(1)
    spec = json.loads((ROOT / "rxf-spec" / "schema" / "rxf-v1.schema.json").read_text("utf-8"))
    assert spec == generated, "run: rhz rxf schema -o rxf-spec/schema/rxf-v1.schema.json"


def test_validator_uses_the_models_not_a_copy():
    from rhizome.rxf.loader import schema_for

    assert schema_for(1) == json_schema(1)
    assert not (ROOT / "backend" / "rhizome" / "rxf" / "schemas").exists()


@pytest.mark.parametrize("name", ["export-instructions.zh.md", "export-instructions.en.md"])
def test_instructions_packaged(name):
    assert (ROOT / "rxf-spec" / "instructions" / name).read_text("utf-8") == \
        (ROOT / "backend" / "rhizome" / "data" / name).read_text("utf-8")


def test_code_fence_and_bom_are_tolerated():
    text = "﻿```yaml\n" + (EXAMPLES / "light-spatial-domains.yaml").read_text("utf-8") + "\n```\n"
    assert load_rxf(text).ok


def test_report_lists_paths():
    from rhizome.rxf import error_report

    r = load_rxf((EXAMPLES / "invalid" / "missing-evidence.yaml").read_text("utf-8"))
    rep = error_report("x.yaml", r.problems)
    assert "claims.0" in rep and "topics.0.relation" in rep and "summary" in rep


def test_yaml_flow_mapping_question_mark_is_reported_not_crashing():
    r = load_rxf("rxf_version: 1\npaper: {title: t}\nreview_cards:\n  - {q: What?, a: b}\n")
    assert not r.ok and "line" in r.problems[0].path


def test_doi_prefix_stripped():
    r = load_rxf("rxf_version: 1\npaper: {title: t, doi: 'https://doi.org/10.1/ABC'}\n")
    assert r.doc.paper.doi == "10.1/ABC"


def test_dataset_needs_identity():
    r = load_rxf("rxf_version: 1\npaper: {title: t}\nassets:\n  datasets:\n    - {organism: human}\n")
    assert not r.ok


@pytest.mark.parametrize("name", ["export-instructions.zh.md", "export-instructions.en.md"])
def test_instruction_skeleton_is_a_valid_export(name):
    """The skeleton in the export instructions is the positive contract case: every field and enum
    value it shows must be accepted by the schema (file-local ids included)."""
    from rhizome.rxf.loader import load_rxf

    text = (ROOT / "backend" / "rhizome" / "data" / name).read_text("utf-8")
    block = text.split("```yaml\n", 1)[1].split("```", 1)[0]
    res = load_rxf(block)
    assert res.ok, res.report
    doc = res.doc
    assert doc.depth == "deep" and doc.assets.datasets[0].public is True and doc.assets.methods[0].extends
    assert {t.id for t in doc.topics} == {"t1", "t2"} and doc.assets.ideas[0].transfer.to == "t2"
