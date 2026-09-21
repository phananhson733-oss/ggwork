"""All business reads and writes are scoped to a trusted authenticated owner."""

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.models import candidate_sets, drama_versions, import_batches, knowledge_versions, selection_commands, selections


class ConflictError(ValueError):
    """A replay changed its payload or a client edited an obsolete version."""


class PickRepository:
    def __init__(self, session_factory: async_sessionmaker, owner_id: str):
        if not isinstance(owner_id, str) or not owner_id.strip() or owner_id == "default":
            raise ValueError("authenticated owner is required")
        self.session_factory = session_factory
        self.owner_id = owner_id

    async def batches(self) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(select(import_batches).where(import_batches.c.owner_id == self.owner_id).order_by(import_batches.c.created_at.desc()))
            return [dict(row) for row in rows.mappings()]

    async def current_batch(self, kind: str) -> dict | None:
        async with self.session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(import_batches)
                        .where(
                            import_batches.c.owner_id == self.owner_id,
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
                        import_batches.c.owner_id == self.owner_id,
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
        async with self.session_factory() as session:
            await self._require_batch(session, batch_id, "catalog")
            rows = await session.execute(select(drama_versions.c.payload_json).where(drama_versions.c.batch_id == batch_id).order_by(drama_versions.c.identity))
            return list(rows.scalars())

    async def knowledge_documents(self, batch_id: str) -> list[dict]:
        async with self.session_factory() as session:
            await self._require_batch(session, batch_id, "knowledge")
            rows = await session.execute(select(knowledge_versions).where(knowledge_versions.c.batch_id == batch_id))
            return [dict(row) for row in rows.mappings()]

    async def publish_import(self, *, kind: str, content_hash: str, raw_blob_path: str, rows: list[dict], source_as_of: str | None = None) -> dict:
        batch_id = uuid4().hex
        now = datetime.now(UTC).isoformat()
        batch = dict(
            id=batch_id,
            owner_id=self.owner_id,
            kind=kind,
            content_hash=content_hash,
            raw_blob_path=raw_blob_path,
            status="published",
            source_as_of=source_as_of,
            created_at=now,
            published_at=now,
            validation_json={"rows": len(rows)},
        )
        async with self.session_factory() as session:
            try:
                async with session.begin():
                    await session.execute(insert(import_batches).values(**batch))
                    if kind == "catalog":
                        if rows:
                            await session.execute(insert(drama_versions), [dict(batch_id=batch_id, identity=row["identity"], payload_json=row) for row in rows])
                    elif kind == "knowledge":
                        if rows:
                            await session.execute(insert(knowledge_versions), [dict(batch_id=batch_id, **row) for row in rows])
                    else:
                        raise ValueError("未知资料类型")
            except IntegrityError:
                row = (
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
                if row is None:
                    raise
                return dict(row)
        return batch

    async def result(self, result_id: str) -> dict:
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
        record = {**record, "owner_id": self.owner_id}
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
            insert(selection_commands).values(
                owner_id=self.owner_id, request_id=request_id, payload_hash=digest, receipt_json=receipt, created_at=datetime.now(UTC).isoformat()
            )
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
            now = datetime.now(UTC).isoformat()
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
            values = dict(version=expected_version + 1, updated_at=datetime.now(UTC).isoformat())
            if note is not None:
                values["note"] = note
            if state is not None:
                values["state"] = state
            await session.execute(update(selections).where(selections.c.id == selection_id, selections.c.owner_id == self.owner_id).values(**values))
            receipt = dict(request_id=request_id, id=selection_id, version=values["version"], state=state or old["state"])
            await self._receipt(session, request_id, digest, receipt)
            return receipt
