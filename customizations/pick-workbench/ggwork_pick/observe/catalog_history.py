"""The shared catalog as D24's lapse fold reads it: the batches that became current since a moment (G3 P2-2; plan D24,
TR-35, TR-20).

A correspondence confirmation lapses for good once a shared catalog batch shows its identity otherwise
(decisions_state.lapse). When a Trends session starts, the collector reads the shared catalog batches
(ggwp_import_batches: owner system:shared, kind catalog) published after the previous Trends set was read, through the
batch this session judges with, oldest first, and in each the correspondence key of every identity it asks about
(ggwp_drama_versions). Two things about the table shape the read:
- a batch whose content comes back is published again with a new published_at (repository._reuse: A, B, then A), so
  batches are ordered by their published_at as it is now and read from `after`, a moment, never from a batch id;
- an old batch's rows are deleted when it is pruned (repository.prune_shared keeps the newest three and the ones still
  referenced), which leaves a Sighting with keys None: nothing is known, and every confirmation it could contradict
  lapses. The batches forget within days; the sets' frozen lapses are what carries a confirmation's past.

key_of is the function that gives a judgment row its theater and normalized title (TR-18's), so a sighting and the row
compare alike; a drama it gives no key for counts as absent.

SELECTs only, on both dialects: pick_observer may read both tables (0007's OBSERVER_READS). `conn` is the caller's
AsyncConnection, AsyncSession or read step; the caller owns the transaction.
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Any

from sqlalchemy import select

from ggwork_pick.models import drama_versions, import_batches
from ggwork_pick.observe.contract import STAMP_PATTERN
from ggwork_pick.observe.decisions_state import CorrespondenceKey, Sighting
from ggwork_pick.observe.errors import StateUnavailable

SHARED_OWNER = "system:shared"  # repository.SHARED_OWNER; the collectors never import the gateway's repository
CATALOG_KIND = "catalog"
_READABLE = ("published", "pruned")  # a pruned batch keeps its row and its published_at, not its dramas
_STAMP = re.compile(rf"^{STAMP_PATTERN}$")

KeyOf = Callable[[Mapping[str, Any]], CorrespondenceKey | None]


class CatalogHistoryError(StateUnavailable):
    """The batch a session judges with is not a published shared catalog batch: nothing is folded on a guessed batch."""


def _shared_catalog() -> tuple:
    return (import_batches.c.owner_id == SHARED_OWNER, import_batches.c.kind == CATALOG_KIND)


def _require_identities(identities: Iterable[str]) -> tuple[str, ...]:
    asked = tuple(identities)
    if not all(isinstance(identity, str) and identity for identity in asked):
        raise TypeError("identities 是身份字符串")
    return tuple(sorted(set(asked)))


def _require_after(after: Any) -> None:
    if after is not None and not (isinstance(after, str) and _STAMP.match(after)):
        raise ValueError("after 是 repository.stamp() 形式的时刻（上一个 Trends 集合读冻结输入的时刻），或 None")


async def _published_at(conn, upto: str) -> str:
    query = select(import_batches.c.published_at).where(import_batches.c.id == upto, *_shared_catalog(), import_batches.c.status == "published")
    found = (await conn.execute(query)).scalar()
    if found is None:
        raise CatalogHistoryError(f"共享剧库批次 {upto} 不存在或不是已发布的共享剧库：不在猜出来的批次上累积对应确认的失效")
    return found


async def _window(conn, upto: str, after: str | None) -> tuple[tuple[str, str], ...]:
    """(batch id, status) of the batches to read, oldest first: published after `after`, through upto, upto always."""
    upto_at = await _published_at(conn, upto)
    if after is None:
        return ((upto, "published"),)
    query = (
        select(import_batches.c.id, import_batches.c.status)
        .where(
            *_shared_catalog(),
            import_batches.c.status.in_(_READABLE),
            import_batches.c.published_at > after,
            import_batches.c.published_at <= upto_at,
        )
        .order_by(import_batches.c.published_at, import_batches.c.id)
    )
    found = tuple((row.id, row.status) for row in (await conn.execute(query)).all())
    return found if any(batch_id == upto for batch_id, _ in found) else (*found, (upto, "published"))


def _key(payload: object, key_of: KeyOf) -> CorrespondenceKey | None:
    try:
        document = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        return None
    key = key_of(document) if isinstance(document, Mapping) else None
    if key is not None and not isinstance(key, CorrespondenceKey):
        raise TypeError("key_of 返回 CorrespondenceKey 或 None")
    return key


async def _keys(conn, batch_ids: tuple[str, ...], identities: tuple[str, ...], key_of: KeyOf) -> Mapping[str, Mapping[str, CorrespondenceKey]]:
    """batch id -> identity -> key, for the batches whose rows are still there."""
    query = select(drama_versions.c.batch_id, drama_versions.c.identity, drama_versions.c.payload_json).where(
        drama_versions.c.batch_id.in_(batch_ids), drama_versions.c.identity.in_(identities)
    )
    rows = (await conn.execute(query)).all() if batch_ids else ()
    keyed = tuple((row.batch_id, row.identity, _key(row.payload_json, key_of)) for row in rows)
    return {batch_id: MappingProxyType({identity: key for held, identity, key in keyed if held == batch_id and key is not None}) for batch_id in batch_ids}


async def read_sightings(conn, identities: Iterable[str], *, upto: str, after: str | None, key_of: KeyOf) -> tuple[Sighting, ...]:
    """The shared catalog batches published after `after` through `upto`, oldest first, as decisions_state.lapse reads
    them: each with the key of every asked identity it holds, or keys None when its rows are gone.

    upto is the batch the session judges with (FrozenInputs.source_catalog_batch_id), always read, even when it was
    current already at `after`. after is the moment the previous Trends set read its frozen inputs; None when there is
    no previous set, and then upto alone is read. Nothing asked, nothing read.
    """
    asked = _require_identities(identities)
    _require_after(after)
    if not asked:
        return ()
    window = await _window(conn, upto, after)
    keys = await _keys(conn, tuple(batch_id for batch_id, status in window if status == "published"), asked, key_of)
    return tuple(Sighting(batch_id, keys[batch_id] if status == "published" else None) for batch_id, status in window)
