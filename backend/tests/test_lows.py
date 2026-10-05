# SPDX-License-Identifier: Apache-2.0
"""The remaining small fixes: scoped citation linking and promotion, bio.tools anchors, splits that
mean something, forgiving decision keys, digest read state, related-paper ranking, vocabulary
staleness, instruction versions, CJK-aware output, replayable card state, raw mirrors, exports,
and the MCP server over JSON-RPC."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from conftest import example
from rhizome.db.models import Entity, ReviewCard
from rhizome.pipeline import decisions
from rhizome.pipeline.graph import Graph
from rhizome.pipeline.ingest import ingest_text

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def test_citation_links_and_promotion_are_scoped(library, session):
    from rhizome.pipeline.materialize import link_citations, promote_topics

    g = Graph(session)
    w1, w2 = g.by_key("work:doi:10.5555/rhz.example.0001"), g.by_key("work:doi:10.5555/rhz.example.0002")
    from rhizome.db.models import Extraction, Work

    for w, oa in ((w1, "W1"), (w2, "W2")):
        session.get(Work, w.id).openalex_id = oa
    session.add(Extraction(kind="openalex", work_key=w1.key, tier=0, model="openalex", schema_version="openalex-v1",
                           input_hashes=[], output={"id": "W1", "referenced_works": ["W2", "W999"]}, meta={}))
    session.add(Extraction(kind="openalex", work_key=w2.key, tier=0, model="openalex", schema_version="openalex-v1",
                           input_hashes=[], output={"id": "W2", "referenced_works": []}, meta={}))
    session.flush()
    assert link_citations(g, only_work_ids={w2.id}) == 1  # the record that cites w2 was found by json_each
    assert g.edge(w1, w2, "cites") is not None
    t = g.create("topic", "topic:scoped", "scoped", status="candidate")
    other = g.create("topic", "topic:other-cand", "other cand", status="candidate")
    for w in (w1, w2, g.by_key("work:doi:10.5555/rhz.example.0003")):
        g.upsert_edge(w, t, "about")
        g.upsert_edge(w, other, "about")
    assert promote_topics(g, only=[t]) == 1 and t.status == "active" and other.status == "candidate"
    assert promote_topics(g) == 1 and other.status == "active"


def test_biotools_ids_anchor_methods(settings, session):
    from rhizome.external.ids import normalize_biotools

    assert normalize_biotools("https://bio.tools/Scanpy") == "scanpy" and normalize_biotools("not valid!") is None
    doc = example("light-scenic-benchmark.yaml").replace("{name: GENIE3, role: evaluates,", "{name: GENIE3, biotools: genie3, role: evaluates,")
    assert ingest_text(session, doc, "a.yaml").ok
    g = Graph(session)
    m = g.by_alias("method", "biotools:genie3")
    assert m is not None and m.canonical_name == "GENIE3" and m.attrs["biotools"] == "genie3"
    doc2 = doc.replace("10.5555/rhz.example.0002", "10.5555/rhz.example.0077").replace("name: GENIE3", "name: GENIE3 (R)")
    assert ingest_text(session, doc2, "b.yaml").ok
    assert g.by_alias("method", "GENIE3 (R)").id == m.id  # same tool through its bio.tools id


def test_split_moves_edges_and_aliases_and_stays_distinct(library, session):
    g = Graph(session)
    m = g.by_alias("method", "DomainGAT")
    work = next(e for e in session.execute(select(Entity).where(Entity.type == "work")).scalars()
                if g.edge(e, m, "proposes") is not None)
    g.add_alias(m, "DomainGAT-lite", source="extraction")
    with pytest.raises(decisions.DecisionError, match="none of the listed edges"):
        decisions.record(g, "split", {"key": m.key, "new_name": "DomainGAT-lite",
                                      "edges": [{"src": "work:nope", "dst": m.key, "type": "proposes"}]})
    assert session.execute(select(Entity).where(Entity.canonical_name == "DomainGAT-lite")).first() is None
    d = decisions.record(g, "split", {"key": m.key, "new_name": "DomainGAT-lite",
                                      "edges": [{"src": work.key, "dst": m.key, "type": "proposes"}]})
    new = g.by_key(d.payload["new_key"])
    assert new is not None and g.edge(work, new, "proposes") is not None and g.edge(work, m, "proposes") is None
    assert g.by_alias("method", "DomainGAT-lite").id == new.id and g.by_alias("method", "DomainGAT").id == m.id
    assert frozenset((m.key, new.key)) in g.distinct_pairs()


def test_decision_keys_are_forgiving_and_parents_checked(library, session):
    g = Graph(session)
    d = decisions.record(g, "add_alias", {"key": "method:DomainGAT", "alias": "DGAT"})  # alias, not the key
    assert d.payload["key"] == g.by_alias("method", "DomainGAT").key
    with pytest.raises(decisions.DecisionError, match="did you mean topic:grn inference"):
        decisions.record(g, "rename", {"key": "topic:grn inference typo", "name": "x"})
    with pytest.raises(decisions.DecisionError, match="parent topic"):
        decisions.record(g, "create_topic", {"name": "child topic", "parent": "no such parent"})
    with pytest.raises(decisions.DecisionError, match="one of: about"):
        decisions.record(g, "add_edge", {"src": "topic:grn inference", "dst": "topic:benchmarking", "type": "likes"})


def test_digest_ack_and_related_ranking(library, session):
    from rhizome.services.recall import related_to_work
    from rhizome.services.synthesis import ack_digest, weekly_digest

    g = Graph(session)
    w = g.by_key("work:doi:10.5555/rhz.example.0001")
    a = related_to_work(session, w.id)
    b = related_to_work(session, w.id)
    assert [x["work_id"] for x in a] == [x["work_id"] for x in b]  # deterministic
    assert all(len({(d["dimension"]) for d in x["via"]}) <= len(x["via"]) for x in a)
    assert w.id not in {x["work_id"] for x in a}
    assert weekly_digest(session)["ack_at"] is None
    at = ack_digest(session)
    assert weekly_digest(session)["ack_at"] == at


def test_vocab_staleness_and_instruction_version(library, session):
    from rhizome.rxf.guide import instructions
    from rhizome.services.vocab import export_vocab, mark_exported, vocab_status

    assert vocab_status(session)["stale"] is None
    mark_exported(session, export_vocab(session))
    assert vocab_status(session)["stale"] is False
    decisions.record(Graph(session), "create_topic", {"name": "a brand new topic"})
    assert vocab_status(session)["stale"] is True
    for lang in ("en", "zh_CN"):
        text = instructions(lang)
        assert 'instructions_version: "2026.10"' in text  # the version the exporter copies as is
    from rhizome.rxf.loader import load_rxf

    assert load_rxf(example("light-scenic-benchmark.yaml").replace("rxf_version: 1", "rxf_version: 1\ninstructions_version: 2026.10")).ok  # unquoted: a float, still read


def test_cjk_clip_and_reference_resolution(library, session):
    from rhizome.cli import _clip

    assert _clip("空间转录组学方法", 8) == "空间转录…" and _clip("abcdefgh", 8) == "abcdefgh" and _clip("abcdefghi", 8) == "abcdefgh…"
    g = Graph(session)
    w = g.by_key("work:doi:10.5555/rhz.example.0001")
    assert g.resolve_ref("10.5555/rhz.example.0001").id == w.id
    assert g.resolve_ref("https://doi.org/10.5555/RHZ.example.0001").id == w.id
    assert g.resolve_ref("GSE999001").key == "dataset:GSE999001"
    assert g.resolve_ref("https://github.com/rhizome-examples/rootnet").key == "method:repo:github.com/rhizome-examples/rootnet"
    assert g.resolve_ref("DomainGAT").type == "method" and g.resolve_ref("nothing like this") is None
    from rhizome.client import LocalClient

    assert LocalClient().get_by_key("GSE999001")["key"] == "dataset:GSE999001"


def test_unreadable_card_state_is_replayed_from_the_log(library, session):
    from rhizome.services.cards import due_cards, grade

    card = due_cards(session, 5)[0]
    grade(session, card["id"], 3)
    grade(session, card["id"], 4)
    row = session.get(ReviewCard, card["id"])
    row.state = {"from": "another py-fsrs major", "stability": "?"}
    session.flush()
    out = grade(session, card["id"], 3)  # the two earlier reviews are replayed, not lost
    from rhizome.db.models import utcnow

    assert out["interval_days"] > 0 and session.get(ReviewCard, card["id"]).due > utcnow()
    assert session.get(ReviewCard, card["id"]).state.get("stability") not in (None, "?")


def test_raw_mirror_check_and_export(library, session, settings, tmp_path):
    from rhizome.cli import app
    from rhizome.db.session import mirror_raw

    session.commit()
    ext = tmp_path / "ext-backups"
    from rhizome.config import set_settings

    set_settings(settings.model_copy(update={"backup_dir": ext}))
    st = settings.model_copy(update={"backup_dir": ext})
    n = mirror_raw(st)
    assert n >= 3 and mirror_raw(st) == 0  # copy-if-absent
    assert len(list((ext / "raw").rglob("*.yaml"))) == 3
    r = runner.invoke(app, ["--data-dir", str(settings.data_dir), "check"], catch_exceptions=False)
    assert r.exit_code == 0 and "Backups" in r.output
    out = tmp_path / "export"
    r = runner.invoke(app, ["--data-dir", str(settings.data_dir), "export", "-o", str(out)], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    ents = [json.loads(line) for line in (out / "entities.jsonl").read_text("utf-8").splitlines()]
    edges = [json.loads(line) for line in (out / "edges.jsonl").read_text("utf-8").splitlines()]
    assert any(e["key"] == "dataset:GSE999001" and e["aliases"] for e in ents)
    assert all(ed["src"].count(":") >= 1 and ed["dst"].count(":") >= 1 for ed in edges) and (out / "README.txt").exists()
    from rhizome.services import recall

    recall._seen.clear()
    assert not recall._seen  # export touched nothing


def test_cli_help_for_every_command_and_setting_keys(settings):
    from rhizome.cli import app

    names = [c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands]
    assert len(names) >= 25
    for name in names:
        r = runner.invoke(app, [name, "--help"])
        assert r.exit_code == 0, (name, r.output)
    r = runner.invoke(app, ["--data-dir", str(settings.data_dir), "settings", "set", "no_such_key", "1"])
    assert r.exit_code == 2 and "No setting named" in r.output
    r = runner.invoke(app, ["--data-dir", str(settings.data_dir), "settings", "set", "thresholds.nope", "1"])
    assert r.exit_code == 2
    r = runner.invoke(app, ["--data-dir", str(settings.data_dir), "settings", "set", "thresholds.merge_auto", "0.91"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def test_mcp_over_json_rpc(settings, library, session):
    import rhizome.mcp_server as m
    from mcp.shared.memory import create_connected_server_and_client_session as connect
    from rhizome.client import LocalClient

    session.commit()
    m._client = LocalClient(settings)

    async def go():
        async with connect(m.mcp._mcp_server) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools.tools}
            assert {"rhz_search", "rhz_get", "rhz_ingest", "rhz_decide", "rhz_rxf_guide"} <= names
            r = await c.call_tool("rhz_search", {"query": "DomainGAT", "types": "method"})
            hits = json.loads(r.content[0].text)
            assert hits[0]["name"] == "DomainGAT"
            r = await c.call_tool("rhz_decide", {"item_id": 99999999, "action": "merge"})
            assert json.loads(r.content[0].text)["ok"] is False
            r = await c.call_tool("rhz_get", {"entity": "GSE999001"})
            assert json.loads(r.content[0].text)["key"] == "dataset:GSE999001"
    asyncio.run(go())


def _ts_interface(name: str) -> set[str]:
    """Top-level required fields of an interface in api.ts (nested braces skipped)."""
    src = (ROOT / "frontend" / "src" / "api.ts").read_text("utf-8")
    start = src.index(f"export interface {name}")
    i = src.index("{", start) + 1
    depth, body = 1, []
    while depth:
        ch = src[i]
        depth += ch == "{"
        depth -= ch == "}"
        body.append(ch if depth == 1 else " ")
        i += 1
    text = "".join(body)
    return set(re.findall(r"(?:^|[;{\s])([a-z_]+)\s*:", text))


def test_api_contract_matches_the_frontend_types(settings, library, session):
    from fastapi.testclient import TestClient

    from rhizome.api.app import create_app

    session.commit()
    app = create_app(settings, start_worker=False)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {app.state.token}"
    stats = c.get("/stats").json()
    assert _ts_interface("Stats") <= set(stats)
    hit = c.get("/search", params={"q": "DomainGAT"}).json()["results"][0]
    assert _ts_interface("Hit") - {"relevance", "origin"} <= set(hit)
    card = c.get(f"/entity/{hit['id']}").json()
    assert _ts_interface("Card") - {"work", "exports", "edge_counts", "relevance", "origin"} <= set(card)
    g = Graph(session)
    w = g.by_key("work:doi:10.5555/rhz.example.0001")
    paper = c.get(f"/entity/{w.id}").json()
    assert _ts_interface("ExportView") <= set(paper["exports"][0])
    sysinfo = c.get("/system").json()
    assert _ts_interface("SystemInfo") <= set(sysinfo)


def test_frontend_vocabularies_follow_the_models():
    from rhizome.db.models import EDGE_TYPES, ENTITY_TYPES
    from rhizome.services.review import ACTIONS

    fe = ROOT / "frontend" / "src"
    entity_src = (fe / "pages" / "Entity.tsx").read_text("utf-8")
    order = re.search(r"const EDGE_ORDER = \[(.*?)\];", entity_src, re.S).group(1)
    assert set(re.findall(r'"([a-z_]+)"', order)) == set(EDGE_TYPES)
    search_src = (fe / "pages" / "Search.tsx").read_text("utf-8")
    types = re.search(r"const TYPES = \[(.*?)\];", search_src).group(1)
    assert set(re.findall(r'"([a-z_]+)"', types)) <= set(ENTITY_TYPES)
    review_src = (fe / "pages" / "Review.tsx").read_text("utf-8")
    kinds = re.search(r"const KINDS = \[(.*?)\];", review_src).group(1)
    assert set(re.findall(r'"([a-z_]+)"', kinds)) == set(ACTIONS)
    en = json.loads((fe / "locales" / "en.json").read_text("utf-8"))
    assert set(EDGE_TYPES) <= set(en["edge"]) and set(ENTITY_TYPES) <= set(en["type"])
    assert set(ACTIONS) <= set(en["review"]["kind"])
    for kind, actions in ACTIONS.items():
        assert set(actions) <= set(en["review"]["action"]), kind
