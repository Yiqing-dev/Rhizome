# SPDX-License-Identifier: Apache-2.0
"""File-local RXF item ids (d1, m2) were stored as an 'id' attribute of datasets and methods and
shown on cards; drop them and refresh the full-text rows that contained them.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "sqlite":
        return
    conn.execute(sa.text("update entity set attrs = json_remove(attrs, '$.id') "
                         "where type in ('dataset', 'method') and json_type(attrs, '$.id') is not null"))
    if conn.execute(sa.text("select 1 from sqlite_master where type='table' and name='entity_fts'")).first():
        from rhizome.pipeline.graph import rebuild_fts

        rebuild_fts(conn)


def downgrade() -> None:
    pass
