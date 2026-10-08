"""Physical private-table constraints and role isolation on both supported databases."""

import pytest


@pytest.mark.asyncio
async def test_manual_link_private_schema(app_client):
    from sqlalchemy import inspect, text

    _, service = app_client
    engine = service.session_factory.kw["bind"]
    names = ("ggwp_feedback_plan_links", "ggwp_feedback_plan_link_commands")
    async with engine.connect() as conn:
        keys = await conn.run_sync(lambda c: {name: inspect(c).get_pk_constraint(name)["constrained_columns"] for name in names})
        assert keys == {names[0]: ["owner_id", "post_key"], names[1]: ["owner_id", "request_id"]}
        if engine.dialect.name == "postgresql":
            public = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
                    "WHERE n.nspname=current_schema() AND c.relname LIKE 'ggwp_feedback_plan_%' AND a.grantee=0"
                )
            )
            assert public == 0
            import os

            accessible = (
                await conn.execute(
                    text(
                        "SELECT r.rolname,c.relname FROM pg_roles r CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=current_schema() AND c.relname LIKE 'ggwp_feedback_plan_%' "
                        "AND (r.rolname IN ('anon','authenticated') OR r.rolname=:reader) "
                        "AND (has_table_privilege(r.oid,c.oid,'SELECT') "
                        "OR has_table_privilege(r.oid,c.oid,'INSERT') "
                        "OR has_table_privilege(r.oid,c.oid,'UPDATE') "
                        "OR has_table_privilege(r.oid,c.oid,'DELETE'))"
                    ),
                    {"reader": os.environ.get("PICK_MIRROR_READER_ROLE", "pick_board_reader")},
                )
            ).all()
            assert accessible == []
