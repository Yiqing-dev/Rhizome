# SPDX-License-Identifier: Apache-2.0
"""Views stay bounded on hubs and broad topics; Chinese keyword search matches real words."""


from rhizome.pipeline.canonicalize import resolve_free
from rhizome.pipeline.graph import Graph
from rhizome.services.search import Filters, keyword_ids, search
from rhizome.services.views import EDGES_PER_TYPE, entity_card, entity_edges, topic_assets


def _hub(session, n=130):
    g = Graph(session)
    topic = resolve_free(g, "topic", "huge topic", status="active").entity
    methods = []
    for i in range(n):
        m = g.create("method", f"method:tool{i:04d}", f"Tool number {i:04d}", embed=False)
        g.upsert_edge(m, topic, "applicable_to")
        methods.append(m)
    session.flush()
    return topic, methods


def test_card_of_a_hub_is_bounded_and_pageable(settings, session):
    topic, methods = _hub(session)
    card = entity_card(session, topic.id, touch_access=False)
    assert len(card["edges"]["applicable_to"]) == EDGES_PER_TYPE
    assert card["edge_counts"]["applicable_to"] == len(methods)
    page = entity_edges(session, topic.id, "applicable_to", offset=EDGES_PER_TYPE, limit=100)
    assert page["total"] == len(methods) and len(page["edges"]) == len(methods) - EDGES_PER_TYPE


def test_broad_topic_is_paged_per_column(settings, session):
    topic, methods = _hub(session, n=180)
    page = topic_assets(session, topic.id, limit=150)
    assert page["totals"]["method"] == 180 and len(page["columns"]["method"]) == 150
    rest = topic_assets(session, topic.id, column="method", offset=150, limit=150)
    assert len(rest["columns"]["method"]) == 30
    names = {i["name"] for i in page["columns"]["method"]} | {i["name"] for i in rest["columns"]["method"]}
    assert len(names) == 180
    assert topic_assets(session, topic.id, role="uses")["totals"]["method"] == 0  # role filter in SQL


def test_chinese_keyword_search(library, session):
    from rhizome.pipeline import decisions

    g = Graph(session)
    m = g.by_alias("method", "RootNet")
    decisions.record(g, "add_alias", {"key": m.key, "alias": "根系调控网络推断"})
    ds = g.by_key("dataset:GSE999001")
    decisions.record(g, "add_alias", {"key": ds.key, "alias": "水稻"})
    types = Filters().types
    assert keyword_ids(session, "调控网络推断", types)[0] == m.id  # 3+ characters: trigram windows
    assert keyword_ids(session, "水稻", types)[0] == ds.id  # 2 characters: substring match
    assert keyword_ids(session, "根", types) == [] or m.id not in keyword_ids(session, "根", types)[:1]
    assert search(session, "调控网络推断")[0]["key"] == m.key
