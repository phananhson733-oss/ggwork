"""Private manual post-to-plan receipts, commands and original confirmation evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    names = ("ggwp_feedback_plan_links", "ggwp_feedback_plan_link_commands")
    if not sa.inspect(op.get_bind()).has_table(names[0]):
        op.create_table(
            names[0],
            sa.Column("owner_id", sa.String(128), primary_key=True),
            sa.Column("post_key", sa.String(512), primary_key=True),
            sa.Column("plan_id", sa.String(64), nullable=False),
            sa.Column("row_id", sa.String(64), nullable=False),
            sa.Column("receipt_json", sa.JSON, nullable=False),
            sa.Column("basis_json", sa.JSON, nullable=False),
        )
    if not sa.inspect(op.get_bind()).has_table(names[1]):
        op.create_table(
            names[1],
            sa.Column("owner_id", sa.String(128), primary_key=True),
            sa.Column("request_id", sa.String(128), primary_key=True),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("receipt_json", sa.JSON, nullable=False),
            sa.Column("basis_json", sa.JSON, nullable=False),
            sa.Column("created_at", sa.String(40), nullable=False),
        )
    if op.get_bind().dialect.name == "postgresql":
        for name in names:
            op.execute(sa.text(f'REVOKE ALL ON TABLE "{name}" FROM PUBLIC'))
            for role in op.get_bind().execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars():
                op.execute(sa.text(f'REVOKE ALL ON TABLE "{name}" FROM "{role}"'))


def downgrade():
    op.drop_table("ggwp_feedback_plan_link_commands")
    op.drop_table("ggwp_feedback_plan_links")
