"""Feedback tools inherit identity and candidate ownership from the authenticated task."""

import asyncio
import json
from typing import Annotated

from deerflow.tools.types import Runtime
from langchain.tools import tool
from pydantic import Field

from ggwork_pick.context import task_from_runtime
from ggwork_pick.feedback.analytics import analyze_feedback
from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery, FeedbackDetailQuery, FeedbackReply
from ggwork_pick.feedback.runtime import frozen_feedback, model_feedback, noticed, prepare_feedback

RefreshId = Annotated[str, Field(pattern=r"^fr_[0-9a-f]{32}$")]


@tool("pick_get_feedback")
async def get_feedback_tool(query: FeedbackDetailQuery, runtime: Runtime) -> str:
    """读取当前绑定或本轮生成候选的历史运营反馈。使用工具返回的result_id与item_ids，不能猜测。
    反馈固定在当时版本，金额按来源/币种/粒度解释，unknown不代表零收益；不触发最新刷新。
    """
    from ggwork_pick.tools import _answer, _owned_result

    async def work():
        validated = FeedbackDetailQuery.model_validate(query)
        task, _repo, record = await _owned_result(runtime, validated.result_id)
        ids = {item["item_id"]: item["identity"] for item in record["ordered_items_json"]}
        if not set(validated.item_ids).issubset(ids):
            raise ValueError("反馈条目不属于绑定候选")
        reply = await frozen_feedback(task, validated.result_id)
        # Keep the original candidate-set coverage explicitly scoped, and per-item coverage on the requested rows.
        wanted = {ids[item] for item in validated.item_ids}
        reply = reply.model_copy(
            update={
                "items": [item for item in reply.items if item.key in wanted],
                "query_scope": {**reply.query_scope, "coverage_scope": "original_candidate_set", "requested_items": validated.item_ids},
            }
        )
        return json.dumps(model_feedback(reply), ensure_ascii=False)

    return await _answer(work)


@tool("pick_analyze_feedback")
async def analyze_feedback_tool(query: FeedbackAnalysisQuery, runtime: Runtime, feedback_refresh_id: RefreshId | None = None) -> str:
    """分析完整飞书反馈范围的题材/语言/剧场表现，不是只统计候选前20条。日期按北京时间的实际发布日期；缺省最近30自然日含当日。
    一轮固定反馈版本，使用最近一次成功读取的版本（freshness=stale，读取时间见scan_completed_at），不现场刷新。
    只有还没有任何版本时才返回refresh_pending：稍后用返回的feedback_refresh_id继续该刷新，不反复重试。
    国家/地区没有可靠维度，不能用语言/币种代替。没有固定年龄快照/点击分母时不得报D7或转化率。
    返回来源、样本、覆盖、混合场景提醒及金额通道；多标签组和收益通道不能直接相加。
    """
    from ggwork_pick.tools import _answer

    task = task_from_runtime(runtime)
    await task.repository(runtime)

    async def work():
        validated = FeedbackAnalysisQuery.model_validate(query)
        if validated.group_by == "country":
            return FeedbackReply(
                status="unsupported_dimension", notice="反馈中没有已验证的实际观众或付费国家维度；不能用语言或币种推断地区。"
            ).model_dump_json()
        pin, failure = await prepare_feedback(task, resume_run_id=feedback_refresh_id)
        if failure:
            return json.dumps(failure, ensure_ascii=False)
        if pin is None:
            notice = "这份历史候选没有保存运营反馈依据；需要最新评估时请重新查询。" if task.feedback_checked else "当前用户尚未启用运营反馈来源。"
            return FeedbackReply(status="unavailable", notice=notice).model_dump_json()
        # Normalization failures may include source content in the tool's refusal.
        task.plugin_read = True
        snapshot = await task.service.feedback.repository(task.owner_id).snapshot(pin.version_id)
        reply = await asyncio.to_thread(analyze_feedback, snapshot, validated, pin.version_id, freshness=pin.freshness, verified_at=pin.verified_at)
        if reply.status == "ok" and pin.scan_started_at and pin.verified_at:
            reply = FeedbackReply.model_validate(
                {**reply.model_dump(mode="json"), "scan_started_at": pin.scan_started_at, "scan_completed_at": pin.verified_at}
            )
        return json.dumps(model_feedback(noticed(reply, pin)), ensure_ascii=False)

    return await _answer(work)
