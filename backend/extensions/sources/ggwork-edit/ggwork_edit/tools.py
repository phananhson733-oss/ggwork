"""Thin conversation adapters over the same owner-scoped editing operations."""

import json
import re
from typing import Literal

from deerflow.tools.types import Runtime
from langchain.tools import tool
from langchain_core.tools import BaseTool

from ggwork_edit.capability import require_profile
from ggwork_edit.context import task_from_runtime
from ggwork_edit.contracts import ID, CreateTask, Manifest, PrepareTask, Requirements, RetryTask, SourceDirectory, StrictInput


class Submit(StrictInput):
    request_id: ID
    title: str
    requirements: Requirements
    device_id: ID | None = None
    source_manifest: Manifest | None = None
    source_directory: SourceDirectory | None = None


def task_receipt(task):
    """Bounded identity/recovery data; live cards resolve the full HTTP resource."""
    keys = (
        "id",
        "status",
        "stage",
        "device_id",
        "device_status",
        "access_status",
        "source_thread_id",
        "parent_task_id",
        "requested_count",
        "completed_count",
        "available_actions",
        "preparation_reasons",
        "plan_confirmed",
    )
    receipt = {key: task[key] for key in keys}
    receipt.update(
        title=task["title"][:80],
        requirements={key: value for key, value in task["requirements"].items() if key != "instructions"},
        source_count=len((task.get("source_manifest") or {}).get("files", [])),
        outputs=[{"id": output["id"], "status": output["status"], "error": output["error"][:64] if output["error"] else None} for output in task["outputs"]],
    )
    return receipt


def result(task):
    return json.dumps({"task": task_receipt(task)}, ensure_ascii=False, allow_nan=False)


EDITING_PAGE = "/workspace/editing"
# The same wording as the editing page (frontend core/editing/presentation.ts).
STATUS_TEXT = {
    "waiting": "待准备",
    "queued": "等待 Mac 开始处理",
    "running": "处理中",
    "awaiting_plan": "等待你确认方案",
    "stopping": "已请求停止，等待设备确认",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "失败",
    "stopped": "已停止",
}
STAGE_TEXT = {"transcribing": "转录素材", "planning": "生成方案", "rendering": "渲染视频", "verifying": "检查成片"}
PREPARATION_TEXT = {
    "device_required": "请连接并选择 Mac",
    "sources_required": "请提供素材",
    "sources_unverified": "等待全部素材本地校验",
    "directory_not_authorized": "请在 Mac 授权该素材目录",
}


def _task_line(receipt):
    status = receipt.get("status")
    text = STATUS_TEXT.get(status) if isinstance(status, str) else None
    if text is None:
        line = "剪辑任务状态已更新"
    else:
        stage = receipt.get("stage")
        if status == "running" and isinstance(stage, str) and stage in STAGE_TEXT:
            text += f"（{STAGE_TEXT[stage]}）"
        line = f"剪辑任务当前状态：{text}"
    done, wanted = receipt.get("completed_count"), receipt.get("requested_count")
    if type(done) is int and type(wanted) is int and 0 <= done <= wanted and wanted > 0:
        line += f"，成片 {done}/{wanted} 条"
    reasons = receipt.get("preparation_reasons")
    needed = [PREPARATION_TEXT[reason] for reason in reasons if isinstance(reason, str) and reason in PREPARATION_TEXT] if isinstance(reasons, list) else []
    if needed:
        line += "（" + "；".join(needed) + "）"
    return f"{line}。[打开剪辑任务]({EDITING_PAGE}/{receipt['id']})"


def receipt_reply(results):
    """Fixed owner-facing text for one run's editing tool results: (content, failed) pairs in call order.

    Only server-set identifiers, states and counts are rendered. Titles, instructions and error text are
    written by the model or the worker, so this text never repeats them. Returns (text, complete), or
    None when the results name no task, history page or refusal.
    """
    tasks, history, failed = {}, None, False
    for content, error in results:
        failed = bool(error)
        if failed:
            continue
        try:
            body = json.loads(content) if isinstance(content, str) else None
        except ValueError:
            body = None
        if not isinstance(body, dict):
            continue
        receipt = body.get("task")
        if isinstance(receipt, dict) and isinstance(receipt.get("id"), str) and re.fullmatch(r"[0-9a-f]{32}", receipt["id"]):
            # The latest receipt of a task replaces its earlier ones and moves it last.
            tasks.pop(receipt["id"], None)
            tasks[receipt["id"]] = receipt
        elif "device_id" not in body and isinstance(body.get("items"), list) and type(body.get("total")) is int and body["total"] >= 0:
            history = body["total"]
    lines = [_task_line(receipt) for receipt in list(tasks.values())[-5:]]
    if not lines and history is not None:
        lines.append(f"剪辑历史共 {history} 个任务。[打开剪辑页面]({EDITING_PAGE})")
    if failed:
        lines.append(f"最近一次剪辑操作未完成。请在[剪辑页面]({EDITING_PAGE})查看任务、设备与能力的当前状态后重试。")
    return ("\n".join(lines), not failed) if lines else None


def page(items, limit, offset):
    return {
        "items": items[offset : offset + limit],
        "limit": limit,
        "offset": offset,
        "total": len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


async def execution(runtime, profile):
    task = task_from_runtime(runtime)
    repo = task.repository(runtime)
    await require_profile({**runtime.context, "user_id": repo.owner}, profile, getattr(task.service, "planner_model", None))
    return task, repo


@tool("clip_submit")
async def submit_tool(request: Submit, runtime: Runtime) -> str:
    """Submit one drama's explicit editing intent. Missing device/material remains waiting.
    Complete requests run automatically unless review_plan=true was explicitly requested. Reuse request_id for retries.
    """
    payload = Submit.model_validate(request)
    task, repo = await execution(runtime, payload.requirements.profile)
    created = await repo.create_task(CreateTask(**payload.model_dump()), source_thread_id=task.info.thread_id)
    return result(created)


@tool("clip_get")
async def get_tool(
    runtime: Runtime, task_id: str | None = None, limit: int = 5, offset: int = 0, section: Literal["summary", "plan"] = "summary", device_id: str | None = None
) -> str:
    """Read a bounded current task receipt or paginated history (limit 1–5). Historical text is not live state.
    For plan review use task_id + section=plan and follow next_offset for all cuts. For preparation use device_id
    to page its authorized grant IDs. The overview includes a device page; device_next_offset is independent.
    """
    if not 1 <= limit <= 5 or offset < 0:
        raise ValueError("limit must be 1–5 and offset nonnegative")
    repo = task_from_runtime(runtime).repository(runtime)
    if device_id:
        devices = [d for d in await repo.devices() if d["id"] == device_id]
        if not devices:
            raise ValueError("Device unavailable")
        device = devices[0]
        return json.dumps({"device_id": device_id, **page(device["grants"], limit, offset)}, ensure_ascii=False)
    if task_id:
        task = await repo.get_task(task_id)
        if section == "plan":
            cuts = [{"output_id": output["output_id"], **cut} for output in (task.get("plan") or {}).get("outputs", []) for cut in output["segments"]]
            return json.dumps({"task": task_receipt(task), "plan_cuts": page(cuts, limit, offset)}, ensure_ascii=False)
        return result(task)
    tasks = await repo.list_tasks_page(limit=limit, offset=offset)
    tasks["items"] = [
        {key: task[key] for key in ("id", "status", "stage", "completed_count", "requested_count")} | {"title": task["title"][:80]} for task in tasks["items"]
    ]
    devices = page(await repo.devices(), limit, offset)
    tasks.update(
        devices=[
            {key: d[key] for key in ("id", "online", "ready", "revoked")} | {"name": d["name"][:80], "grant_count": len(d["grants"])} for d in devices["items"]
        ],
        device_next_offset=devices["next_offset"],
        capabilities=await repo.capabilities(),
    )
    return json.dumps(tasks, ensure_ascii=False, allow_nan=False)


@tool("clip_stop")
async def stop_tool(task_id: str, runtime: Runtime) -> str:
    """Request stopping this exact task. A stopping response still awaits the Mac's acknowledgment; verified outputs remain available."""
    return result(await task_from_runtime(runtime).repository(runtime).stop_task(task_id))


@tool("clip_prepare")
async def prepare_tool(task_id: str, preparation: PrepareTask, runtime: Runtime) -> str:
    """Bind a paired Mac and selected material to an existing waiting intent, preserving its identity. Paths are relative to the Mac's authorized grant."""
    repo = task_from_runtime(runtime).repository(runtime)
    current = await repo.get_task(task_id)
    await execution(runtime, current["requirements"]["profile"])
    return result(await repo.prepare(task_id, PrepareTask.model_validate(preparation)))


@tool("clip_retry")
async def retry_tool(task_id: str, retry: RetryTask, runtime: Runtime) -> str:
    """Retry failed output IDs only with a confirmed reusable plan; otherwise use stage=planning (or transcribing).
    Requirements stay unchanged. Never retry successful outputs.
    """
    repo = task_from_runtime(runtime).repository(runtime)
    current = await repo.get_task(task_id)
    await execution(runtime, current["requirements"]["profile"])
    return result(await repo.retry(task_id, RetryTask.model_validate(retry)))


@tool("clip_confirm_plan")
async def confirm_tool(task_id: str, runtime: Runtime) -> str:
    """Confirm an explicitly requested plan review for the same task and attempt."""
    repo = task_from_runtime(runtime).repository(runtime)
    current = await repo.get_task(task_id)
    await execution(runtime, current["requirements"]["profile"])
    return result(await repo.confirm_plan(task_id))


@tool("clip_change_version")
async def change_version_tool(task_id: str, request_id: str, requirements: Requirements, runtime: Runtime) -> str:
    """Create a linked version with changed requirements; preserve the previous task and all delivered outputs."""
    task = task_from_runtime(runtime)
    repo = task.repository(runtime)
    parent = await repo.get_task(task_id)
    requirements = Requirements.model_validate(requirements)
    await execution(runtime, requirements.profile)
    manifest = parent["source_manifest"]
    if manifest:
        manifest = {**manifest, "files": [{**item, "state": "selected", "sha256": None, "duration_seconds": None} for item in manifest["files"]]}
    payload = CreateTask(
        request_id=request_id,
        title=parent["title"],
        requirements=requirements,
        device_id=parent["device_id"],
        source_manifest=manifest,
        source_directory=parent["source_directory"] if manifest is None else None,
        parent_task_id=task_id,
    )
    return result(await repo.create_task(payload, source_thread_id=task.info.thread_id))


TOOLS = (submit_tool, get_tool, stop_tool, prepare_tool, retry_tool, confirm_tool, change_version_tool)


def is_editing_tool(candidate):
    return isinstance(candidate, BaseTool) and any(
        candidate.name == registered.name and getattr(candidate, "coroutine", None) is registered.coroutine for registered in TOOLS
    )
