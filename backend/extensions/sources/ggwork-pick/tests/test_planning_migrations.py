"""Private migration/role boundary on the actual SQLite and PostgreSQL schemas."""

import pytest


@pytest.mark.asyncio
async def test_plan_schema_constraints_indexes_and_private_grants(app_client):
    import os

    from sqlalchemy import insert, inspect, text
    from sqlalchemy.exc import IntegrityError

    from ggwork_pick.models import content_plan_rows, content_plans

    _, service = app_client
    engine = service.session_factory.kw["bind"]
    async with engine.connect() as conn:
        info = await conn.run_sync(
            lambda c: {
                "tables": inspect(c).get_table_names(),
                "checks": {name: inspect(c).get_check_constraints(name) for name in ["ggwp_content_plans", "ggwp_content_plan_rows"]},
                "indexes": inspect(c).get_indexes("ggwp_content_plans"),
                "pk": inspect(c).get_pk_constraint("ggwp_content_plan_rows"),
            }
        )
        assert {"ggwp_content_plans", "ggwp_content_plan_rows", "ggwp_content_plan_commands"} <= set(info["tables"])
        assert "ggwp_content_plan_version" in {c["name"] for c in info["checks"]["ggwp_content_plans"]}
        assert "ggwp_content_plan_row_position" in {c["name"] for c in info["checks"]["ggwp_content_plan_rows"]}
        assert any(i["name"] == "ggwp_content_plans_owner_updated" and i["column_names"] == ["owner_id", "updated_at", "id"] for i in info["indexes"])
        assert info["pk"]["constrained_columns"] == ["plan_id", "row_id"]
        if engine.dialect.name == "postgresql":
            public = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
                    "WHERE n.nspname=current_schema() AND c.relname LIKE 'ggwp_content_plan%' AND a.grantee=0"
                )
            )
            assert public == 0
            roles = (
                await conn.execute(
                    text(
                        "SELECT r.rolname,c.relname FROM pg_roles r CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=current_schema() AND c.relname LIKE 'ggwp_content_plan%' "
                        "AND (r.rolname IN ('anon','authenticated') OR r.rolname=:reader) "
                        "AND (has_table_privilege(r.oid,c.oid,'SELECT') OR has_table_privilege(r.oid,c.oid,'INSERT') "
                        "OR has_table_privilege(r.oid,c.oid,'UPDATE') OR has_table_privilege(r.oid,c.oid,'DELETE'))"
                    ),
                    {"reader": os.environ.get("PICK_MIRROR_READER_ROLE", "pick_board_reader")},
                )
            ).all()
            assert roles == []
    async with service.session_factory() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                await session.execute(
                    insert(content_plans).values(id="bad-version", owner_id="alice", version=0, title="bad", timezone="UTC", created_at="x", updated_at="x")
                )
    async with service.session_factory() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                await session.execute(
                    insert(content_plan_rows).values(plan_id="none", row_id="bad-pos", owner_id="alice", position=-1, source_json={}, editable_json={})
                )
