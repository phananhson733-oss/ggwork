"""Task-scoped state, tied to the host's authenticated runtime."""

import asyncio
import math
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow_extension_api import TaskInfo, task_store_from_runtime
from deerflow_extension_api.pick_publication import PickPublication

from ggwork_pick.answer_check import Seen
from ggwork_pick.answer_evidence import AnswerEvidence
from ggwork_pick.pin import Pin
from ggwork_pick.reference_contracts import PickReferences
from ggwork_pick.repository import PickRepository

# A gateway started without PICK_RUN_TIMEOUT_SECONDS has no host watchdog; the turn still ends here.
DEFAULT_RUN_SECONDS = 120.0
# Event-loop absolute time only; never pass this value to a synchronous worker.
query_call_loop_deadline: ContextVar[float | None] = ContextVar("pick_query_call_loop_deadline", default=None)


def run_seconds() -> float:
    """The turn's budget: the host's PICK_RUN_TIMEOUT_SECONDS (pick_entrypoint always sets it), checked the host's way."""
    raw = os.environ.get("PICK_RUN_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_RUN_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("PICK_RUN_TIMEOUT_SECONDS must be finite and positive")
    return seconds


@dataclass
class PickTask:
    service: object
    info: TaskInfo
    # Host publication and synchronous workers use time.monotonic(), not loop.time().
    deadline: float | None = None
    owner_id: str | None = None
    catalog_id: str | None = None
    knowledge_id: str | None = None
    # The mirror version paired with the two batches (None when there is none) and the data_as_of that goes with them.
    mirror_version: int | None = None
    data_as_of: dict | None = None
    reference_id: str | None = None
    references: dict[str, list[str]] = field(default_factory=dict)
    reference_context: list[dict] = field(default_factory=list)
    selected_item_ids: list[str] = field(default_factory=list)
    reference_order: list[str] = field(default_factory=list)
    produced_result_ids: set[str] = field(default_factory=set)
    known_titles: set[str] = field(default_factory=set)
    answer_evidence: AnswerEvidence = field(default_factory=AnswerEvidence)
    posted_checked: bool = False
    # Normalized title -> what the posted summaries of items a tool returned say (answer_check.with_posted).
    posted_seen: dict[str, Seen] = field(default_factory=dict)
    versions_refreshed: bool = False
    feedback_checked: bool = False
    feedback_pin: object | None = None
    feedback_failure: dict | None = None
    execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    initialized: bool = False
    model_calls: int = 0
    tool_calls: int = 0
    plugin_calls: int = 0
    lark_calls: int = 0
    # Set once a read-only plugin brought outside content into this run; see PickToolGate.
    plugin_read: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    budget: float = field(default_factory=run_seconds)
    publication: PickPublication | None = None

    def __post_init__(self) -> None:
        if self.deadline is None:
            self.deadline = time.monotonic() + self.budget

    def pin(self) -> Pin:
        return Pin(self.catalog_id, self.knowledge_id, self.mirror_version, self.data_as_of)

    def repin(self, pin: Pin) -> None:
        self.catalog_id, self.knowledge_id, self.mirror_version, self.data_as_of = pin

    @property
    def ordinary_deadline(self) -> float:
        """Monotonic-clock deadline; finalization owns the last twenty seconds."""
        return self.deadline - 20.0

    @property
    def ordinary_loop_deadline(self) -> float:
        """Translate remaining monotonic duration to the running loop's own epoch."""
        # Sample loop time first so conversion overhead can only shorten the budget.
        loop_now = asyncio.get_running_loop().time()
        return loop_now + self.ordinary_remaining()

    @property
    def query_deadline(self) -> float:
        """Event-loop deadline for query/SQL consumers, including the invocation cap."""
        deadline = query_call_loop_deadline.get()
        return min(self.ordinary_loop_deadline, deadline if deadline is not None else float("inf"))

    def ordinary_remaining(self):
        remaining = self.ordinary_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("本轮选剧已进入收尾阶段")
        return remaining

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"本轮选剧已达到{self.budget:g}秒执行上限")
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
                current_pin = await repo.current_pin()
                reference = runtime.context.get("pick_reference")
                if "pick_references" in runtime.context:
                    try:
                        if "pick_reference" in runtime.context:
                            raise ValueError("conflicting references")
                        plural = PickReferences.model_validate(runtime.context["pick_references"])
                        validated = []
                        for group in plural.references:
                            parent = await repo.result(group.result_id)
                            order = [item["item_id"] for item in parent["ordered_items_json"]]
                            if parent["thread_id"] != self.info.thread_id or any(item_id not in order for item_id in group.item_ids):
                                raise ValueError("invalid reference")
                            selected = [item_id for item_id in order if item_id in group.item_ids]
                            validated.append((parent, selected, order, await repo.result_data_as_of(parent, emit_mirror_version=True)))
                    except (ValueError, LookupError):
                        raise ValueError("候选引用无效或已不可用，请重新选择当前对话中的候选和条目") from None
                    for parent, selected, order, data_as_of in validated:
                        self.references[parent["id"]] = selected
                        self.reference_context.append(
                            {
                                "result_id": parent["id"],
                                "item_ids": selected,
                                "original_item_order": order,
                                "catalog_batch_id": parent["catalog_batch_id"],
                                "knowledge_batch_id": parent["knowledge_batch_id"],
                                "mirror_version": parent.get("mirror_version"),
                                "rule_version": parent["rule_version"],
                                "ranking_version": parent["ranking_version"],
                                "data_as_of": data_as_of,
                            }
                        )
                        self.known_titles.update(item["title"] for item in parent["ordered_items_json"] if item["item_id"] in selected)
                    if len(validated) == 1:
                        parent, selected, order, _ = validated[0]
                        self.reference_id, self.selected_item_ids, self.reference_order = parent["id"], selected, order
                elif reference is not None:
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
                    self.known_titles.update(item["title"] for item in parent["ordered_items_json"])
                    self.selected_item_ids = [item_id for item_id in order if item_id in ids]
                    self.reference_id = parent["id"]
                # Every run reads the current data in one read; only 换一批 goes back to the bound card's version.
                self.repin(current_pin)
                self.initialized = True
            return repo


def task_from_runtime(runtime, *, ordinary=True):
    store = task_store_from_runtime(runtime)
    task = store.get(PickTask) if store is not None else None
    if task is None or task.info.kind != "lead":
        raise ValueError("选剧工具只能在受控的个人对话运行中调用")
    task.ordinary_remaining() if ordinary else task.remaining()
    return task


class PickLifecycle:
    def __init__(self, service):
        self.service = service

    async def on_task_start(self, app_store, task_store, info):
        from deerflow_extension_api.pick_publication import PickCompletionMetadata

        from ggwork_pick.answer_check import incomplete_publication

        publication = task_store.get(PickPublication)
        task = PickTask(self.service, info)
        if publication is not None and info.kind == "lead":
            fallback = incomplete_publication(thread_id=info.thread_id, run_id=info.run_id, message_id=publication.message_id)
            publication.prepare(
                fallback.content, PickCompletionMetadata(fallback.status, fallback.checker_version, fallback.checked_at, fallback.correction_count)
            )
            task.publication = publication
            if publication.deadline is not None:
                task.deadline = min(task.deadline, publication.deadline)
        task_store.set(task)

    async def on_task_stop(self, app_store, task_store, info, outcome):
        task_store.remove(PickTask)
