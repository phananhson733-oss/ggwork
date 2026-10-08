"""Run-scoped feedback pins and immutable evidence beside existing candidate snapshots."""

import asyncio
from dataclasses import dataclass

from ggwork_pick.feedback.analytics import drama_feedback
from ggwork_pick.feedback.contracts import FeedbackReply
from ggwork_pick.feedback.repository import FeedbackRepository
from ggwork_pick.repository import stamp

NOTICES = {
    "refresh_pending": "运营反馈正在刷新，尚未生成候选。刷新完成后可用 feedback_refresh_id 继续同一次请求，不要反复创建新刷新。",
    "auth_required": "读取运营反馈需要飞书用户授权，请在能力中心的飞书插件完成授权后重试。",
    "schema_changed": "飞书反馈字段发生变化，需要核验字段映射；未使用旧数据冒充最新反馈。",
    "unavailable": "当前无法读取运营反馈，未将历史数据作为最新依据。",
    "refresh_failed": "运营反馈刷新未成功，请检查反馈同步状态；未将历史数据作为最新依据。",
}


@dataclass(frozen=True)
class FeedbackPin:
    version_id: str
    verified_at: str | None
    scan_started_at: str | None
    freshness: str


async def prepare_feedback(task, *, parent=None, resume_run_id=None):
    service = getattr(task.service, "feedback", None)
    if service is None or not service.enabled or service.owner_id != task.owner_id:
        return None, None
    repo = service.repository(task.owner_id)
    if parent is not None:
        frozen = await repo.result_evidence(parent["id"])
        return pin_historical_feedback(task, frozen)
    if task.feedback_checked:
        return task.feedback_pin, task.feedback_failure
    outcome = await service.refresh(task.owner_id, wait_seconds=max(0, min(20, task.remaining() - 1)), resume_run_id=resume_run_id)
    task.feedback_checked = True
    if outcome.status != "ok":
        status = outcome.status if outcome.status in NOTICES else "unavailable"
        task.feedback_failure = {**FeedbackReply(status=status, notice=NOTICES[status]).model_dump(mode="json"), "feedback_refresh_id": outcome.run_id}
        return None, task.feedback_failure
    task.feedback_pin = FeedbackPin(outcome.version_id, outcome.verified_at, outcome.scan_started_at, "fresh_scan")
    return task.feedback_pin, None


def pin_historical_feedback(task, frozen):
    """Historical reads and derived queries must agree on one version, including no evidence."""
    parent_version = frozen.feedback_version_id if frozen is not None else None
    if task.feedback_checked:
        pinned_version = task.feedback_pin.version_id if task.feedback_pin else None
        if pinned_version != parent_version or task.feedback_failure is not None:
            return None, FeedbackReply(
                status="unavailable", notice="本轮已固定另一份反馈依据，不能同时混用历史候选版本；请在新一轮继续这份历史候选或重新评估。"
            ).model_dump(mode="json")
        return task.feedback_pin, None
    task.feedback_checked = True
    if frozen is not None:
        task.feedback_pin = FeedbackPin(
            frozen.feedback_version_id,
            stamp(frozen.last_verified_at) if frozen.last_verified_at else None,
            stamp(frozen.scan_started_at),
            "historical",
        )
    return task.feedback_pin, None


async def candidate_feedback(task, pick_repo, record, pin):
    """Interpret feedback before the candidate or its sidecar becomes visible."""
    if pin is None:
        return None
    repo = FeedbackRepository(task.service.session_factory, task.owner_id)
    # Source-derived validation errors can reach the model too, so mark the read before interpretation.
    task.plugin_read = True
    snapshot = await repo.snapshot(pin.version_id)
    catalog = await pick_repo.catalog_rows(record["catalog_batch_id"])
    reply = await asyncio.to_thread(
        drama_feedback,
        snapshot,
        catalog,
        pin.version_id,
        freshness=pin.freshness,
        verified_at=pin.verified_at,
        candidate_identities={item["identity"] for item in record["ordered_items_json"]},
    )
    if pin.scan_started_at and pin.verified_at:
        reply = FeedbackReply.model_validate({**reply.model_dump(mode="json"), "scan_started_at": pin.scan_started_at, "scan_completed_at": pin.verified_at})
    return reply


async def frozen_feedback(task, result_id):
    repo = FeedbackRepository(task.service.session_factory, task.owner_id)
    reply = await repo.result_evidence(result_id)
    service = getattr(task.service, "feedback", None)
    if service is not None and service.enabled and service.owner_id == task.owner_id:
        _, failure = pin_historical_feedback(task, reply)
        if failure is not None:
            return FeedbackReply.model_validate(failure)
    if reply is not None:
        task.plugin_read = True
        return reply.model_copy(update={"freshness": "historical"})
    return FeedbackReply(status="unavailable", notice="这份历史候选没有保存运营反馈依据；需要最新评估时请重新查询。")


def model_feedback(reply: FeedbackReply) -> dict:
    """Bound provenance shown to the model; full evidence remains in the owned result notes."""
    value = reply.model_dump(mode="json")
    for item in value["items"]:
        refs = item["evidence_refs"]
        if len(refs) > 3:
            item["evidence_refs"] = refs[:3]
            item["metrics"]["evidence_records_total"] = len(refs)
            item["warnings"].append("evidence_preview_truncated")
    return value
