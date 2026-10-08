"""User-confirmed plan drafts. Saved source evidence never turns into model write authority."""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, insert, select, true, update

from ggwork_pick.completion_contracts import Plan, PlanRow, QueryPin
from ggwork_pick.models import candidate_sets, content_plan_commands, content_plan_rows, content_plans, feedback_result_evidence, selections
from ggwork_pick.planning_time import plan_zone, scheduled_instant
from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.repository import _fits, stamp

EDITABLE = ("account", "channel", "local_time", "fold", "copy_text", "note")
SOURCE = ("identity", "source_result_id", "source_item_id", "selection_id")


class PlanConflict(QueryFailure):
    def __init__(self, current_version):
        super().__init__("version_conflict", "计划版本已变化，请读取当前版本后重新确认")
        self.current_version = current_version


class PlanningService:
    def __init__(self, repository):
        self.repository = repository
        self.owner = repository.owner_id

    async def _page(self, session, *, plan_id=None, offset=0, limit=20):
        where = content_plans.c.owner_id == self.owner
        if plan_id is not None:
            where = where & (content_plans.c.id == plan_id)
        counted = select(func.count().label("total")).select_from(content_plans).where(where).subquery()
        page = select(content_plans).where(where).order_by(content_plans.c.updated_at.desc(), content_plans.c.id).offset(offset).limit(limit).subquery()
        rows = content_plan_rows.c
        query = (
            select(counted.c.total, *page.c, rows.row_id, rows.source_json, rows.editable_json, rows.scheduled_at)
            .select_from(
                counted.outerjoin(page, true()).outerjoin(
                    content_plan_rows, (rows.plan_id == page.c.id) & (rows.owner_id == self.owner) & rows.position.is_not(None)
                )
            )
            .order_by(page.c.updated_at.desc(), page.c.id, rows.position, rows.row_id)
        )
        data = (await session.execute(query)).mappings().all()
        plans = {}
        for row in data:
            if row["id"] is None:
                continue
            plan = plans.setdefault(row["id"], {**{key: row[key] for key in ("id", "version", "title", "timezone", "created_at", "updated_at")}, "rows": []})
            if row["row_id"] is not None:
                plan["rows"].append(PlanRow(row_id=row["row_id"], **row["source_json"], **row["editable_json"], scheduled_at=row["scheduled_at"]))
        items = [Plan.model_validate(plan).model_dump(mode="json") for plan in plans.values()]
        total = data[0]["total"]
        return {"items": items, "total": total, "next_offset": offset + len(items) if offset + len(items) < total else None}

    async def _view(self, session, plan_id):
        page = await self._page(session, plan_id=plan_id, limit=1)
        if not page["items"]:
            raise LookupError("排期草稿不存在")
        return page["items"][0]

    async def get(self, plan_id):
        async with self.repository.session_factory() as session:
            return await self._view(session, plan_id)

    async def list(self, offset=0, limit=20):
        async with self.repository.session_factory() as session:
            return await self._page(session, offset=offset, limit=limit)

    async def _sources(self, session, rows):
        if not rows:
            return {}
        result_ids = {row.source_result_id for row in rows}
        results = {
            r["id"]: r
            for r in (
                await session.execute(select(candidate_sets).where(candidate_sets.c.owner_id == self.owner, candidate_sets.c.id.in_(result_ids)))
            ).mappings()
        }
        selection_ids = {row.selection_id for row in rows if row.selection_id is not None}
        saved = (
            {
                r["id"]: r
                for r in (await session.execute(select(selections).where(selections.c.owner_id == self.owner, selections.c.id.in_(selection_ids)))).mappings()
            }
            if selection_ids
            else {}
        )
        feedback = {
            r["result_id"]: r["version_id"]
            for r in (
                await session.execute(
                    select(feedback_result_evidence.c.result_id, feedback_result_evidence.c.version_id).where(
                        feedback_result_evidence.c.owner_id == self.owner, feedback_result_evidence.c.result_id.in_(result_ids)
                    )
                )
            ).mappings()
        }
        return {
            row.row_id: self._source(row, results.get(row.source_result_id), saved.get(row.selection_id), feedback.get(row.source_result_id)) for row in rows
        }

    def _source(self, row, result, saved, feedback_version):
        if result is None:
            raise LookupError("来源候选不存在")
        item = next((item for item in result["ordered_items_json"] if item["item_id"] == row.source_item_id), None)
        if item is None:
            raise LookupError("来源条目不存在")
        if item["identity"] != row.identity:
            raise QueryFailure("invalid_query", "计划行身份与来源候选不一致")
        if row.selection_id is not None:
            if saved is None:
                raise LookupError("来源个人选剧不存在")
            if any(saved[key] != getattr(row, key) for key in ("identity", "source_result_id", "source_item_id")):
                raise QueryFailure("invalid_query", "个人选剧与候选来源不一致")
        version = result.get("mirror_version")
        pin = QueryPin(
            catalog_batch_id=result["catalog_batch_id"],
            knowledge_batch_id=result["knowledge_batch_id"],
            mirror_version=version,
            rule_version=f"mirror-rules-v{version}" if version is not None else result["rule_version"],
            feedback_version_id=feedback_version,
        )
        return {
            **{key: getattr(row, key) for key in SOURCE},
            **{key: item[key] for key in ("title", "theater", "language")},
            "source_pin": pin.model_dump(mode="json"),
        }

    async def _command(self, session, request_id, payload):
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        old = (
            (
                await session.execute(
                    select(content_plan_commands).where(content_plan_commands.c.owner_id == self.owner, content_plan_commands.c.request_id == request_id)
                )
            )
            .mappings()
            .first()
        )
        if old is not None and old["payload_hash"] != digest:
            raise QueryFailure("version_conflict", "重复请求的内容不同")
        return digest, old["receipt_json"] if old is not None else None

    async def _receipt(self, session, request_id, digest, plan):
        await session.execute(
            insert(content_plan_commands).values(
                **_fits(content_plan_commands, dict(owner_id=self.owner, request_id=request_id, payload_hash=digest, receipt_json=plan, created_at=stamp()))
            )
        )
        return plan

    async def create(self, body):
        plan_id = uuid4().hex
        async with self.repository._write() as session:
            digest, previous = await self._command(session, body.request_id, {"operation": "create", "body": body.model_dump(mode="json")})
            if previous is not None:
                return previous
            zone = plan_zone(body.timezone)
            now = stamp()
            await session.execute(
                insert(content_plans).values(
                    **_fits(
                        content_plans,
                        dict(id=plan_id, owner_id=self.owner, version=1, title=body.title, timezone=body.timezone, created_at=now, updated_at=now),
                    )
                )
            )
            sources = await self._sources(session, body.rows)
            inserts = []
            for position, row in enumerate(body.rows):
                source = sources[row.row_id]
                inserts.append(
                    dict(
                        plan_id=plan_id,
                        row_id=row.row_id,
                        owner_id=self.owner,
                        position=position,
                        source_json=source,
                        editable_json={key: getattr(row, key) for key in EDITABLE},
                        scheduled_at=scheduled_instant(row, zone, position),
                    )
                )
            if inserts:
                await session.execute(insert(content_plan_rows), inserts)
            return await self._receipt(session, body.request_id, digest, await self._view(session, plan_id))

    async def update(self, plan_id, body):
        async with self.repository._write() as session:
            digest, previous = await self._command(session, body.request_id, {"operation": "update", "plan_id": plan_id, "body": body.model_dump(mode="json")})
            if previous is not None:
                return previous
            current = await self._view(session, plan_id)
            if current["version"] != body.expected_version:
                raise PlanConflict(current["version"])
            zone = plan_zone(body.timezone)
            if body.timezone != current["timezone"] and body.timezone_change is None:
                raise QueryFailure("invalid_query", "更改计划时区需要明确选择保留当地时间或保留同一时刻")
            saved = (
                (await session.execute(select(content_plan_rows).where(content_plan_rows.c.plan_id == plan_id, content_plan_rows.c.owner_id == self.owner)))
                .mappings()
                .all()
            )
            bindings = {row["row_id"]: row for row in saved}
            sources = await self._sources(session, [row for row in body.rows if row.row_id not in bindings])
            await session.execute(
                update(content_plan_rows).where(content_plan_rows.c.plan_id == plan_id, content_plan_rows.c.owner_id == self.owner).values(position=None)
            )
            for position, row in enumerate(body.rows):
                old = bindings.get(row.row_id)
                editable = {key: getattr(row, key) for key in EDITABLE}
                if old is not None:
                    if any(old["source_json"][key] != getattr(row, key) for key in SOURCE):
                        raise QueryFailure("invalid_query", "已有计划行的来源不可更换，请为新的来源使用新行标识")
                    await session.execute(
                        update(content_plan_rows)
                        .where(content_plan_rows.c.plan_id == plan_id, content_plan_rows.c.row_id == row.row_id, content_plan_rows.c.owner_id == self.owner)
                        .values(position=position, editable_json=editable, scheduled_at=scheduled_instant(row, zone, position))
                    )
                else:
                    source = sources[row.row_id]
                    await session.execute(
                        insert(content_plan_rows).values(
                            plan_id=plan_id,
                            row_id=row.row_id,
                            owner_id=self.owner,
                            position=position,
                            source_json=source,
                            editable_json=editable,
                            scheduled_at=scheduled_instant(row, zone, position),
                        )
                    )
            await session.execute(
                update(content_plans)
                .where(content_plans.c.id == plan_id, content_plans.c.owner_id == self.owner, content_plans.c.version == body.expected_version)
                .values(**_fits(content_plans, dict(title=body.title, timezone=body.timezone, version=body.expected_version + 1, updated_at=stamp())))
            )
            return await self._receipt(session, body.request_id, digest, await self._view(session, plan_id))
