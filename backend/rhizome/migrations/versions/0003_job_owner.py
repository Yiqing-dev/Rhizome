# SPDX-License-Identifier: Apache-2.0
"""job.owner_pid: which process runs a job, so a restart only requeues jobs whose process is gone

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner_pid', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('owner_pid')
