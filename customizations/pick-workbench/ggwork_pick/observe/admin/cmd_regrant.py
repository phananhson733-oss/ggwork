"""`python -m ggwork_pick.observe.admin regrant [--check]`: give pick_observer every grant it should hold (plan TR-12; D3, D15).

In the gateway container through railway ssh, as the owner of the tables (the gateway's own PICK_DATABASE_URL, deerflow_app):
    cd /app/backend && python -m ggwork_pick.observe.admin regrant --check
    cd /app/backend && python -m ggwork_pick.observe.admin regrant

The grants are migration 0007's list and, for every published mirror version, USAGE on its schema and SELECT on its rs_ids,
what each publish gives from 0007 on (ggwork_pick.observe.grants). regrant gives them all in one transaction, which is
idempotent, and reads the catalog back before it commits: anything still missing rolls the transaction back. --check
only reads. Either way a grant beyond the list (another table or column, pick_obs) is listed and exits 1; regrant never
revokes, the runbook (observe-runbook/observer-role.md) says how.

Refused before any grant (exit 2): PICK_DATABASE_URL or PGSSLMODE missing, a malformed PICK_OBS_OBSERVER_ROLE, the role
missing, a connection that does not own the tables (the crons' observer DSN, the reader), the tables missing (0007 not
run yet). Exit 0 all granted, 1 something missing or beyond the list. Nothing printed carries the DSN or a password.
"""

import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ggwork_pick.mirror.connection import SEARCH_PATH, dsn_from_env
from ggwork_pick.mirror.publish import observer_role
from ggwork_pick.observe.admin.args import command_parser
from ggwork_pick.observe.errors import ExitCode, ObserveFailure, Refused
from ggwork_pick.observe.grants import Grant, Layout, describe, grant_all, held, read_layout, refuse_unless_ready

NAME = "regrant"
DESCRIPTION = "补齐观测角色 pick_observer 的授权（0007 的表级授权与每个已发布镜像版本的 rs_ids），并核对有没有越权"
SSL_MODE_ENV = "PGSSLMODE"
APPLICATION_NAME = "ggwp-obs-regrant"
COMMAND_TIMEOUT = 60
# A GRANT waits on whatever holds the object's catalog row (a mirror build, the retention's DROP): give up and rerun.
LIMITS = ("SET LOCAL lock_timeout = '10s'", "SET LOCAL statement_timeout = '60s'")
KINDS = (("schema", "schema", "个"), ("table", "表", "张"), ("column", "列", "个"), ("sequence", "序列", "个"))


@dataclass(frozen=True)
class Report:
    layout: Layout
    held: frozenset[Grant]
    granted: bool

    @property
    def missing(self) -> frozenset[Grant]:
        return self.layout.expected() - self.held

    @property
    def extra(self) -> frozenset[Grant]:
        return self.held - self.layout.expected()


def main(argv: Sequence[str]) -> int:
    parser = command_parser(NAME, DESCRIPTION)
    parser.add_argument("--check", action="store_true", help="只核对不授权：缺授权或越权时以 1 退出")
    args = parser.parse_args(list(argv))
    dsn, role = configuration(os.environ)
    return asyncio.run(regrant(dsn, role, check=args.check))


def configuration(environ: Mapping[str, str]) -> tuple[str, str]:
    """(DSN, observer role) from the gateway's variables; Refused naming the variable, never echoing its value."""
    try:
        dsn = dsn_from_env(environ)
    except ValueError as exc:
        raise Refused(str(exc)) from None
    if SSL_MODE_ENV not in environ:
        raise Refused(f"缺少环境变量 {SSL_MODE_ENV}（gateway 上是 require；连接串里不写 ssl 参数）")
    try:
        return dsn, observer_role(environ=environ)
    except ValueError as exc:
        raise Refused(str(exc)) from None


async def regrant(dsn: str, role: str, *, check: bool = False, out: TextIO | None = None) -> int:
    """Give (or with check, only read) the observer's grants on the database at `dsn`; returns the exit status."""
    role = observer_role(role)
    engine = _engine(dsn)
    try:
        async with AsyncSession(engine) as session:
            report = await (_check(session, role) if check else _grant(session, role))
    finally:
        await engine.dispose()
    return _print(report, out or sys.stdout)


def _engine(dsn: str):
    """One connection at a time, the gateway's search_path; the host's JSON serializer object as every observe engine
    takes it (D1), although regrant writes no JSON."""
    from deerflow.persistence.engine import _json_serializer

    return create_async_engine(
        make_url(dsn).set(drivername="postgresql+asyncpg"),
        poolclass=NullPool,
        json_serializer=_json_serializer,
        connect_args={"server_settings": {"search_path": SEARCH_PATH, "application_name": APPLICATION_NAME}, "command_timeout": COMMAND_TIMEOUT},
    )


async def _prepare(session, role: str) -> Layout:
    for statement in LIMITS:
        await session.execute(text(statement))
    return await read_layout(session, await refuse_unless_ready(session, role))


async def _grant(session, role: str) -> Report:
    async with session.begin():
        layout = await _prepare(session, role)
        await grant_all(session, role, layout)
        report = Report(layout, await held(session, role), granted=True)
        if report.missing:
            # A GRANT that only warned: nothing of this run stays.
            raise ObserveFailure("授权后读回仍有缺项，整个事务已回滚：" + "；".join(line.strip() for line in describe(report.missing)))
    return report


async def _check(session, role: str) -> Report:
    async with session.begin():
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        layout = await _prepare(session, role)
        return Report(layout, await held(session, role), granted=False)


def _counts(grants: frozenset[Grant]) -> str:
    return "、".join(f"{label} {len({grant.target for grant in grants if grant.kind == kind})} {unit}" for kind, label, unit in KINDS)


def _print(report: Report, out: TextIO) -> int:
    layout = report.layout
    versions = f"，已发布镜像版本 {len(layout.versions)} 个{'（' + '、'.join(layout.versions) + '）' if layout.versions else ''}"
    scope = f"{_counts(layout.expected())}{versions}"
    if report.granted:
        lines = [f"regrant：观测角色的授权已补齐：{scope}"]
    elif report.missing:
        lines = [f"regrant --check：缺少授权 {len(report.missing)} 项，执行 regrant 补齐：", *describe(report.missing)]
    else:
        lines = [f"regrant --check：授权齐全：{scope}"]
    if layout.bare_versions:
        lines.append(f"已发布但没有 rs_ids 的镜像版本（没有可授的表，跳过）：{'、'.join(layout.bare_versions)}")
    if report.extra:
        lines.extend([f"越权 {len(report.extra)} 项（regrant 不收回，按 observer-role.md「越权」处理）：", *describe(report.extra)])
    for line in lines:
        print(line, file=out, flush=True)
    return int(ExitCode.FAILED) if report.missing or report.extra else int(ExitCode.OK)
