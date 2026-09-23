"""Shared-source sync runs: every scheduled or manual RealShort pull leaves one row."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite commits DDL outside the migration transaction; a retry after a failed batch copy
    # must not trip over the table and index that already landed.
    if not sa.inspect(op.get_bind()).has_table("ggwp_sync_runs"):
        _create_sync_runs()
    if not any(index["name"] == "ggwp_sync_runs_started" for index in sa.inspect(op.get_bind()).get_indexes("ggwp_sync_runs")):
        op.create_index("ggwp_sync_runs_started", "ggwp_sync_runs", ["source", "started_at"])
    with op.batch_alter_table("ggwp_import_batches") as batch:
        batch.drop_constraint("ggwp_batch_status", type_="check")
        batch.create_check_constraint("ggwp_batch_status", "status IN ('importing', 'published', 'failed', 'pruned')")


def _create_sync_runs():
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


def downgrade():
    # Pruned batches lost their rows for good; 0002 has no word for that, so they read as failed.
    op.execute("UPDATE ggwp_import_batches SET status = 'failed' WHERE status = 'pruned'")
    with op.batch_alter_table("ggwp_import_batches") as batch:
        batch.drop_constraint("ggwp_batch_status", type_="check")
        batch.create_check_constraint("ggwp_batch_status", "status IN ('importing', 'published', 'failed')")
    op.drop_index("ggwp_sync_runs_started", table_name="ggwp_sync_runs")
    op.drop_table("ggwp_sync_runs")
