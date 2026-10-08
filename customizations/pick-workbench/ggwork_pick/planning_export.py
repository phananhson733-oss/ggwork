"""User-confirmed execution previews; no publication or model write authority."""

import asyncio
import csv
import hashlib
import io
from uuid import uuid4

from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from ggwork_pick.completion_contracts import PlanExport, PlanPreview
from ggwork_pick.models import content_plan_exports, content_plan_previews
from ggwork_pick.pin import pin_from_row, pin_statement
from ggwork_pick.planning import PlanConflict, PlanningService
from ggwork_pick.planning_sources import current_facts
from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.repository import stamp


class PlanningExportService:
    def __init__(self, repository, query_service):
        self.repository, self.query = repository, query_service
        self.plans = PlanningService(repository)
        self.owner = repository.owner_id

    async def _load(self, session, plan_id, version):
        plan = await self.plans._view(session, plan_id)
        if plan["version"] != version:
            raise PlanConflict(plan["version"])
        return plan

    async def _checks(self, plan, deadline):
        pin, facts, failure = None, {}, None
        try:
            async with asyncio.timeout_at(deadline):
                pin, facts = await current_facts(self.query, {r["identity"] for r in plan["rows"]}, deadline=deadline)
        except (QueryFailure, TimeoutError, LookupError):
            failure = "当前必要来源不可完整读取，请稍后重新预览"
        checks = []
        for row in plan["rows"]:
            blockers = []
            for field, label in (("account", "账号"), ("channel", "渠道"), ("scheduled_at", "有效排期时间")):
                if not row[field] or not row[field].strip():
                    blockers.append(f"缺少{label}")
            fact = facts.get(row["identity"])
            if failure:
                blockers.append(failure)
            elif fact is None:
                blockers.append("当前来源缺少该完整身份，无法确认执行依据")
            else:
                if fact.availability != "active":
                    blockers.append("当前剧目已下架" if fact.availability == "delisted" else "当前在架状态未知")
                if row["channel"] and fact.channel_rules.get(row["channel"]) != "allowed":
                    blockers.append("当前渠道不允许发布" if fact.channel_rules.get(row["channel"]) == "denied" else "当前渠道发布权限未知")
            checks.append(
                {
                    "row_id": row["row_id"],
                    "status": "blocked" if blockers else "ready",
                    "blockers": blockers,
                    "warnings": [],
                    "current_pin": pin.model_dump(mode="json") if pin else None,
                }
            )
        return pin, checks

    async def _pin_guard(self, session, pin, version, deadline):
        if pin is None:
            return
        if session.bind.dialect.name == "postgresql":
            # A brief commit fence: no source publication/prune may slip between
            # the final current-pin read and the immutable private receipt commit.
            remaining_ms = max(1, min(2000, int((deadline - asyncio.get_running_loop().time()) * 1000)))
            await session.execute(text(f"SET LOCAL lock_timeout = {remaining_ms}"))
            await session.execute(text("LOCK TABLE ggwp_import_batches, pick_mirror.versions IN SHARE MODE"))
        current = pin_from_row((await session.execute(pin_statement(self.owner, session.bind.dialect.name))).one())
        if (current.catalog_id, current.knowledge_id, current.mirror_version) != (pin.catalog_batch_id, pin.knowledge_batch_id, pin.mirror_version):
            raise PlanConflict(version)

    async def _bounded(self, operation, plan_id, body):
        deadline = asyncio.get_running_loop().time() + 10
        try:
            async with asyncio.timeout_at(deadline):
                return await operation(plan_id, body, deadline)
        except TimeoutError:
            raise QueryFailure("query_timeout", "执行依据核对超时，请保留草稿并重试", retryable=True) from None
        except DBAPIError:
            raise QueryFailure("source_unavailable", "执行依据或保存暂不可用，请保留草稿并重试", retryable=True) from None

    async def preview(self, plan_id, body):
        return await self._bounded(self._preview, plan_id, body)

    async def _preview(self, plan_id, body, deadline):
        payload = {"operation": "preview", "plan_id": plan_id, "body": body.model_dump(mode="json")}
        async with self.repository._write() as session:
            digest, previous = await self.plans._command(session, body.request_id, payload)
            if previous is not None:
                return previous
            plan = await self._load(session, plan_id, body.expected_version)
        pin, checks = await self._checks(plan, deadline)
        result = PlanPreview(
            plan=plan, preview_id=uuid4().hex, checked_at=stamp(), exportable=bool(checks) and all(c["status"] == "ready" for c in checks), checks=checks
        ).model_dump(mode="json")
        async with self.repository._write() as session:
            digest, previous = await self.plans._command(session, body.request_id, payload)
            if previous is not None:
                return previous
            await self._load(session, plan_id, body.expected_version)
            await self._pin_guard(session, pin, body.expected_version, deadline)
            await session.execute(
                insert(content_plan_previews).values(
                    id=result["preview_id"],
                    owner_id=self.owner,
                    plan_id=plan_id,
                    plan_version=body.expected_version,
                    receipt_json=result,
                    created_at=result["checked_at"],
                )
            )
            return await self.plans._receipt(session, body.request_id, digest, result)

    async def export(self, plan_id, body):
        return await self._bounded(self._export, plan_id, body)

    async def _export(self, plan_id, body, deadline):
        payload = {"operation": "export", "plan_id": plan_id, "body": body.model_dump(mode="json")}
        async with self.repository._write() as session:
            digest, previous = await self.plans._command(session, body.request_id, payload)
            if previous is not None:
                return previous
            plan = await self._load(session, plan_id, body.expected_version)
            preview = (
                await session.execute(
                    select(content_plan_previews.c.receipt_json).where(
                        content_plan_previews.c.id == body.preview_id,
                        content_plan_previews.c.owner_id == self.owner,
                        content_plan_previews.c.plan_id == plan_id,
                    )
                )
            ).scalar_one_or_none()
            if preview is None:
                raise LookupError("预览不存在")
            if preview["plan"]["version"] != body.expected_version:
                raise PlanConflict(plan["version"])
            if not preview["exportable"]:
                raise QueryFailure("export_blocked", "必要执行依据不完整，请保留草稿并重新预览")
        pin, checks = await self._checks(plan, deadline)
        if not checks or any(c["status"] != "ready" for c in checks):
            raise QueryFailure("export_blocked", "当前必要执行依据不完整或已变化，请保留草稿并重新预览")
        if any(check["current_pin"] != pin.model_dump(mode="json") for check in preview["checks"]):
            raise PlanConflict(plan["version"])
        data = execution_csv(plan)
        export_id = uuid4().hex
        result = PlanExport(
            id=export_id,
            plan_id=plan_id,
            plan_version=body.expected_version,
            preview_id=body.preview_id,
            created_at=stamp(),
            filename=f"plan-{plan_id}-v{body.expected_version}.csv",
            row_count=len(plan["rows"]),
            sha256=hashlib.sha256(data).hexdigest(),
        ).model_dump(mode="json")
        async with self.repository._write() as session:
            digest, previous = await self.plans._command(session, body.request_id, payload)
            if previous is not None:
                return previous
            await self._load(session, plan_id, body.expected_version)
            await self._pin_guard(session, pin, body.expected_version, deadline)
            await session.execute(
                insert(content_plan_exports).values(
                    id=export_id,
                    owner_id=self.owner,
                    plan_id=plan_id,
                    plan_version=body.expected_version,
                    preview_id=body.preview_id,
                    receipt_json=result,
                    csv_bytes=data,
                    created_at=result["created_at"],
                )
            )
            return await self.plans._receipt(session, body.request_id, digest, result)

    async def download(self, export_id):
        async with self.repository.session_factory() as session:
            saved = (
                await session.execute(
                    select(content_plan_exports.c.receipt_json, content_plan_exports.c.csv_bytes).where(
                        content_plan_exports.c.id == export_id, content_plan_exports.c.owner_id == self.owner
                    )
                )
            ).first()
            if saved is None:
                raise LookupError("导出文件不存在")
            return saved.receipt_json, saved.csv_bytes


COLUMNS = (
    "plan_id",
    "plan_version",
    "row_id",
    "identity",
    "source_result_id",
    "source_item_id",
    "title",
    "theater",
    "language",
    "account",
    "channel",
    "local_time",
    "timezone",
    "scheduled_at",
    "copy_text",
    "note",
)


def execution_csv(plan):
    def cell(value):
        text = str(value) if value is not None else ""
        if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + text
        return text

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in plan["rows"]:
        values = {**row, "plan_id": plan["id"], "plan_version": plan["version"], "timezone": plan["timezone"]}
        writer.writerow([cell(values[key]) for key in COLUMNS])
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
