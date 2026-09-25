"""`reset-disable --operator NAME`: clear the Trends channel's disabled_7d, recording who did it (plan TR-13; design 4.3).

A third extinguished target date within seven disables direct access (breaker.py, status code disabled_7d) until an
operator clears it; nothing else does (DbStateStore refuses a state that drops it). This command locks the Trends
runtime row, refuses while a collector's lease still runs (exit 1: wait for it to exit and run again), clears the
breaker's disabled_on (its history stays, so one more extinguished day inside the window disables it again), sets
disabled_at to null and records reset_by and reset_at. When nothing is disabled it writes nothing and says so.

Runs where PICK_DATABASE_URL reaches the database as the observer: in the pick-obs-trends container,
`cd /app/backend && python -m ggwork_pick.observe.admin reset-disable --operator <name>`.
"""

import asyncio
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy.engine import URL

from ggwork_pick.observe.admin.args import PROG, command_parser
from ggwork_pick.observe.clock import Clock, SystemClock
from ggwork_pick.observe.db import ADMIN_APPLICATION_NAME, ObsDatabase, database_url
from ggwork_pick.observe.errors import ObserveFailure, Refused, StateUnavailable, describe_error
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.lease import (
    DATABASE_FAILURES,
    MAX_OWNER,
    TRENDS,
    LeaseHeld,
    lease_live,
    locked_runtime,
    runtime_update,
    state_document,
    state_sections,
    stored_breaker,
)
from ggwork_pick.observe.trends import breaker

NAME = "reset-disable"


@dataclass(frozen=True)
class ResetOutcome:
    cleared: bool
    operator: str
    at: str


def checked_operator(name: str) -> str:
    """1–128 printable characters (reset_by); the value is never echoed back."""
    operator = name.strip()
    if not 0 < len(operator) <= MAX_OWNER or not operator.isprintable():
        raise Refused(f"--operator 须是 1–{MAX_OWNER} 个可打印字符")
    return operator


async def reset_disabled(url: str | URL, *, operator: str, clock: Clock) -> ResetOutcome:
    db = ObsDatabase(url, application_name=ADMIN_APPLICATION_NAME)
    try:
        async with db.transaction() as conn:
            row = await locked_runtime(conn, TRENDS)
            now = clock.now()
            if lease_live(row, now):
                raise LeaseHeld("trends 的采集进程正持有租约：等它退出（停止续租后 5 分钟过期）再重置")
            machine = stored_breaker(row)
            if row["disabled_at"] is None and (machine is None or machine.disabled_on is None):
                return ResetOutcome(False, operator, stamp(now))
            cleared = breaker.clear_disabled(machine).to_dict() if machine is not None else None
            document = state_document(state_sections(row["state_json"])["pacing"], cleared)
            values = {"state_json": document, "disabled_at": None, "reset_by": operator, "reset_at": stamp(now), "updated_at": stamp(now)}
            await conn.execute(runtime_update(TRENDS).values(**values))
    except (ValueError, TypeError):
        raise StateUnavailable("trends 运行时行里的熔断状态读不回来：没有重置") from None
    except DATABASE_FAILURES as exc:
        raise StateUnavailable("trends 运行时行读写不了：没有重置") from exc
    finally:
        await db.dispose()
    return ResetOutcome(True, operator, stamp(now))


async def run(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    parser = command_parser(NAME, "清除 trends 通道的 disabled_7d（连续 7 个目标日内熄火 3 次后的停用），并记下操作人")
    parser.add_argument("--operator", required=True, help=f"操作人，记进 reset_by；1–{MAX_OWNER} 个可打印字符")
    args = parser.parse_args(list(argv))
    out, err = out or sys.stdout, err or sys.stderr
    try:
        operator = checked_operator(args.operator)
        outcome = await reset_disabled(database_url(environ), operator=operator, clock=clock or SystemClock())
    except ObserveFailure as exc:
        print(f"{PROG} {NAME}: {describe_error(exc)}", file=err)
        return int(exc.exit_code)
    if outcome.cleared:
        print(f"已清除 trends 的 disabled（操作人 {outcome.operator}，{outcome.at}）；熔断历史保留", file=out)
    else:
        print("trends 没有处于 disabled，没有改动", file=out)
    return 0


def main(argv: Sequence[str], *, environ: Mapping[str, str] | None = None, clock: Clock | None = None) -> int:
    return asyncio.run(run(argv, environ=environ, clock=clock))
