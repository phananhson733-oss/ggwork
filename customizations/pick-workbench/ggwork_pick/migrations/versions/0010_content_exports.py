"""Private execution preview receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    name = "ggwp_content_plan_previews"
    if not sa.inspect(op.get_bind()).has_table(name):
        op.create_table(
            name,
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("plan_id", sa.String(64), nullable=False),
            sa.Column("plan_version", sa.Integer, nullable=False),
            sa.Column("receipt_json", sa.JSON, nullable=False),
            sa.Column("created_at", sa.String(40), nullable=False),
            sa.CheckConstraint("plan_version >= 1", name="ggwp_content_preview_version"),
        )
    exports = "ggwp_content_plan_exports"
    if not sa.inspect(op.get_bind()).has_table(exports):
        op.create_table(
            exports,
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("plan_id", sa.String(64), nullable=False),
            sa.Column("plan_version", sa.Integer, nullable=False),
            sa.Column("preview_id", sa.String(64), nullable=False),
            sa.Column("receipt_json", sa.JSON, nullable=False),
            sa.Column("csv_bytes", sa.LargeBinary, nullable=False),
            sa.Column("created_at", sa.String(40), nullable=False),
            sa.CheckConstraint("plan_version >= 1", name="ggwp_content_export_version"),
        )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(f'REVOKE ALL ON TABLE "{name}", "{exports}" FROM PUBLIC'))
        for role in op.get_bind().execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")).scalars():
            op.execute(sa.text(f'REVOKE ALL ON TABLE "{name}", "{exports}" FROM "{role}"'))


def downgrade():
    op.drop_table("ggwp_content_plan_exports")
    op.drop_table("ggwp_content_plan_previews")
