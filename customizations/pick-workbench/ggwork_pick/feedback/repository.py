"""Owner-checked immutable versions and cross-process fenced feedback publication."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from ggwork_pick.contracts import unstorable_path
from ggwork_pick.feedback.contracts import FeedbackReply, FeedbackSnapshot
from ggwork_pick.models import candidate_sets
from ggwork_pick.models import feedback_identity_links as identity_links
from ggwork_pick.models import feedback_records as records
from ggwork_pick.models import feedback_result_evidence as result_evidence
from ggwork_pick.models import feedback_runs as runs
from ggwork_pick.models import feedback_scopes as scopes
from ggwork_pick.models import feedback_versions as versions
from ggwork_pick.repository import stamp


def _prepare_publication(snapshot: FeedbackSnapshot) -> tuple[FeedbackSnapshot, str, dict]:
    # Revalidate even instances and detach caller-owned values before hashing/storing.
    snapshot = FeedbackSnapshot.model_validate(snapshot.model_dump(mode="json"))
    digest = snapshot.content_hash()
    manifest = snapshot.model_dump(mode="json", exclude={"tables": {"__all__": {"records"}}})
    return snapshot, digest, manifest


def _reconstruct_snapshot(manifest: dict, stored) -> FeedbackSnapshot:
    grouped = {}
    for row in stored:
        grouped.setdefault(row["table_id"], []).append({"record_id": row["record_id"], "values": row["values_json"]})
    return FeedbackSnapshot.model_validate({**manifest, "tables": [{**table, "records": grouped.get(table["table_id"], [])} for table in manifest["tables"]]})


class LostFeedbackLease(ValueError):
    """This worker no longer owns the source scope; none of its writes may publish."""


class FeedbackRepository:
    def __init__(self, session_factory, owner_id: str):
        if (
            not isinstance(owner_id, str)
            or not owner_id.strip()
            or owner_id in ("default", "system:shared")
            or len(owner_id) > 128
            or unstorable_path(owner_id)
        ):
            raise ValueError("反馈读取缺少有效用户身份")
        self.factory = session_factory
        self.owner_id = owner_id

    async def claim(self, trigger: str, *, now: datetime | None = None, lease_seconds: int = 600) -> dict | None:
        if trigger not in ("manual", "scheduled", "query") or not 1 <= lease_seconds <= 3600:
            raise ValueError("无效同步触发参数")
        now = now or datetime.now(UTC)
        run_id = "fr_" + uuid4().hex
        row = dict(id=run_id, owner_id=self.owner_id, trigger=trigger, status="running", started_at=stamp(now))
        async with self.factory.begin() as session:
            # Savepoint handles first-use races without aborting the surrounding PostgreSQL transaction.
            try:
                async with session.begin_nested():
                    await session.execute(insert(scopes).values(owner_id=self.owner_id))
            except IntegrityError:
                pass
            claimed = await session.execute(
                update(scopes)
                .where(
                    scopes.c.owner_id == self.owner_id,
                    or_(scopes.c.lease_token.is_(None), scopes.c.lease_until <= stamp(now)),
                )
                .values(lease_token=run_id, lease_until=stamp(now + timedelta(seconds=lease_seconds)))
            )
            if claimed.rowcount != 1:
                return None
            await session.execute(
                update(runs)
                .where(runs.c.owner_id == self.owner_id, runs.c.status == "running")
                .values(status="failed", finished_at=stamp(now), error_code="lease_expired")
            )
            await session.execute(insert(runs).values(**row))
        return row

    async def current(self) -> dict | None:
        async with self.factory() as session:
            result = await session.execute(
                select(versions)
                .join(scopes, scopes.c.current_version_id == versions.c.id)
                .where(scopes.c.owner_id == self.owner_id, versions.c.owner_id == self.owner_id)
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def version(self, version_id: str) -> dict:
        async with self.factory() as session:
            row = (await session.execute(select(versions).where(versions.c.id == version_id, versions.c.owner_id == self.owner_id))).mappings().first()
            if row is None:
                raise LookupError("反馈版本不可用")
            return dict(row)

    async def run(self, run_id: str) -> dict:
        async with self.factory() as session:
            row = (await session.execute(select(runs).where(runs.c.id == run_id, runs.c.owner_id == self.owner_id))).mappings().first()
            if row is None:
                raise LookupError("反馈同步任务不可用")
            return dict(row)

    async def confirmed_links(self) -> list[dict]:
        async with self.factory() as session:
            result = await session.execute(select(identity_links).where(identity_links.c.owner_id == self.owner_id))
            return [dict(row) for row in result.mappings()]

    async def result_evidence(self, result_id: str) -> FeedbackReply | None:
        async with self.factory() as session:
            owned = await session.scalar(select(candidate_sets.c.id).where(candidate_sets.c.id == result_id, candidate_sets.c.owner_id == self.owner_id))
            if owned is None:
                raise LookupError("候选结果不可用")
            evidence = await session.scalar(
                select(result_evidence.c.evidence_json).where(result_evidence.c.result_id == result_id, result_evidence.c.owner_id == self.owner_id)
            )
            return FeedbackReply.model_validate(evidence) if evidence is not None else None

    async def freeze_result(self, result_id: str, reply: FeedbackReply, *, session=None) -> FeedbackReply:
        """Validate and freeze evidence, optionally inside the candidate publication transaction."""
        reply = FeedbackReply.model_validate(reply.model_dump(mode="json"))
        if reply.status != "ok":
            raise ValueError("不能将未完成反馈保存为候选依据")
        if session is None:
            async with self.factory.begin() as session:
                return await self.freeze_result(result_id, reply, session=session)
        version = await session.scalar(select(versions.c.id).where(versions.c.id == reply.feedback_version_id, versions.c.owner_id == self.owner_id))
        if version is None:
            raise LookupError("反馈版本不可用")
        candidate = (
            (await session.execute(select(candidate_sets).where(candidate_sets.c.id == result_id, candidate_sets.c.owner_id == self.owner_id)))
            .mappings()
            .first()
        )
        if candidate is None:
            raise LookupError("候选结果不可用")
        allowed = {item["identity"] for item in candidate["ordered_items_json"]}
        if any(item.key not in allowed for item in reply.items):
            raise ValueError("反馈条目不属于候选结果")
        if session.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as insert_once
        else:
            from sqlalchemy.dialects.sqlite import insert as insert_once
        await session.execute(
            insert_once(result_evidence)
            .values(
                result_id=result_id,
                owner_id=self.owner_id,
                version_id=reply.feedback_version_id,
                evidence_json=reply.model_dump(mode="json"),
                created_at=stamp(),
            )
            .on_conflict_do_nothing(index_elements=["result_id"])
        )
        frozen = await session.scalar(
            select(result_evidence.c.evidence_json).where(result_evidence.c.result_id == result_id, result_evidence.c.owner_id == self.owner_id)
        )
        if frozen is None:
            raise LookupError("候选反馈不可用")
        return FeedbackReply.model_validate(frozen)

    async def snapshot(self, version_id: str) -> FeedbackSnapshot:
        version = await self.version(version_id)
        manifest = version["manifest_json"]
        async with self.factory() as session:
            stored = (await session.execute(select(records).where(records.c.version_id == version_id))).mappings().all()
        return await asyncio.to_thread(_reconstruct_snapshot, manifest, stored)

    async def publish(self, run_id: str, snapshot: FeedbackSnapshot, *, now: datetime | None = None) -> dict:
        # The worker only prepares values: cancellation cannot leave a background publisher.
        snapshot, digest, manifest = await asyncio.to_thread(_prepare_publication, snapshot)
        now = now or datetime.now(UTC)
        async with self.factory.begin() as session:
            fenced = await session.execute(
                update(scopes)
                .where(
                    scopes.c.owner_id == self.owner_id,
                    scopes.c.lease_token == run_id,
                    scopes.c.lease_until > stamp(now),
                )
                .values(last_verified_at=stamp(now))
            )
            if fenced.rowcount != 1:
                raise LostFeedbackLease("同步租约已失效")
            existing = (
                (await session.execute(select(versions).where(versions.c.owner_id == self.owner_id, versions.c.content_hash == digest))).mappings().first()
            )
            if existing:
                version = dict(existing)
            else:
                version_id = "fv_" + uuid4().hex
                version = dict(
                    id=version_id,
                    owner_id=self.owner_id,
                    content_hash=digest,
                    scan_started_at=stamp(snapshot.scan_started_at),
                    scan_completed_at=stamp(snapshot.scan_completed_at),
                    published_at=stamp(now),
                    manifest_json=manifest,
                )
                await session.execute(insert(versions).values(**version))
                for table in snapshot.tables:
                    for start in range(0, len(table.records), 200):
                        await session.execute(
                            insert(records),
                            [
                                dict(version_id=version_id, table_id=table.table_id, record_id=record.record_id, values_json=record.values)
                                for record in table.records[start : start + 200]
                            ],
                        )
            await session.execute(
                update(scopes)
                .where(scopes.c.owner_id == self.owner_id, scopes.c.lease_token == run_id)
                .values(current_version_id=version["id"], lease_token=None, lease_until=None)
            )
            await session.execute(
                update(runs)
                .where(runs.c.id == run_id, runs.c.owner_id == self.owner_id, runs.c.status == "running")
                .values(status="success", finished_at=stamp(now), version_id=version["id"])
            )
        return version

    async def fail(self, run_id: str, error_code: str, *, now: datetime | None = None):
        allowed = {"auth_required", "refresh_failed", "schema_changed", "source_changed", "incomplete", "unavailable", "cancelled", "capacity"}
        if error_code not in allowed:
            error_code = "refresh_failed"
        async with self.factory.begin() as session:
            # Scope first, like claim/publish: reverse order deadlocks expired-worker cleanup with takeover.
            await session.execute(
                update(scopes).where(scopes.c.owner_id == self.owner_id, scopes.c.lease_token == run_id).values(lease_token=None, lease_until=None)
            )
            await session.execute(
                update(runs)
                .where(runs.c.id == run_id, runs.c.owner_id == self.owner_id, runs.c.status == "running")
                .values(status="failed", finished_at=stamp(now), error_code=error_code)
            )

    async def status(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        async with self.factory() as session:
            scope = (await session.execute(select(scopes).where(scopes.c.owner_id == self.owner_id))).mappings().first()
            last = (
                (await session.execute(select(runs).where(runs.c.owner_id == self.owner_id).order_by(runs.c.started_at.desc(), runs.c.id.desc()).limit(1)))
                .mappings()
                .first()
            )
            running = None
            expired = bool(scope and scope["lease_token"] and (scope["lease_until"] is None or scope["lease_until"] <= stamp(now)))
            if scope and scope["lease_token"] and not expired:
                row = (await session.execute(select(runs).where(runs.c.id == scope["lease_token"], runs.c.owner_id == self.owner_id))).mappings().first()
                running = dict(row) if row and row["status"] == "running" else None
        return {
            "current": await self.current(),
            "last_verified_at": scope["last_verified_at"] if scope else None,
            "running": running,
            "lease_expired": expired,
            "last_run": dict(last) if last else None,
        }
