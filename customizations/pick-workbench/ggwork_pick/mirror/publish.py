"""The statements of the paired and the agent-only publish (plan 5.2 step 9, P2-5a), run on the ORM session.

PickRepository opens the transaction (the shared owner's _write) and calls these in order; nothing here commits.
Every UPDATE that must hit exactly one row checks it, so a mismatch raises and the caller's transaction rolls back
as a whole. pick_mirror only exists on PostgreSQL: callers skip or refuse the mirror statements on SQLite (U35).
Identifiers are checked against fixed patterns before they are quoted into a statement; values are always bound.
"""

import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime

from sqlalchemy import text, update

from ggwork_pick.models import import_batches

READER_ROLE_ENV = "PICK_MIRROR_READER_ROLE"
DEFAULT_READER_ROLE = "pick_board_reader"
# The trends radar's collectors (TR-12, D15): each version's rs_ids and nothing else of it; migration 0007 reads the same variable.
OBSERVER_ROLE_ENV = "PICK_OBS_OBSERVER_ROLE"
DEFAULT_OBSERVER_ROLE = "pick_observer"
# Same rule as migration 0006's reader role check; the migration is frozen, so the pattern is repeated here.
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_VERSION_SCHEMA = re.compile(r"pickm_v[0-9]{6}")
# The fixed failure codes (U11): only these ever reach control.last_failure, which /api/pick/sync shows everyone.
FAILURE_REASONS = ("capacity", "fallback_v1", "v1", "drift", "busy")
# From this many failures in a row the run logs an ERROR (plan 808, U50) and /api/pick/sync raises mirror.alert (P2-8b).
ALERT_AFTER = 3
_DEGRADED = re.compile(r"degraded:[A-Za-z][A-Za-z0-9_]{0,63}")
PAIRED_KINDS = ("catalog", "knowledge")
_PAIRED_COLUMN = {"catalog": "agent_catalog_batch_id", "knowledge": "agent_knowledge_batch_id"}  # constants, never input
# A batch update may carry only what the reuse path deferred (repository._reuse), never a status or a time.
DEFERRED_KEYS = frozenset({"source_as_of", "validation_json"})

_SUPERSEDE = "UPDATE pick_mirror.versions SET superseded_at = :t WHERE status = 'published' AND superseded_at IS NULL"
_FLIP = (
    "UPDATE pick_mirror.versions SET status = 'published', published_at = :t, agent_catalog_batch_id = :c, agent_knowledge_batch_id = :k"
    " WHERE id = :v AND schema_name = :s AND status = 'building'"
)
# accept_empty_once goes back to false only when this run's G9 passed on it and nobody set it again since the gate
# read accept_empty_set_at: a later accept-empty is the operator's next pass, not this one.
_SETTLE = (
    "UPDATE pick_mirror.control SET consecutive_failures = 0,"
    " accept_empty_once = CASE WHEN CAST(:used AS boolean) AND accept_empty_set_at IS NOT DISTINCT FROM CAST(:seen AS timestamptz)"
    " THEN false ELSE accept_empty_once END"
    " WHERE id = 1"
)
_FAIL = (
    "UPDATE pick_mirror.control SET consecutive_failures = consecutive_failures + 1, last_failure_at = :t, last_failure = :reason"
    " WHERE id = 1 RETURNING consecutive_failures"
)


class MirrorPublishError(RuntimeError):
    """A publish statement did not hit the one row it must; the whole transaction is rolled back."""


def reader_role(value: str | None = None, *, environ: Mapping[str, str] | None = None) -> str:
    """The reader role to grant, as migration 0006 reads it: the argument, else PICK_MIRROR_READER_ROLE, else the default."""
    role = value if value is not None else ((environ if environ is not None else os.environ).get(READER_ROLE_ENV) or DEFAULT_READER_ROLE)
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise ValueError(f"{READER_ROLE_ENV} 必须是小写字母、数字、下划线组成的角色名：以字母或下划线开头，不超过 63 个字符")
    return role


def observer_role(value: str | None = None, *, environ: Mapping[str, str] | None = None) -> str:
    """The observer role to grant, as migration 0007 reads it: the argument, else PICK_OBS_OBSERVER_ROLE, else the default."""
    role = value if value is not None else ((environ if environ is not None else os.environ).get(OBSERVER_ROLE_ENV) or DEFAULT_OBSERVER_ROLE)
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise ValueError(f"{OBSERVER_ROLE_ENV} 必须是小写字母、数字、下划线组成的角色名：以字母或下划线开头，不超过 63 个字符")
    return role


def check_schema_name(schema_name: object) -> str:
    if not isinstance(schema_name, str) or not _VERSION_SCHEMA.fullmatch(schema_name):
        raise ValueError("版本 schema 名必须形如 pickm_v000123")
    return schema_name


def check_reason(reason: object) -> str:
    if not isinstance(reason, str) or not (reason in FAILURE_REASONS or _DEGRADED.fullmatch(reason)):
        raise ValueError("镜像失败代号无效：只能是 capacity、fallback_v1、v1、drift、busy 或 degraded:<闸门或错误类>")
    return reason


def check_moment(t: object) -> datetime:
    if not isinstance(t, datetime) or t.tzinfo is None:
        raise ValueError("发布时刻必须是带时区的 datetime")
    return t


def pair_ids(batches: Iterable[Mapping]) -> tuple[str, str]:
    """(catalog id, knowledge id) of a paired publish: exactly one batch of each kind (U8)."""
    by_kind: dict[str, list[str]] = {}
    for batch in batches:
        by_kind.setdefault(batch.get("kind"), []).append(batch.get("id"))
    if sorted(by_kind) != sorted(PAIRED_KINDS) or any(len(ids) != 1 or not isinstance(ids[0], str) for ids in by_kind.values()):
        raise ValueError("配对发布需要正好一个剧库批次和一个知识批次")
    return by_kind["catalog"][0], by_kind["knowledge"][0]


def batch_values(batch: Mapping, published_at: str) -> dict:
    deferred = batch.get("deferred") or {}
    if not set(deferred) <= DEFERRED_KEYS:
        raise ValueError("推迟写入只能包含 source_as_of 与 validation_json")
    return {**deferred, "status": "published", "published_at": published_at}


def is_postgres(session) -> bool:
    return session.bind.dialect.name == "postgresql"


async def publish_batches(session, owner_id: str, updates: Iterable[tuple[str, dict]]) -> None:
    """Step 2: each staged or reused batch becomes published at the publish time, with what its reuse deferred."""
    for batch_id, values in updates:
        result = await session.execute(
            update(import_batches)
            .where(import_batches.c.id == batch_id, import_batches.c.owner_id == owner_id, import_batches.c.status.in_(("importing", "published")))
            .values(**values)
        )
        if result.rowcount != 1:
            raise MirrorPublishError("有批次不在暂存或已发布状态，本次不发布")


async def grant_reader(session, schema_name: str, role: str) -> bool:
    """Step 1: the reader sees the version from the commit on; a missing role is skipped like migration 0006 does."""
    if (await session.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).first() is None:
        return False
    quote = session.bind.dialect.identifier_preparer.quote
    schema, grantee = quote(check_schema_name(schema_name)), quote(reader_role(role))
    await session.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {grantee}"))
    await session.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {grantee}"))
    return True


async def grant_observer(session, schema_name: str, role: str) -> bool:
    """Step 1 as well (trends radar D15): the observer resolves non-canonical ids through the version's rs_ids and reads
    nothing else of it. Skipped like migration 0007 skips it when the role does not exist, and when the version has no
    rs_ids, so the observer never fails a pair; `observe.admin regrant --check` reports a version without it."""
    schema, grantee = check_schema_name(schema_name), observer_role(role)
    if (await session.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": grantee})).first() is None:
        return False
    quote = session.bind.dialect.identifier_preparer.quote
    schema, grantee = quote(schema), quote(grantee)
    if (await session.execute(text("SELECT to_regclass(:t)"), {"t": f"{schema}.rs_ids"})).scalar() is None:
        return False
    await session.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {grantee}"))
    await session.execute(text(f"GRANT SELECT ON {schema}.rs_ids TO {grantee}"))
    return True


async def flip_version(session, *, version_id: int, schema_name: str, t: datetime, catalog_id: str, knowledge_id: str) -> None:
    """Steps 3 and 4: the published version is superseded, the building one becomes current with its pair."""
    await session.execute(text(_SUPERSEDE), {"t": t})
    result = await session.execute(text(_FLIP), {"t": t, "c": catalog_id, "k": knowledge_id, "v": version_id, "s": schema_name})
    if result.rowcount != 1:
        raise MirrorPublishError("镜像版本不在 building 状态，本次不发布")


async def settle_control(session, *, accept_empty_used: bool, accept_empty_seen: datetime | None) -> None:
    """Step 5: a paired publish clears the failure count and consumes the accept-empty it used."""
    result = await session.execute(text(_SETTLE), {"used": bool(accept_empty_used), "seen": accept_empty_seen})
    if result.rowcount != 1:
        raise MirrorPublishError("pick_mirror.control 缺少那一行")


async def record_failure(session, reason: str, t: datetime) -> int:
    """One more run that did not publish a pair (U11); returns the new count."""
    count = (await session.execute(text(_FAIL), {"t": t, "reason": check_reason(reason)})).scalar_one_or_none()
    if count is None:
        raise MirrorPublishError("pick_mirror.control 缺少那一行")
    return count


async def paired_batch_ids(session, kind: str) -> set[str]:
    """Batches a still-published version is paired with; batch pruning keeps them (U25). Empty on SQLite."""
    if not is_postgres(session) or kind not in _PAIRED_COLUMN:
        return set()
    column = _PAIRED_COLUMN[kind]
    rows = await session.execute(text(f"SELECT {column} FROM pick_mirror.versions WHERE status = 'published' AND {column} IS NOT NULL"))
    return set(rows.scalars())
