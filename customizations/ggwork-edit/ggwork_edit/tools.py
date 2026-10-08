"""Thin conversation adapters over the same owner-scoped editing operations."""

import json

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


def result(task):
    return json.dumps({"task": task}, ensure_ascii=False, allow_nan=False)


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
async def get_tool(runtime: Runtime, task_id: str | None = None) -> str:
    """Read current editing task or list the owner's tasks. Historical conversation text is not current task state."""
    repo = task_from_runtime(runtime).repository(runtime)
    if task_id:
        return result(await repo.get_task(task_id))
    return json.dumps({"items": await repo.list_tasks(), "devices": await repo.devices(), "capabilities": await repo.capabilities()}, ensure_ascii=False)


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
    """Retry failed output IDs or an early failed transcribing/planning stage with unchanged requirements. Never retry successful outputs."""
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
