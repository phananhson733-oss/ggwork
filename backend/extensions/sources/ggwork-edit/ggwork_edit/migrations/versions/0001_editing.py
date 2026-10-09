"""Private editing records and digest-only device credentials."""

import sqlalchemy as sa
from alembic import op

revision = "ggwe_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ggwe_records",
        sa.Column("owner_id", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_table(
        "ggwe_credentials",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
    )


def downgrade():
    op.drop_table("ggwe_credentials")
    op.drop_table("ggwe_records")
