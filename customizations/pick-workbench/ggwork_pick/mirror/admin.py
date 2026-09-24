"""The mirror's two operator commands (plan 5.3, 5.5, 6.5, 947-950; implementation note P2-5c admin.py; U40, U41).

Through railway ssh, written out in full (plan 6.5: the entry depends on no cwd and no app config):
    cd /app/backend && DEER_FLOW_HOME=/data python -m ggwork_pick.mirror.admin accept-empty
    cd /app/backend && DEER_FLOW_HOME=/data python -m ggwork_pick.mirror.admin cleanup

accept-empty: G9 held a publish back because a table that had rows is empty now, and the operator has checked that the
emptiness is real (plan 791). It sets pick_mirror.control.accept_empty_once and stamps accept_empty_set_at with the
database's now(): the next run whose G9 leans on it consumes it in its pair transaction, and only if nobody set it again
after the gate read that stamp (publish._SETTLE). No lock (U40): it writes the one row a run reads; a run under way
either saw it or did not, and the next one will.

cleanup: what a run killed mid-way left, for when the next sync is hours off or the mirror was switched off (plan 9 step
6). It takes the mirror lock as "cleanup" on a dedicated connection and gives up at once when the sync or the backfill
holds it (exit 1, nothing changed); then runs the sync's own clean_leftovers (building versions failed, pickm_v* schemas
no building or published version claims dropped, importing batches failed and their blobs deleted), then deletes the
blobs of every failed batch no live batch shares (a process that died between failing a batch and deleting its file left
it there). A step refused for want of privilege (42501, or a file it may not delete) is reported and the rest still runs.
Blobs are deleted only under $DEER_FLOW_HOME/pick; DEER_FLOW_HOME defaults to /data, the gateway's (pick_entrypoint).

Both need PICK_DATABASE_URL and PGSSLMODE (present, whatever its value: production sets require, and the URL never carries
ssl* parameters). Nothing here imports app.gateway.* (it needs cwd /app/backend and the app config), and no output
carries a variable's value or a database message: errors name their class and SQLSTATE.
Exit status: 0 done, 1 the lock is taken, 2 usage or a missing variable, 3 failed, 130 interrupted.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, TextIO

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.mirror.connection import DATABASE_URL_ENV, SEARCH_PATH, MirrorConnectionError, dsn_from_env, open_dedicated
from ggwork_pick.mirror.lock import lock_status, mirror_lock
from ggwork_pick.mirror.run import clean_leftovers, describe_db_error, tolerate_permission
from ggwork_pick.repository import PickRepository, stamp

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_LOCKED, EXIT_USAGE, EXIT_FAILED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
PROG = "python -m ggwork_pick.mirror.admin"
ACCEPT_EMPTY, CLEANUP = "accept-empty", "cleanup"
USAGE = f"用法：{PROG} accept-empty | cleanup（accept-empty：放行下一次因空表被 G9 拦下的发布，只一次；cleanup：取镜像锁清理死掉的运行留下的东西）"
HOME_ENV = "DEER_FLOW_HOME"
DEFAULT_HOME = "/data"  # the gateway's DEER_FLOW_HOME in production (pick_entrypoint)
SSL_MODE_ENV = "PGSSLMODE"
REQUIRED_ENV = (DATABASE_URL_ENV, SSL_MODE_ENV)
CLEANUP_HOLDER = "cleanup"
STATEMENT_TIMEOUT = 30.0
CLOSE_TIMEOUT = 10
APPLICATION_NAME = "ggwp-mirror-admin"
BLOB_OUTCOMES = ("deleted", "missing", "refused", "outside")
_ACCEPT_EMPTY = "UPDATE pick_mirror.control SET accept_empty_once = true, accept_empty_set_at = now() WHERE id = 1 RETURNING accept_empty_set_at"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _say(stream: TextIO, line: str) -> None:
    print(line, file=stream, flush=True)


def _describe(exc: BaseException) -> str:
    """Safe to print: the dedicated connection's own text names only a class and a SQLSTATE; anything else is described so."""
    return str(exc) if isinstance(exc, MirrorConnectionError) else describe_db_error(exc)


def data_dir_from_env(environ: Mapping[str, str]) -> Path:
    """$DEER_FLOW_HOME/pick, the extension's data directory (ggwork_pick/__init__.py); /data/pick when it is unset."""
    return Path(environ.get(HOME_ENV, "").strip() or DEFAULT_HOME) / "pick"


# ---------------------------------------------------------------- accept-empty


async def accept_empty(dsn: str, *, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Set the one-time empty-table pass (U40); no lock. Returns the exit status."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        conn = await open_dedicated(dsn)
    except MirrorConnectionError as exc:
        _say(err, f"accept-empty 未执行：{exc}")
        return EXIT_FAILED
    try:
        set_at = await conn.fetchval(_ACCEPT_EMPTY, timeout=STATEMENT_TIMEOUT)
    except Exception as exc:
        _say(err, f"accept-empty 未执行：{_describe(exc)}")
        return EXIT_FAILED
    finally:
        await _close(conn)
    if set_at is None:
        _say(err, "accept-empty 未执行：pick_mirror.control 缺少 id=1 这一行（迁移 0006 没跑过？先启动一次 gateway）")
        return EXIT_FAILED
    _say(out, f"已设置 accept_empty_once（accept_empty_set_at {stamp(set_at)}）：下一次同步若因空表被 G9 拦下，放行这一次，发布后自动置回 false")
    return EXIT_OK


async def _close(conn) -> None:
    try:
        await conn.close(timeout=CLOSE_TIMEOUT)
    except Exception:
        logger.warning("[pick-mirror] the admin connection did not close cleanly; terminated")
        conn.terminate()


# ---------------------------------------------------------------- cleanup


def admin_session_factory(dsn: str):
    """(engine, session factory) for the shared repository: one connection at a time, the gateway's search_path, and
    JSON written as the gateway's engine writes it (deerflow.persistence.engine), although the cleanup writes none."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        make_url(dsn).set(drivername="postgresql+asyncpg"),
        poolclass=NullPool,
        json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
        connect_args={"server_settings": {"search_path": SEARCH_PATH, "application_name": APPLICATION_NAME}, "command_timeout": STATEMENT_TIMEOUT},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def delete_unused(data_dir: Path, paths: Sequence[str]) -> dict[str, int]:
    """Delete each blob under data_dir, one by one: counts of deleted, already gone (missing), refused by the file system
    (PermissionError; the rest still go) and outside data_dir (never touched)."""
    root = data_dir.resolve()
    counted = Counter(_delete_one(root, Path(raw)) for raw in paths)
    return {outcome: counted.get(outcome, 0) for outcome in BLOB_OUTCOMES}


def _delete_one(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return "outside"
    try:
        resolved.unlink()
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "refused"
    return "deleted"


async def _failed_blobs(repo: PickRepository, data_dir: Path) -> dict:
    paths, error = await tolerate_permission("failed_blob_paths", repo.failed_blob_paths)
    counts = await asyncio.to_thread(delete_unused, data_dir, paths or [])
    return {**counts, "errors": [error] if error else []}


async def _lock_taken(repo: PickRepository, clock: Callable[[], datetime]) -> str:
    async with repo.session_factory() as session:
        status = await lock_status(session, now=clock())
    since = status["holder_since"]
    when = f"，自 {stamp(since)} 起" if since is not None else ""
    return f"镜像锁被占用（持有方 {status['holder'] or '未知'}{when}，pid {status['holder_pid']}）：cleanup 退出，没有改动任何东西。等同步或回填结束再跑"


def _listed(items: Sequence) -> str:
    return f"（{'、'.join(str(item) for item in items)}）" if items else ""


def _versions_line(versions: dict | None) -> str:
    if versions is None:
        return "版本与 schema 没有清理"
    failed, dropped, busy = versions["failed_building"], versions["dropped_schemas"], versions["lock_busy"]
    return (
        f"作废 building 版本 {len(failed)} 个{_listed(failed)}，删除遗留 schema {len(dropped)} 个{_listed(dropped)}，"
        f"DROP 等锁未成 {len(busy)} 个{_listed(busy)}（下次同步或清理再删）"
    )


def summary_lines(leftovers: dict, failed: dict) -> list[str]:
    """What the cleanup did, in version ids, pickm_v names and counts only."""
    blobs = (
        f"遗留暂存批次作废后删除 blob {leftovers['blobs_deleted']} 个；failed 批次的 blob 删除 {failed['deleted']} 个，"
        f"已不存在 {failed['missing']} 个，权限拒绝 {failed['refused']} 个，不在数据目录下跳过 {failed['outside']} 个"
    )
    errors = [*leftovers["errors"], *failed["errors"]]
    return [f"清理完成：{_versions_line(leftovers['versions'])}", blobs, *(f"跳过（权限不足）：{error}" for error in errors)]


async def _cleanup(dsn: str, repo: PickRepository, *, data_dir: Path, clock: Callable[[], datetime], out: TextIO, err: TextIO) -> int:
    async with mirror_lock(dsn, holder=CLEANUP_HOLDER, clock=clock) as conn:
        if conn is None:
            _say(err, await _lock_taken(repo, clock))
            return EXIT_LOCKED
        leftovers = await clean_leftovers(conn, repo, data_dir=data_dir, clock=clock)
        failed = await _failed_blobs(repo, data_dir)
    for line in summary_lines(leftovers, failed):
        _say(out, line)
    return EXIT_OK


async def cleanup(
    dsn: str,
    *,
    data_dir: Path,
    session_factory: async_sessionmaker | None = None,
    clock: Callable[[], datetime] = _utc_now,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """The cleanup command's body under the mirror lock held as cleanup; returns the exit status. Without a session
    factory it opens its own engine on the DSN (admin_session_factory) and disposes of it on the way out."""
    out, err = out or sys.stdout, err or sys.stderr
    engine = None
    if session_factory is None:
        engine, session_factory = admin_session_factory(dsn)
    try:
        return await _cleanup(dsn, PickRepository.shared(session_factory), data_dir=data_dir, clock=clock, out=out, err=err)
    except Exception as exc:
        _say(err, f"清理中止：{_describe(exc)}。重跑是安全的：它只处理没有运行认领的遗留")
        return EXIT_FAILED
    finally:
        if engine is not None:
            await engine.dispose()


# ---------------------------------------------------------------- the command line


class UsageError(Exception):
    """A bad command line or a missing variable; the message names commands and variables, never a value."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's own message can quote what was typed; the fixed usage line says all that is needed.
        raise UsageError(USAGE)


@dataclass(frozen=True)
class _Settings:
    dsn: str = field(repr=False)
    data_dir: Path


def _command(argv: Sequence[str] | None) -> str:
    parser = _Parser(prog=PROG, description="选剧镜像的运维命令", add_help=True)
    parser.add_argument("command", choices=(ACCEPT_EMPTY, CLEANUP), help=USAGE)
    return parser.parse_args(argv).command


def _settings(environ: Mapping[str, str]) -> _Settings:
    missing = [name for name in REQUIRED_ENV if not environ.get(name, "").strip()]
    if missing:
        raise UsageError(f"缺少环境变量 {'、'.join(missing)}")
    try:
        dsn = dsn_from_env(environ)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    return _Settings(dsn=dsn, data_dir=data_dir_from_env(environ))


async def _dispatch(command: str, settings: _Settings) -> int:
    if command == ACCEPT_EMPTY:
        return await accept_empty(settings.dsn)
    return await cleanup(settings.dsn, data_dir=settings.data_dir)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`python -m ggwork_pick.mirror.admin accept-empty | cleanup`; the exit statuses are in the module docstring.
    Reads no app config and, besides DEER_FLOW_HOME for the data directory, no DEER_FLOW_* (plan 6.5)."""
    try:
        command = _command(argv)
        settings = _settings(os.environ if env is None else env)
    except UsageError as exc:
        _say(sys.stderr, f"{PROG}: {exc}")
        return EXIT_USAGE
    try:
        return asyncio.run(_dispatch(command, settings))
    except KeyboardInterrupt:
        _say(sys.stderr, f"{PROG}: 已中断。重跑是安全的")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
