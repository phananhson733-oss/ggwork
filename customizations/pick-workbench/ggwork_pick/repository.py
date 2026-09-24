"""All business reads and writes are scoped to a trusted authenticated owner."""

import hashlib
import json
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, insert, literal, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.contracts import storable
from ggwork_pick.mirror import publish as pairing
from ggwork_pick.models import answer_checks, candidate_sets, drama_versions, import_batches, knowledge_versions, selection_commands, selections, sync_runs

# Batches published by the scheduled source sync. Every user can read them; nobody can log in as this owner
# (host principals are UUIDs, and the constructor below refuses the value).
SHARED_OWNER = "system:shared"
# A shared batch a candidate snapshot used within this window keeps its rows so the card can still 换一批.
RETAIN_REFERENCED = timedelta(days=30)
# Also exactly the keys a result freezes (P2-5b), which stored_data_as_of checks. P4-1's optional mirror_version comes
# from the result's own column (plan P4-1), beside these: added here, it would turn every frozen row into a fallback.
DATA_AS_OF_KEYS = ("source_as_of", "published_at", "freshness", "scope", "shared")

logger = logging.getLogger(__name__)


def stored_data_as_of(record: dict) -> dict | None:
    """The data_as_of a result froze, in the DATA_AS_OF_KEYS shape the frontend's strict schema accepts.

    None when it froze nothing (every result from before P2, quietly) or a value of another shape (logged with the
    result id only), so each reader falls back to the batch: its readers here and 换一批 carrying it on (selection).
    """
    frozen = record.get("data_as_of_json")
    if isinstance(frozen, dict) and set(frozen) == set(DATA_AS_OF_KEYS):
        return {key: frozen[key] for key in DATA_AS_OF_KEYS}
    if frozen is not None:
        logger.warning("[pick] result %s froze a data_as_of of another shape; its batch's is used", record.get("id"))
    return None


class ConflictError(ValueError):
    """A replay changed its payload or a client edited an obsolete version."""


def stamp(moment: datetime | None = None) -> str:
    """A stored timestamp; the tables keep them as ISO strings and compare them as text.

    Always six fractional digits: isoformat() drops ".000000", and a linguistic collation (Supabase's
    en_US.UTF-8) orders "." before "+", so "10:00:00+00:00" would sort after "10:00:00.500000+00:00".
    """
    return (moment or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="microseconds")


def _fits(table, values: dict) -> dict:
    """SQLite ignores VARCHAR lengths and PostgreSQL fails the whole statement: refuse the same way on both."""
    for key, value in values.items():
        column = table.c.get(key)
        length = getattr(column.type, "length", None) if column is not None else None
        if length is not None and isinstance(value, str) and len(value) > length:
            raise ValueError(f"{key} 超过 {length} 个字符")
    return values


async def _live_blob_paths(session, paths: set[str]) -> set[str]:
    """Paths a batch that can still be read or published uses; failed and pruned batches hold no blob."""
    rows = await session.execute(
        select(import_batches.c.raw_blob_path).where(import_batches.c.raw_blob_path.in_(paths), import_batches.c.status.in_(("importing", "published")))
    )
    return set(rows.scalars())


class BatchRowCache:
    """Catalog rows of the most recently used batches, per process.

    A published batch's rows never change, and prune_shared evicts what it prunes. Read only after the
    owner check has passed. Callers share the cached row dicts and must not modify them.
    """

    def __init__(self, max_batches: int = 2):
        self.max_batches = max_batches
        self._rows: OrderedDict[str, tuple[dict, ...]] = OrderedDict()

    def __contains__(self, batch_id: str) -> bool:
        return batch_id in self._rows

    def get(self, batch_id: str) -> tuple[dict, ...] | None:
        rows = self._rows.get(batch_id)
        if rows is not None:
            self._rows.move_to_end(batch_id)
        return rows

    def put(self, batch_id: str, rows: tuple[dict, ...]) -> None:
        self._rows[batch_id] = rows
        self._rows.move_to_end(batch_id)
        while len(self._rows) > self.max_batches:
            self._rows.popitem(last=False)

    def evict(self, batch_ids) -> None:
        for batch_id in batch_ids:
            self._rows.pop(batch_id, None)

    def clear(self) -> None:
        self._rows.clear()


# The shared catalog is about 5.4 MB of JSON; every tool call reads it, and over the network that is slow.
CATALOG_CACHE = BatchRowCache()


class PickRepository:
    def __init__(self, session_factory: async_sessionmaker, owner_id: str, *, _shared: bool = False):
        if not isinstance(owner_id, str) or not owner_id.strip() or owner_id == "default":
            raise ValueError("authenticated owner is required")
        if owner_id == SHARED_OWNER and not _shared:
            raise ValueError("authenticated owner is required")
        self.session_factory = session_factory
        self.owner_id = owner_id

    @classmethod
    def shared(cls, session_factory: async_sessionmaker) -> "PickRepository":
        """The sync writer. Only server code constructs it; HTTP principals never map here."""
        return cls(session_factory, SHARED_OWNER, _shared=True)

    def _readable(self):
        return or_(import_batches.c.owner_id == self.owner_id, import_batches.c.owner_id == SHARED_OWNER)

    async def batches(self) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(select(import_batches).where(self._readable()).order_by(import_batches.c.created_at.desc()).limit(200))
            return [{**dict(row), "shared": row["owner_id"] == SHARED_OWNER} for row in rows.mappings()]

    async def batch_info(self, batch_id: str | None) -> dict | None:
        if not batch_id:
            return None
        async with self.session_factory() as session:
            row = (await session.execute(select(import_batches).where(import_batches.c.id == batch_id, self._readable()))).mappings().first()
            if row is None:
                return None
            return {
                "id": row["id"],
                "status": row["status"],
                "shared": row["owner_id"] == SHARED_OWNER,
                "source_as_of": row["source_as_of"],
                "published_at": row["published_at"],
                "freshness": (row["validation_json"] or {}).get("freshness"),
                "scope": (row["validation_json"] or {}).get("scope"),
                "rows": (row["validation_json"] or {}).get("rows"),
            }

    async def data_as_of(self, batch_id: str | None) -> dict | None:
        """When the data behind a result or tool answer was captured; one shape for the UI and the model."""
        info = await self.batch_info(batch_id)
        return {key: info[key] for key in DATA_AS_OF_KEYS} if info else None

    async def frozen_data_as_of(self, record: dict) -> dict | None:
        """The data_as_of a result froze when it was written (P2-5b), for every reader of that result (P2-8a).

        A batch's own values move on when a later run reuses it (U10), so the result's are read first. A result from
        before the mirror froze nothing and falls back to its batch; so does a stored value of another shape.
        """
        frozen = stored_data_as_of(record)
        return frozen if frozen is not None else await self.data_as_of(record["catalog_batch_id"])

    async def current_batch(self, kind: str) -> dict | None:
        async with self.session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(import_batches)
                        .where(
                            self._readable(),
                            import_batches.c.kind == kind,
                            import_batches.c.status == "published",
                        )
                        .order_by(import_batches.c.published_at.desc(), import_batches.c.id.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def _require_batch(self, session, batch_id: str, kind: str):
        row = (
            (
                await session.execute(
                    select(import_batches).where(
                        import_batches.c.id == batch_id,
                        self._readable(),
                        import_batches.c.kind == kind,
                        import_batches.c.status == "published",
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LookupError("资料不存在或不属于当前用户")
        return dict(row)

    async def catalog_rows(self, batch_id: str) -> list[dict]:
        """A new list of the batch's rows. The row dicts are shared with the cache: never modify them."""
        async with self.session_factory() as session:
            await self._require_batch(session, batch_id, "catalog")
            rows = CATALOG_CACHE.get(batch_id)
            if rows is None:
                result = await session.execute(
                    select(drama_versions.c.payload_json).where(drama_versions.c.batch_id == batch_id).order_by(drama_versions.c.identity)
                )
                rows = tuple(result.scalars())
                CATALOG_CACHE.put(batch_id, rows)
            return list(rows)

    async def knowledge_documents(self, batch_id: str) -> list[dict]:
        async with self.session_factory() as session:
            await self._require_batch(session, batch_id, "knowledge")
            rows = await session.execute(select(knowledge_versions).where(knowledge_versions.c.batch_id == batch_id))
            return [dict(row) for row in rows.mappings()]

    async def publish_import(
        self,
        *,
        kind: str,
        content_hash: str,
        raw_blob_path: str,
        rows: list[dict],
        source_as_of: str | None = None,
        meta: dict | None = None,
        stage: bool = False,
    ) -> dict:
        """Rows are validated by the Importer (DramaInput, strict UTF-8 knowledge); the feed's metadata is not.

        stage=True is the mirror run's v1 half (P2-5a): a new batch is written as importing, invisible to every reader
        until publish_mirror_pair or publish_agent_only flips it, and content identical to a published batch writes
        nothing now, returning what the reuse would have written as "deferred" for the publish to write.
        """
        source_as_of, meta = storable(source_as_of), storable(meta)
        batch = self._new_batch(kind, content_hash, raw_blob_path, len(rows), source_as_of=source_as_of, meta=meta, stage=stage)
        for row in rows:
            if kind == "catalog":
                _fits(drama_versions, {"identity": row["identity"]})
            elif kind == "knowledge":
                _fits(knowledge_versions, row)
        async with self.session_factory() as session:
            try:
                async with session.begin():
                    await self._insert_batch(session, batch, rows)
                return {**batch, "staged": True, "deferred": None} if stage else batch
            except IntegrityError:
                # A leftover importing batch with this content is not reused: fail_leftover_staged clears it first.
                existing = await self._published_duplicate(session, kind, content_hash)
                if existing is None:
                    raise
        return await self._reuse(existing, kind, source_as_of=source_as_of, meta=meta, stage=stage)

    async def stage_import(self, **batch) -> dict:
        """publish_import(stage=True), under the plan's name."""
        return await self.publish_import(**batch, stage=True)

    def _new_batch(self, kind: str, content_hash: str, raw_blob_path: str, rows: int, *, source_as_of, meta, stage: bool) -> dict:
        now = stamp()
        batch = dict(
            id=uuid4().hex,
            owner_id=self.owner_id,
            kind=kind,
            content_hash=content_hash,
            raw_blob_path=raw_blob_path,
            status="importing" if stage else "published",
            source_as_of=source_as_of,
            created_at=now,
            published_at=None if stage else now,
            validation_json={**(meta or {}), "rows": rows},
        )
        return _fits(import_batches, batch)

    @staticmethod
    async def _insert_batch(session, batch: dict, rows: list[dict]) -> None:
        await session.execute(insert(import_batches).values(**batch))
        if batch["kind"] == "catalog":
            if rows:
                await session.execute(insert(drama_versions), [dict(batch_id=batch["id"], identity=row["identity"], payload_json=row) for row in rows])
        elif batch["kind"] == "knowledge":
            if rows:
                await session.execute(insert(knowledge_versions), [dict(batch_id=batch["id"], **row) for row in rows])
        else:
            raise ValueError("未知资料类型")

    async def _published_duplicate(self, session, kind: str, content_hash: str) -> dict | None:
        existing = (
            (
                await session.execute(
                    select(import_batches).where(
                        import_batches.c.owner_id == self.owner_id,
                        import_batches.c.kind == kind,
                        import_batches.c.content_hash == content_hash,
                        import_batches.c.status == "published",
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(existing) if existing is not None else None

    async def _reuse(self, existing: dict, kind: str, *, source_as_of: str | None, meta: dict | None, stage: bool = False) -> dict:
        """Identical content seen again: record when, and make it current if a newer batch replaced it (A, B, then A).

        A staged run writes nothing yet: the values go back as "deferred", and only the publish that ends the run
        writes them, together with its own published_at. A run that fails leaves the batch exactly as it was.
        """
        deferred = {}
        if source_as_of is not None:
            deferred["source_as_of"] = source_as_of
        if meta is not None:
            deferred["validation_json"] = {**meta, "rows": (existing["validation_json"] or {}).get("rows")}
        if stage:
            return {**existing, "staged": False, "deferred": deferred}
        current = await self.current_batch(kind)
        values = dict(deferred)
        if current is None or current["id"] != existing["id"]:
            values["published_at"] = stamp()
        if not values:
            return existing
        async with self.session_factory() as session, session.begin():
            await session.execute(update(import_batches).where(import_batches.c.id == existing["id"]).values(**_fits(import_batches, values)))
        return {**existing, **values}

    # ---- the mirror run's publish (P2-5a; only the shared repository may call these) ----

    def _require_shared(self) -> None:
        """Shared writes go only through PickRepository.shared() (brief 0.3); a user's repository never touches the mirror."""
        if self.owner_id != SHARED_OWNER:
            raise RuntimeError("镜像发布与失败计数只能经共享仓库写入")

    async def publish_mirror_pair(
        self,
        *,
        version_id: int,
        schema_name: str,
        batches: list[dict],
        t: datetime,
        accept_empty_used: bool,
        accept_empty_seen: datetime | None,
        reader_role: str | None = None,
    ) -> dict:
        """Make the staged pair and the built version current in one transaction (plan 5.2 step 9).

        The caller has made sure its dedicated connection is idle: an uncommitted DDL there would hold the GRANT here
        until the ORM's statement timeout (brief 0.3). t is one instant: the batches store stamp(t), the version t.
        accept_empty_used and accept_empty_seen have no default: the run passes whether G9 passed on accept-empty and the
        accept_empty_set_at it read, so a one-time pass is never left unconsumed by omission.
        """
        self._require_shared()
        catalog_id, knowledge_id = pairing.pair_ids(batches)
        pairing.check_schema_name(schema_name)
        role = pairing.reader_role(reader_role)
        updates = self._batch_updates(batches, pairing.check_moment(t))
        async with self._write() as session:
            if not pairing.is_postgres(session):
                raise RuntimeError("镜像版本只在 PostgreSQL 上发布")
            granted = await pairing.grant_reader(session, schema_name, role)
            await pairing.publish_batches(session, self.owner_id, updates)
            await pairing.flip_version(session, version_id=version_id, schema_name=schema_name, t=t, catalog_id=catalog_id, knowledge_id=knowledge_id)
            await pairing.settle_control(session, accept_empty_used=accept_empty_used, accept_empty_seen=accept_empty_seen)
        return {"version": version_id, "catalog_batch_id": catalog_id, "knowledge_batch_id": knowledge_id, "published_at": stamp(t), "reader_granted": granted}

    async def publish_agent_only(self, *, batches: list[dict], reason: str, t: datetime) -> int | None:
        """Publish the staged v1 batches without a version and count the failure, in one transaction (U10, U11).

        Returns the new consecutive_failures; None on SQLite, which has no pick_mirror (U35).
        """
        self._require_shared()
        pairing.check_reason(reason)
        updates = self._batch_updates(batches, pairing.check_moment(t))
        if not updates:
            raise ValueError("降级发布至少需要一个批次")
        async with self._write() as session:
            await pairing.publish_batches(session, self.owner_id, updates)
            if not pairing.is_postgres(session):
                return None
            return await pairing.record_failure(session, reason, t)

    async def record_mirror_failure(self, *, reason: str, t: datetime) -> int | None:
        """The one writer of the failure count besides publish_agent_only (U11); None on SQLite."""
        self._require_shared()
        pairing.check_reason(reason)
        pairing.check_moment(t)
        async with self.session_factory() as session, session.begin():
            if not pairing.is_postgres(session):
                return None
            return await pairing.record_failure(session, reason, t)

    def _batch_updates(self, batches: list[dict], t: datetime) -> list[tuple[str, dict]]:
        published_at = stamp(t)
        return [(batch["id"], _fits(import_batches, pairing.batch_values(batch, published_at))) for batch in batches]

    async def fail_staged(self, batch_ids) -> list[str]:
        """This run's staged batches failed: they become failed, lose their rows and free their content-hash slot.

        Only batches of this owner still importing are touched; a reused published batch is left alone and whatever
        was deferred for it is simply dropped. Returns the raw blob paths no importing or published batch still uses,
        for the caller to delete.
        """
        ids = sorted({batch_id for batch_id in batch_ids if isinstance(batch_id, str)})
        if not ids:
            return []
        async with self.session_factory() as session, session.begin():
            doomed = (
                await session.execute(
                    select(import_batches.c.id, import_batches.c.raw_blob_path).where(
                        import_batches.c.id.in_(ids), import_batches.c.owner_id == self.owner_id, import_batches.c.status == "importing"
                    )
                )
            ).all()
            if not doomed:
                return []
            doomed_ids = [row.id for row in doomed]
            await session.execute(
                update(import_batches)
                .where(import_batches.c.id.in_(doomed_ids), import_batches.c.status == "importing")
                .values(status="failed", content_hash=literal("failed-") + import_batches.c.id)
            )
            await session.execute(delete(drama_versions).where(drama_versions.c.batch_id.in_(doomed_ids)))
            await session.execute(delete(knowledge_versions).where(knowledge_versions.c.batch_id.in_(doomed_ids)))
            paths = {row.raw_blob_path for row in doomed}
            live = await _live_blob_paths(session, paths)
        CATALOG_CACHE.evict(doomed_ids)
        return sorted(paths - live)

    async def fail_leftover_staged(self) -> list[str]:
        """Under the mirror lock: importing batches a dead run left behind (U9), handled like fail_staged."""
        async with self.session_factory() as session:
            ids = (
                await session.execute(select(import_batches.c.id).where(import_batches.c.owner_id == self.owner_id, import_batches.c.status == "importing"))
            ).scalars()
            leftovers = list(ids)
        return await self.fail_staged(leftovers)

    async def current_pin(self):
        """The (catalog, knowledge, mirror version) a run works on and its data_as_of, read in one statement (U7)."""
        # pin.py builds on stamp() and SHARED_OWNER from this module, so it is imported where it is used.
        from ggwork_pick.pin import read_pin

        return await read_pin(self.session_factory, self.owner_id)

    async def result(self, result_id: str) -> dict:
        # The model passes result ids too; one the drivers cannot send is simply not found.
        result_id = storable(result_id)
        async with self.session_factory() as session:
            row = (
                (await session.execute(select(candidate_sets).where(candidate_sets.c.id == result_id, candidate_sets.c.owner_id == self.owner_id)))
                .mappings()
                .first()
            )
            if row is None:
                raise LookupError("候选结果不存在")
            return dict(row)

    async def results(self, thread_id: str) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(candidate_sets)
                .where(candidate_sets.c.thread_id == thread_id, candidate_sets.c.owner_id == self.owner_id)
                .order_by(candidate_sets.c.created_at.desc())
                .limit(100)
            )
            return [dict(row) for row in rows.mappings()]

    async def result_for_call(self, run_id: str, call_id: str) -> dict | None:
        # Tool call ids come from the model provider; add_result stores them the same way.
        run_id, call_id = storable(run_id), storable(call_id)
        async with self.session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(candidate_sets).where(
                            candidate_sets.c.owner_id == self.owner_id, candidate_sets.c.run_id == run_id, candidate_sets.c.tool_call_id == call_id
                        )
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def add_result(self, record: dict) -> dict:
        record = _fits(candidate_sets, storable({**record, "owner_id": self.owner_id}))
        async with self.session_factory() as session:
            try:
                async with session.begin():
                    await session.execute(insert(candidate_sets).values(**record))
            except IntegrityError:
                old = await self.result_for_call(record["run_id"], record["tool_call_id"])
                if old is None:
                    raise
                if any(old[key] != record[key] for key in ("thread_id", "request_hash")):
                    raise ConflictError("重复工具调用的参数不同") from None
                return old
        return record

    async def selections(self) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(selections)
                .where(selections.c.owner_id == self.owner_id, selections.c.state == "selected")
                .order_by(selections.c.created_at.desc(), selections.c.id)
            )
            return [dict(row) for row in rows.mappings()]

    @asynccontextmanager
    async def _write(self):
        async with self.session_factory() as session, session.begin():
            if session.bind.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            elif session.bind.dialect.name == "postgresql":
                lock = int.from_bytes(hashlib.sha256(("ggwp:" + self.owner_id).encode()).digest()[:8], signed=True)
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
            yield session

    async def _command(self, session, request_id: str, payload: dict):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ValueError("保存请求ID无效")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        old = (
            (
                await session.execute(
                    select(selection_commands).where(selection_commands.c.owner_id == self.owner_id, selection_commands.c.request_id == request_id)
                )
            )
            .mappings()
            .first()
        )
        if old is not None and old["payload_hash"] != digest:
            raise ConflictError("重复请求的内容不同")
        return digest, old["receipt_json"] if old is not None else None

    async def _receipt(self, session, request_id: str, digest: str, receipt: dict):
        await session.execute(
            insert(selection_commands).values(owner_id=self.owner_id, request_id=request_id, payload_hash=digest, receipt_json=receipt, created_at=stamp())
        )

    async def command_receipt(self, request_id: str) -> dict | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(selection_commands.c.receipt_json).where(
                        selection_commands.c.owner_id == self.owner_id, selection_commands.c.request_id == request_id
                    )
                )
            ).first()
            return row[0] if row else None

    async def save_selection(self, request_id: str, result_id: str, item_ids: list[str], note: str = "") -> dict:
        if not item_ids or len(item_ids) > 20 or len(note) > 2000:
            raise ValueError("请选择1至20部候选，备注不超过2000字")
        requested = set(item_ids)
        payload = {"action": "save", "result_id": result_id, "item_ids": sorted(requested), "note": note}
        async with self._write() as session:
            digest, previous = await self._command(session, request_id, payload)
            if previous is not None:
                return previous
            result = (
                (await session.execute(select(candidate_sets).where(candidate_sets.c.id == result_id, candidate_sets.c.owner_id == self.owner_id)))
                .mappings()
                .first()
            )
            if result is None:
                raise LookupError("候选结果不存在")
            items = [item for item in result["ordered_items_json"] if item["item_id"] in requested]
            if len(items) != len(requested):
                raise ValueError("保存条目不在候选结果中")
            saved = []
            now = stamp()
            for item in items:
                old = (
                    (await session.execute(select(selections).where(selections.c.owner_id == self.owner_id, selections.c.identity == item["identity"])))
                    .mappings()
                    .first()
                )
                if old is not None and old["state"] == "selected":
                    saved.append({"id": old["id"], "identity": old["identity"], "status": "existing", "version": old["version"]})
                    continue
                values = dict(source_result_id=result_id, source_item_id=item["item_id"], snapshot_json=item, note=note, state="selected", updated_at=now)
                if old is None:
                    record_id, version = uuid4().hex, 1
                    await session.execute(
                        insert(selections).values(id=record_id, owner_id=self.owner_id, identity=item["identity"], created_at=now, version=version, **values)
                    )
                else:
                    record_id, version = old["id"], old["version"] + 1
                    await session.execute(
                        update(selections).where(selections.c.id == record_id, selections.c.owner_id == self.owner_id).values(version=version, **values)
                    )
                saved.append({"id": record_id, "identity": item["identity"], "status": "created" if old is None else "restored", "version": version})
            receipt = {"request_id": request_id, "saved": saved}
            await self._receipt(session, request_id, digest, receipt)
            return receipt

    async def update_selection(self, selection_id: str, request_id: str, expected_version: int, *, note: str | None = None, state: str | None = None):
        if (note is None and state is None) or (note is not None and len(note) > 2000) or state not in (None, "selected", "removed"):
            raise ValueError("选择更新无效")
        payload = dict(action="update", id=selection_id, expected_version=expected_version, note=note, state=state)
        async with self._write() as session:
            digest, previous = await self._command(session, request_id, payload)
            if previous is not None:
                return previous
            old = (await session.execute(select(selections).where(selections.c.id == selection_id, selections.c.owner_id == self.owner_id))).mappings().first()
            if old is None:
                raise LookupError("选剧记录不存在")
            if old["version"] != expected_version:
                raise ConflictError("记录版本已变化，请刷新后重试")
            values = dict(version=expected_version + 1, updated_at=stamp())
            if note is not None:
                values["note"] = note
            if state is not None:
                values["state"] = state
            await session.execute(update(selections).where(selections.c.id == selection_id, selections.c.owner_id == self.owner_id).values(**values))
            receipt = dict(request_id=request_id, id=selection_id, version=values["version"], state=state or old["state"])
            await self._receipt(session, request_id, digest, receipt)
            return receipt

    # ---- shared source sync bookkeeping (called with the shared repository) ----

    async def start_sync_run(self, source: str, trigger: str, stale_after_seconds: int = 900) -> dict:
        now = datetime.now(UTC)
        stale = stamp(now - timedelta(seconds=stale_after_seconds))
        record = dict(id=uuid4().hex, source=source, trigger=trigger, status="running", started_at=stamp(now))
        async with self.session_factory() as session, session.begin():
            # A run left "running" by a restarted process can never finish; close it instead of blocking forever.
            await session.execute(
                update(sync_runs)
                .where(sync_runs.c.source == source, sync_runs.c.status == "running", sync_runs.c.started_at < stale)
                .values(status="failed", finished_at=stamp(now), error="interrupted")
            )
            await session.execute(insert(sync_runs).values(**record))
        return record

    async def finish_sync_run(self, run_id: str, **values) -> dict:
        # source_as_of is the feed's capture time as sent; errors quote the failure.
        values = _fits(sync_runs, storable({**values, "finished_at": stamp()}))
        async with self.session_factory() as session, session.begin():
            await session.execute(update(sync_runs).where(sync_runs.c.id == run_id).values(**values))
            row = (await session.execute(select(sync_runs).where(sync_runs.c.id == run_id))).mappings().first()
            return dict(row)

    async def sync_runs(self, source: str = "realshort", limit: int = 10) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(sync_runs).where(sync_runs.c.source == source).order_by(sync_runs.c.started_at.desc(), sync_runs.c.id.desc()).limit(limit)
            )
            return [dict(row) for row in rows.mappings()]

    async def close_interrupted_runs(self, source: str = "realshort") -> None:
        """At startup nothing can be running in this process; a "running" row is left over from a dead one."""
        now = stamp()
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(sync_runs)
                .where(sync_runs.c.source == source, sync_runs.c.status == "running")
                .values(status="failed", finished_at=now, error="interrupted")
            )

    async def prune_shared(self, kind: str, keep: int, *, referenced_within: timedelta = RETAIN_REFERENCED) -> list[str]:
        """Drop rows of old shared batches no recent candidate snapshot uses; the batch row stays as history.

        A batch a still-published mirror version is paired with keeps its rows too (U25): after a few degraded runs
        the current version's pair can fall out of the newest `keep`. Returns the raw blob paths that no live batch
        uses any more, for the caller to delete.
        """
        cutoff = stamp(datetime.now(UTC) - referenced_within)
        column = candidate_sets.c.catalog_batch_id if kind == "catalog" else candidate_sets.c.knowledge_batch_id
        async with self.session_factory() as session, session.begin():
            batches = (
                await session.execute(
                    select(import_batches.c.id, import_batches.c.raw_blob_path)
                    .where(import_batches.c.owner_id == SHARED_OWNER, import_batches.c.kind == kind, import_batches.c.status == "published")
                    .order_by(import_batches.c.published_at.desc(), import_batches.c.id.desc())
                )
            ).all()
            referenced = set((await session.execute(select(column).where(candidate_sets.c.created_at >= cutoff).distinct())).scalars())
            referenced |= await pairing.paired_batch_ids(session, kind)
            victims = [batch for batch in batches[keep:] if batch.id not in referenced]
            if not victims:
                return []
            table = drama_versions if kind == "catalog" else knowledge_versions
            await session.execute(delete(table).where(table.c.batch_id.in_([batch.id for batch in victims])))
            for batch in victims:
                # Free the content-hash slot so identical content can be published again later.
                await session.execute(update(import_batches).where(import_batches.c.id == batch.id).values(status="pruned", content_hash="pruned-" + batch.id))
            paths = {batch.raw_blob_path for batch in victims}
            live = await _live_blob_paths(session, paths)
        # After the commit: a reader that passed the owner check before it may still put the rows back, but a
        # pruned batch never passes that check again, and the cache holds two batches at most.
        CATALOG_CACHE.evict(batch.id for batch in victims)
        return sorted(paths - live)

    # ---- answer-check notes (per user) ----

    async def record_answer_check(self, *, thread_id: str, run_id: str, message_id: str | None, notes: list[str]) -> None:
        # Notes quote titles from the model's answer, and the message id comes from the model provider.
        row = _fits(
            answer_checks,
            storable(
                dict(
                    id=uuid4().hex,
                    owner_id=self.owner_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    message_id=message_id,
                    notes_json=list(notes),
                    created_at=stamp(),
                )
            ),
        )
        async with self.session_factory() as session:
            try:
                async with session.begin():
                    await session.execute(insert(answer_checks).values(**row))
            except IntegrityError:
                if row["message_id"] is None or not await self._answer_checked(row["message_id"]):
                    raise
                # a retried model call already recorded this answer

    async def _answer_checked(self, message_id: str) -> bool:
        async with self.session_factory() as session:
            found = await session.execute(select(answer_checks.c.id).where(answer_checks.c.owner_id == self.owner_id, answer_checks.c.message_id == message_id))
            return found.first() is not None

    async def answer_checks(self, thread_id: str) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(answer_checks)
                .where(answer_checks.c.owner_id == self.owner_id, answer_checks.c.thread_id == thread_id)
                .order_by(answer_checks.c.created_at)
                .limit(500)
            )
            return [
                {"message_id": row["message_id"], "run_id": row["run_id"], "notes": row["notes_json"], "created_at": row["created_at"]}
                for row in rows.mappings()
            ]
