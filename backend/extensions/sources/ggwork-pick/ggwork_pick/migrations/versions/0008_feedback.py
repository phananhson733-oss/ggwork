"""Private owner-scoped feedback snapshots, leases and immutable result evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _s(name, size=64, *, primary=False, nullable=False):
    return sa.Column(name, sa.String(size), primary_key=primary, nullable=False if primary else nullable)


def upgrade():
    definitions = {
        "ggwp_feedback_scopes": [
            _s("owner_id", 128, primary=True),
            _s("current_version_id", nullable=True),
            _s("last_verified_at", 40, nullable=True),
            _s("lease_token", nullable=True),
            _s("lease_until", 40, nullable=True),
        ],
        "ggwp_feedback_versions": [
            _s("id", primary=True),
            _s("owner_id", 128),
            _s("content_hash"),
            _s("scan_started_at", 40),
            _s("scan_completed_at", 40),
            _s("published_at", 40),
            sa.Column("manifest_json", sa.JSON, nullable=False),
            sa.UniqueConstraint("owner_id", "content_hash", name="ggwp_feedback_version_content"),
        ],
        "ggwp_feedback_records": [
            _s("version_id", primary=True),
            _s("table_id", 128, primary=True),
            _s("record_id", 128, primary=True),
            sa.Column("values_json", sa.JSON, nullable=False),
        ],
        "ggwp_feedback_runs": [
            _s("id", primary=True),
            _s("owner_id", 128),
            _s("trigger", 20),
            _s("status", 20),
            _s("started_at", 40),
            _s("finished_at", 40, nullable=True),
            _s("version_id", nullable=True),
            _s("error_code", 40, nullable=True),
        ],
        "ggwp_feedback_result_evidence": [
            _s("result_id", primary=True),
            _s("owner_id", 128),
            _s("version_id"),
            sa.Column("evidence_json", sa.JSON, nullable=False),
            _s("created_at", 40),
        ],
        "ggwp_feedback_identity_links": [
            _s("owner_id", 128, primary=True),
            _s("source_record_id", 128, primary=True),
            _s("catalog_identity", 512),
            _s("method", 40),
            sa.Column("evidence_json", sa.JSON, nullable=False),
            _s("confirmed_at", 40),
        ],
    }
    bind = op.get_bind()
    for name, columns in definitions.items():
        if not sa.inspect(bind).has_table(name):
            op.create_table(name, *columns)
    op.execute("CREATE INDEX IF NOT EXISTS ggwp_feedback_runs_owner_started ON ggwp_feedback_runs (owner_id, started_at)")
    # No privileges for the public board reader or shared-source readers. On hosts with API defaults, revoke them.
    if bind.dialect.name == "postgresql":
        roles = list(bind.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")).scalars())
        for name in definitions:
            op.execute(sa.text(f'REVOKE ALL ON TABLE "{name}" FROM PUBLIC'))
            for role in roles:
                op.execute(sa.text(f'REVOKE ALL ON TABLE "{name}" FROM "{role}"'))


def downgrade():
    # Deliberate operator operation only: normal rollback disables the feature and preserves evidence.
    for name in ("identity_links", "result_evidence", "runs", "records", "versions", "scopes"):
        op.drop_table(f"ggwp_feedback_{name}")
