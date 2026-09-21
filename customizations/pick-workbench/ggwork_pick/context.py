"""Task-scoped state, tied to the host's authenticated runtime."""

import asyncio
import time
from dataclasses import dataclass, field

from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow_extension_api import TaskInfo, task_store_from_runtime

from ggwork_pick.repository import PickRepository


@dataclass
class PickTask:
    service: object
    info: TaskInfo
    deadline: float = field(default_factory=lambda: time.monotonic() + 120)
    owner_id: str | None = None
    catalog_id: str | None = None
    knowledge_id: str | None = None
    reference_id: str | None = None
    selected_item_ids: list[str] = field(default_factory=list)
    reference_order: list[str] = field(default_factory=list)
    produced_result_ids: set[str] = field(default_factory=set)
    versions_refreshed: bool = False
    execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    initialized: bool = False
    model_calls: int = 0
    tool_calls: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("本轮选剧已达到120秒执行上限")
        return remaining

    async def repository(self, runtime):
        owner = resolve_runtime_user_id(runtime)
        if not owner or owner == "default":
            raise ValueError("运行缺少已认证身份")
        if self.service.session_factory is None:
            raise ValueError("选剧服务尚未就绪")
        async with self.lock:
            if self.owner_id is not None and self.owner_id != owner:
                raise ValueError("运行身份发生变化")
            self.owner_id = owner
            repo = PickRepository(self.service.session_factory, owner)
            if not self.initialized:
                reference = runtime.context.get("pick_reference")
                if reference is not None:
                    if not isinstance(reference, dict) or not isinstance(reference.get("result_id"), str):
                        raise ValueError("候选引用无效")
                    parent = await repo.result(reference["result_id"])
                    if parent["thread_id"] != self.info.thread_id:
                        raise ValueError("引用结果不属于当前对话")
                    ids = reference.get("item_ids", [])
                    order = [item["item_id"] for item in parent["ordered_items_json"]]
                    if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(item_id, str) or item_id not in order for item_id in ids):
                        raise ValueError("候选引用条目无效")
                    self.reference_order = order
                    self.selected_item_ids = [item_id for item_id in order if item_id in ids]
                    self.reference_id = parent["id"]
                    self.catalog_id, self.knowledge_id = parent["catalog_batch_id"], parent["knowledge_batch_id"]
                else:
                    catalog = await repo.current_batch("catalog")
                    knowledge = await repo.current_batch("knowledge")
                    self.catalog_id = catalog["id"] if catalog else None
                    self.knowledge_id = knowledge["id"] if knowledge else None
                self.initialized = True
            return repo


def task_from_runtime(runtime):
    store = task_store_from_runtime(runtime)
    task = store.get(PickTask) if store is not None else None
    if task is None or task.info.kind != "lead":
        raise ValueError("选剧工具只能在受控的个人对话运行中调用")
    task.remaining()
    return task


class PickLifecycle:
    def __init__(self, service):
        self.service = service

    async def on_task_start(self, app_store, task_store, info):
        task_store.set(PickTask(self.service, info))

    async def on_task_stop(self, app_store, task_store, info, outcome):
        task_store.remove(PickTask)
