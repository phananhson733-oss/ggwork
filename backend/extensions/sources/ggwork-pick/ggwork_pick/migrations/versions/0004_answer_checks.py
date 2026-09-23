"""Answer-check notes, stored beside the answer instead of rewriting the model message."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite commits each DDL statement on its own, so a retry may find the table without its index.
    if not sa.inspect(op.get_bind()).has_table("ggwp_answer_checks"):
        _create_table()
    if not _has_index("ggwp_answer_checks", "ggwp_answer_checks_thread"):
        op.create_index("ggwp_answer_checks_thread", "ggwp_answer_checks", ["owner_id", "thread_id"])


def _has_index(table: str, name: str) -> bool:
    return any(index["name"] == name for index in sa.inspect(op.get_bind()).get_indexes(table))


def _create_table():
    op.create_table(
        "ggwp_answer_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("message_id", sa.String(256)),  # null when the model message had no id
        sa.Column("notes_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner_id", "message_id", name="ggwp_answer_check_message_unique"),
    )


def downgrade():
    op.drop_index("ggwp_answer_checks_thread", table_name="ggwp_answer_checks")
    op.drop_table("ggwp_answer_checks")
