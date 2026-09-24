"""pick_mirror's four unversioned tables on PostgreSQL (plan 3.2, 3.7): versions, series, series_state, control.

SQLite never mirrors, so this revision does nothing there. The schema itself belongs to the Supabase bootstrap
(docs/pick-workbench/supabase/bootstrap.sql), which creates it owned by deerflow_app with the reader's USAGE; the
CREATE SCHEMA IF NOT EXISTS here only matters where the bootstrap never ran, and the downgrade leaves the schema (U26).
Everything runs in the gateway's one migration transaction, so a failure anywhere leaves nothing of 0001-0006 behind.
"""

import logging
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Alembic loads revision files under generated module names ("0006_pick_mirror_py"); log under a stable one.
logger = logging.getLogger("ggwork_pick.migrations")

READER_ROLE_ENV = "PICK_MIRROR_READER_ROLE"
DEFAULT_READER_ROLE = "pick_board_reader"
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_VERSION_SCHEMA = re.compile(r"pickm_v[0-9]{6}")
# The reader gets no control: the failure counters and the empty-table pass are the writer's alone (U27).
READABLE = ("pick_mirror.versions", "pick_mirror.series", "pick_mirror.series_state")
TABLES = ("pick_mirror.control", "pick_mirror.series_state", "pick_mirror.series", "pick_mirror.versions")

CREATE = (
    "CREATE SCHEMA IF NOT EXISTS pick_mirror",
    # fingerprint is RealShort's 64-digit hex, not jsonb (U1); freshness and warnings are manifest.meta verbatim (U5).
    """CREATE TABLE IF NOT EXISTS pick_mirror.versions (
        id bigserial PRIMARY KEY,
        schema_name text NOT NULL UNIQUE,
        status text NOT NULL,
        as_of timestamptz NOT NULL,
        fingerprint text NOT NULL,
        counts jsonb,
        latest_snapshot date,
        freshness jsonb,
        warnings jsonb,
        agent_catalog_batch_id text,
        agent_knowledge_batch_id text,
        sync_run_id text,
        created_at timestamptz NOT NULL,
        published_at timestamptz,
        superseded_at timestamptz,
        dropped_at timestamptz,
        error text,
        CONSTRAINT pick_mirror_versions_schema_name CHECK (schema_name ~ '^pickm_v[0-9]{6}$'),
        CONSTRAINT pick_mirror_versions_status CHECK (status IN ('building', 'published', 'failed', 'dropped')),
        CONSTRAINT pick_mirror_versions_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{64}$')
    )""",
    "CREATE INDEX IF NOT EXISTS pick_mirror_versions_current ON pick_mirror.versions (status, published_at DESC)",
    "CREATE INDEX IF NOT EXISTS pick_mirror_versions_catalog ON pick_mirror.versions (agent_catalog_batch_id, published_at)",
    """CREATE TABLE IF NOT EXISTS pick_mirror.series (
        drama_id text PRIMARY KEY,
        days date[] NOT NULL,
        revenue_cents float8[] NOT NULL,
        promoters int[] NOT NULL,
        updated_at timestamptz NOT NULL,
        CONSTRAINT pick_mirror_series_aligned CHECK (cardinality(days) = cardinality(revenue_cents) AND cardinality(days) = cardinality(promoters))
    )""",
    """CREATE TABLE IF NOT EXISTS pick_mirror.series_state (
        id smallint PRIMARY KEY CONSTRAINT pick_mirror_series_state_singleton CHECK (id = 1),
        through date,
        trimmed_before date,
        updated_at timestamptz
    )""",
    # last_failure is the reason plan 5.7 shows (U12). lock_holder_since is when the current holder took the mirror
    # lock: behind Supavisor the next client inherits the same backend, so backend_start says nothing about the hold.
    """CREATE TABLE IF NOT EXISTS pick_mirror.control (
        id smallint PRIMARY KEY CONSTRAINT pick_mirror_control_singleton CHECK (id = 1),
        accept_empty_once boolean NOT NULL DEFAULT false,
        accept_empty_set_at timestamptz,
        consecutive_failures integer NOT NULL DEFAULT 0,
        last_failure_at timestamptz,
        last_failure text,
        lock_holder_since timestamptz
    )""",
    "INSERT INTO pick_mirror.series_state (id) VALUES (1) ON CONFLICT DO NOTHING",
    "INSERT INTO pick_mirror.control (id) VALUES (1) ON CONFLICT DO NOTHING",
)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Checked before any DDL: a bad name must stop the start, not surface later as a reader that sees nothing.
    role = _reader_role()
    for statement in CREATE:
        op.execute(statement)
    _grant_reader(bind, role)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for schema in _registered_version_schemas(bind):
        op.execute(f"DROP SCHEMA IF EXISTS {bind.dialect.identifier_preparer.quote(schema)} CASCADE")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def _reader_role() -> str:
    # An empty value reads as unset, like the other deployment variables.
    role = os.environ.get(READER_ROLE_ENV) or DEFAULT_READER_ROLE
    if not _ROLE_NAME.fullmatch(role):
        raise ValueError(f"{READER_ROLE_ENV} 必须是小写字母、数字、下划线组成的角色名：以字母或下划线开头，不超过 63 个字符")
    return role


def _grant_reader(bind, role: str) -> None:
    # GRANT to a missing role fails the statement and so the whole migration transaction: skip it instead.
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first() is None:
        logger.warning("[pick-mirror] the role %s names does not exist; pick_mirror read grants skipped", READER_ROLE_ENV)
        return
    quoted = bind.dialect.identifier_preparer.quote(role)
    op.execute(f"GRANT USAGE ON SCHEMA pick_mirror TO {quoted}")
    op.execute(f"GRANT SELECT ON {', '.join(READABLE)} TO {quoted}")


def _registered_version_schemas(bind) -> list[str]:
    """Version schemas pick_mirror.versions knows about; only names of the writer's own shape are ever dropped."""
    if bind.execute(sa.text("SELECT to_regclass('pick_mirror.versions')")).scalar() is None:
        return []
    names = bind.execute(sa.text("SELECT schema_name FROM pick_mirror.versions ORDER BY id")).scalars().all()
    return [name for name in names if _VERSION_SCHEMA.fullmatch(name)]
