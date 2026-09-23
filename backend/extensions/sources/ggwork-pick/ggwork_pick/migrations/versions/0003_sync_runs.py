"""Shared-source sync runs: every scheduled or manual RealShort pull leaves one row."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ggwp_sync_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40)),
        sa.Column("rows", sa.Integer),
        sa.Column("catalog_batch_id", sa.String(64)),
        sa.Column("knowledge_batch_id", sa.String(64)),
        sa.Column("source_as_of", sa.String(40)),
        sa.Column("error", sa.Text),
    )
    op.create_index("ggwp_sync_runs_started", "ggwp_sync_runs", ["source", "started_at"])
    # Old shared batches can have their rows pruned; the batch row stays as history.
    with op.batch_alter_table("ggwp_import_batches") as batch:
        batch.drop_constraint("ggwp_batch_status", type_="check")
        batch.create_check_constraint("ggwp_batch_status", "status IN ('importing', 'published', 'failed', 'pruned')")


def downgrade():
    with op.batch_alter_table("ggwp_import_batches") as batch:
        batch.drop_constraint("ggwp_batch_status", type_="check")
        batch.create_check_constraint("ggwp_batch_status", "status IN ('importing', 'published', 'failed')")
    op.drop_index("ggwp_sync_runs_started", table_name="ggwp_sync_runs")
    op.drop_table("ggwp_sync_runs")
