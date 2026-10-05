# SPDX-License-Identifier: Apache-2.0
"""review_card.created_at: new cards are introduced newest first instead of by hash id

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('review_card', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('review_card', schema=None) as batch_op:
        batch_op.drop_column('created_at')
