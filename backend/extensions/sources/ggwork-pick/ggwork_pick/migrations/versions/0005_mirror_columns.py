"""Mirror bookkeeping on the ggwp tables (plan 3.7): what a query excluded, the mirror version and data_as_of it froze,
and what a sync run did. All nullable and never backfilled: a row from before P2 reads its batch exactly as before."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

INDEX = "ggwp_candidate_sets_mirror"
# sa.JSON is json on PostgreSQL like every other ggwp JSON column, never jsonb: json keeps an escaped NUL (plan 3.7).
COLUMNS = (
    ("ggwp_candidate_sets", "excluded_json", sa.JSON),
    ("ggwp_candidate_sets", "mirror_version", sa.Integer),
    ("ggwp_candidate_sets", "data_as_of_json", sa.JSON),
    ("ggwp_sync_runs", "details_json", sa.JSON),
)


def upgrade():
    # SQLite commits each DDL statement on its own, and rolling the code back means setting the version table back by
    # hand (realshort-sync.md): either way a rerun can find part or all of this revision already in place.
    for table, name, type_ in COLUMNS:
        if name not in _column_names(table):
            op.add_column(table, sa.Column(name, type_(), nullable=True))
    if not _has_index("ggwp_candidate_sets", INDEX):
        op.create_index(INDEX, "ggwp_candidate_sets", ["mirror_version", "created_at"])


def downgrade():
    # The index names mirror_version; SQLite's batch copy of the table would carry it over a dropped column.
    if _has_index("ggwp_candidate_sets", INDEX):
        op.drop_index(INDEX, table_name="ggwp_candidate_sets")
    for table in dict.fromkeys(table for table, _, _ in COLUMNS):
        _drop_columns(table, [name for owner, name, _ in COLUMNS if owner == table])


def _drop_columns(table: str, names: list[str]) -> None:
    present = _column_names(table)
    doomed = [name for name in names if name in present]
    if not doomed:
        return
    # SQLite drops a column by copying the table; batch mode does the copy, PostgreSQL gets a plain ALTER TABLE.
    with op.batch_alter_table(table) as batch:
        for name in doomed:
            batch.drop_column(name)


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    return any(index["name"] == name for index in sa.inspect(op.get_bind()).get_indexes(table))
