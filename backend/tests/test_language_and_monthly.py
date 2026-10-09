# SPDX-License-Identifier: Apache-2.0
"""The export's `language` reaches the alias table; the monthly topic summary has an MCP tool."""

import json

from sqlalchemy import select

from conftest import example
from rhizome.db.models import Entity, EntityAlias
from rhizome.pipeline.graph import Graph
from rhizome.text import alias_default


def test_alias_default_from_language():
    assert alias_default(None) is None and alias_default("") is None
    assert alias_default("en") == "en" and alias_default("zh-CN") == "en" and alias_default("zh_TW") == "en"
    assert alias_default("ja") == "ja" and alias_default("de-DE") == "de" and alias_default("x-klingon") is None


def test_export_language_lands_on_aliases(settings, session):
    from rhizome.pipeline.ingest import ingest_text

    zh = example("deep-grn-atlas.yaml")
    ja = zh.replace("rxf_version: 1", "rxf_version: 1\nlanguage: ja", 1).replace(
        "10.5555/rhz.example.0001", "10.5555/rhz.example.0901").replace(
        "{name: GRN inference, relation: about}",
        "{name: GRN inference, relation: about, aliases: [遺伝子制御ネットワーク推定, gene regulatory network inference]}")
    assert ingest_text(session, ja, "ja.yaml").ok
    g = Graph(session)
    topic = g.by_alias("topic", "GRN inference")
    langs = {a.alias: a.lang for a in session.execute(select(EntityAlias).where(EntityAlias.entity_id == topic.id)).scalars()}
    assert langs["遺伝子制御ネットワーク推定"] == "zh"  # Han script decides, whatever the document says
    assert langs["gene regulatory network inference"] == "ja"  # a Latin-script alias from a Japanese export
    work = session.execute(select(Entity).where(Entity.key == "work:doi:10.5555/rhz.example.0901")).scalar_one()
    assert work.attrs.get("language") == "ja"
    # a Chinese export keeps Latin names English, and Han names zh
    cn = example("light-spatial-domains.yaml")
    cn2 = cn.replace("rxf_version: 1", "rxf_version: 1\nlanguage: zh-CN", 1).replace(
        "10.5555/rhz.example.0003", "10.5555/rhz.example.0903")
    assert ingest_text(session, cn2, "cn.yaml").ok
    rows = session.execute(select(EntityAlias).join(Entity, Entity.id == EntityAlias.entity_id)
                           .where(Entity.key == "work:doi:10.5555/rhz.example.0903")).scalars().all()
    assert rows and all(a.lang == ("zh" if any("\u4e00" <= ch <= "\u9fff" for ch in a.alias) else "en") for a in rows)
    assert g.alias_lang is None  # reset after materialising


def test_monthly_topic_changes_tool(settings, library, session):
    import rhizome.mcp_server as m
    from rhizome.client import LocalClient

    session.commit()
    m._client = LocalClient(settings)
    try:
        out = json.loads(m.rhz_topic_changes("GRN inference"))
        assert out["topic"]["name"] == "GRN inference" and out["works_total"] >= 1
        assert out["new_works"] and out["new_works"][0]["tldr"] and "since" in out
        assert any(a["edge"] for a in out["new_assets"])
        by_key = json.loads(m.rhz_topic_changes(out["topic"]["key"], days=365))
        assert by_key["topic_id"] == out["topic_id"]
        assert json.loads(m.rhz_topic_changes(str(out["topic_id"])))["topic_id"] == out["topic_id"]
        none = json.loads(m.rhz_topic_changes("no such topic at all"))
        assert none["ok"] is False
        assert "rhz_topic_changes" in {t.name for t in m.mcp._tool_manager.list_tools()}
    finally:
        m._client = None
