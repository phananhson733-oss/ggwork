"""Private content drafts, immutable row source bindings and command receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ggwp_content_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint("version >= 1", name="ggwp_content_plan_version"),
    )
    op.create_index("ggwp_content_plans_owner_updated", "ggwp_content_plans", ["owner_id", "updated_at", "id"])
    op.create_table(
        "ggwp_content_plan_rows",
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column("row_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer),
        sa.Column("source_json", sa.JSON, nullable=False),
        sa.Column("editable_json", sa.JSON, nullable=False),
        sa.Column("scheduled_at", sa.String(40)),
        sa.CheckConstraint("position IS NULL OR position >= 0", name="ggwp_content_plan_row_position"),
    )
    op.create_table(
        "ggwp_content_plan_commands",
        sa.Column("owner_id", sa.String(128), primary_key=True),
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("receipt_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        roles = list(bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars())
        for table in ("ggwp_content_plans", "ggwp_content_plan_rows", "ggwp_content_plan_commands"):
            op.execute(sa.text(f'REVOKE ALL ON TABLE "{table}" FROM PUBLIC'))
            for role in roles:
                op.execute(sa.text(f'REVOKE ALL ON TABLE "{table}" FROM "{role}"'))


def downgrade():
    for table in ("ggwp_content_plan_commands", "ggwp_content_plan_rows", "ggwp_content_plans"):
        op.drop_table(table)
