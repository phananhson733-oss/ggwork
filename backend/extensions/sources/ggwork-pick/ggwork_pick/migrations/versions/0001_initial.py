"""Initial personal selection storage. Keep this revision immutable."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ggwp_import_batches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_blob_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_as_of", sa.String(40)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("published_at", sa.String(40)),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("owner_id", "kind", "content_hash", name="ggwp_batch_content_unique"),
        sa.CheckConstraint("kind IN ('catalog', 'knowledge')", name="ggwp_batch_kind"),
        sa.CheckConstraint("status IN ('importing', 'published', 'failed')", name="ggwp_batch_status"),
    )
    op.create_index("ggwp_batches_owner_kind", "ggwp_import_batches", ["owner_id", "kind", "published_at"])
    op.create_table(
        "ggwp_drama_versions",
        sa.Column("batch_id", sa.String(64), primary_key=True),
        sa.Column("identity", sa.String(512), primary_key=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "ggwp_knowledge_versions",
        sa.Column("batch_id", sa.String(64), primary_key=True),
        sa.Column("document_id", sa.String(128), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "ggwp_candidate_sets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("tool_call_id", sa.String(128), nullable=False),
        sa.Column("parent_result_id", sa.String(64)),
        sa.Column("catalog_batch_id", sa.String(64), nullable=False),
        sa.Column("knowledge_batch_id", sa.String(64)),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("ranking_version", sa.String(64), nullable=False),
        sa.Column("conditions_json", sa.JSON(), nullable=False),
        sa.Column("ordered_items_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner_id", "run_id", "tool_call_id", name="ggwp_candidate_call_unique"),
    )
    op.create_index("ggwp_candidates_owner_thread", "ggwp_candidate_sets", ["owner_id", "thread_id", "created_at"])
    op.create_table(
        "ggwp_selections",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("identity", sa.String(512), nullable=False),
        sa.Column("source_result_id", sa.String(64), nullable=False),
        sa.Column("source_item_id", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner_id", "identity", name="ggwp_selection_identity_unique"),
        sa.CheckConstraint("state IN ('selected', 'removed')", name="ggwp_selection_state"),
    )
    op.create_table(
        "ggwp_selection_commands",
        sa.Column("owner_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("receipt_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )


def downgrade():
    for table in (
        "ggwp_selection_commands",
        "ggwp_selections",
        "ggwp_candidate_sets",
        "ggwp_knowledge_versions",
        "ggwp_drama_versions",
        "ggwp_import_batches",
    ):
        op.drop_table(table)
