"""Private export schema and stable spreadsheet encoding contract."""

import csv
import io

import pytest

from ggwork_pick.planning_export import execution_csv


@pytest.mark.parametrize("value", ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\ttext", "\rtext", "\ntext", " \t=1+1", "\u2003@SUM(A1)"])
def test_every_execution_csv_text_cell_neutralizes_formula_prefixes(value):
    row = {
        key: value
        for key in (
            "row_id",
            "identity",
            "source_result_id",
            "source_item_id",
            "title",
            "theater",
            "language",
            "account",
            "channel",
            "local_time",
            "scheduled_at",
            "copy_text",
            "note",
        )
    }
    data = execution_csv({"id": value, "version": 1, "timezone": value, "rows": [row]})
    cells = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))[1]
    assert cells[1] == "1" and all(cell == "'" + value for i, cell in enumerate(cells) if i != 1)


@pytest.mark.asyncio
async def test_export_private_schema_actual_constraints_and_no_reader_grants(app_client):
    import os

    from sqlalchemy import insert, inspect, text
    from sqlalchemy.exc import IntegrityError

    from ggwork_pick.models import content_plan_exports, content_plan_previews

    _, service = app_client
    engine = service.session_factory.kw["bind"]
    async with engine.connect() as conn:
        checks = await conn.run_sync(lambda c: {t.name: inspect(c).get_check_constraints(t.name) for t in (content_plan_exports, content_plan_previews)})
        assert "ggwp_content_export_version" in {c["name"] for c in checks[content_plan_exports.name]}
        assert "ggwp_content_preview_version" in {c["name"] for c in checks[content_plan_previews.name]}
        if engine.dialect.name == "postgresql":
            public = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
                    "WHERE n.nspname=current_schema() AND c.relname IN ('ggwp_content_plan_exports','ggwp_content_plan_previews') AND a.grantee=0"
                )
            )
            assert public == 0
            allowed = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_roles r CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() AND c.relname IN ('ggwp_content_plan_exports','ggwp_content_plan_previews') "
                    "AND (r.rolname IN ('anon','authenticated') OR r.rolname=:reader) "
                    "AND (has_table_privilege(r.oid,c.oid,'SELECT') OR has_table_privilege(r.oid,c.oid,'INSERT') "
                    "OR has_table_privilege(r.oid,c.oid,'UPDATE') OR has_table_privilege(r.oid,c.oid,'DELETE'))"
                ),
                {"reader": os.environ["PICK_MIRROR_READER_ROLE"]},
            )
            assert allowed == 0
    for table in (content_plan_exports, content_plan_previews):
        row = {"id": "invalid", "owner_id": "alice", "plan_id": "none", "plan_version": 0, "receipt_json": {}, "created_at": "now"}
        if table is content_plan_exports:
            row.update(preview_id="none", csv_bytes=b"")
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(insert(table).values(**row))
