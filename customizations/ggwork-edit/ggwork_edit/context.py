"""Task-local editing access with host-authenticated owner and source thread."""

from dataclasses import dataclass

from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow_extension_api import TaskInfo, task_store_from_runtime


@dataclass
class EditingTask:
    service: object
    info: TaskInfo
    owner: str | None = None

    def repository(self, runtime):
        owner = resolve_runtime_user_id(runtime)
        if not owner or owner in ("default", "system:shared") or (self.owner is not None and owner != self.owner):
            raise PermissionError("Editing requires a stable authenticated owner")
        self.owner = owner
        return self.service.repository(owner)


def task_from_runtime(runtime):
    store = task_store_from_runtime(runtime)
    task = store.get(EditingTask) if store is not None else None
    if task is None or task.info.kind != "lead":
        raise PermissionError("Editing tools require a controlled lead conversation")
    return task


class EditingLifecycle:
    def __init__(self, service):
        self.service = service

    async def on_task_start(self, app_store, task_store, info):
        task_store.set(EditingTask(self.service, info))

    async def on_task_stop(self, app_store, task_store, info, outcome):
        task_store.remove(EditingTask)
