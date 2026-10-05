# SPDX-License-Identifier: Apache-2.0
"""Full-text index keyed by entity id, re-derived from entities and aliases

entity_fts had entity_id as an UNINDEXED column, so every reindex deleted by a full scan (27 ms at
55k rows, 12-25 times per ingest), and alias additions from auto-merges, NLI merges and add_alias
decisions never reached it (aliases were not searchable). The table now uses rowid = entity id and
every row is rebuilt with the text the app indexes today.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    # FTS is derived data: build it with the current application code, so it matches reindex()
    from rhizome.pipeline.graph import FTS_DDL, rebuild_fts

    op.execute("drop table if exists entity_fts")
    op.execute(FTS_DDL)
    rebuild_fts(op.get_bind())


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute("drop table if exists entity_fts")
    op.execute("create virtual table entity_fts using fts5(text, entity_id unindexed, tokenize='trigram')")
    op.execute("insert into entity_fts(text, entity_id) select canonical_name, id from entity")
