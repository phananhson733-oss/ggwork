"""Bind query replay to its normalized conditions and refresh intent."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ggwp_candidate_sets", sa.Column("request_hash", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("ggwp_candidate_sets") as batch:
        batch.drop_column("request_hash")
