"""Pick-only publication capability installed in the host's live task store.

This is ephemeral transport state, never a second message repository. Only the
host installs the capability; caller context and message metadata cannot opt in.
"""

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

PICK_EXECUTION_ERROR = "本轮未能完成可核对的答复，请重试。"


@dataclass(frozen=True)
class PickCompletionMetadata:
    status: Literal["confirmed", "partial", "incomplete"]
    checker_version: str
    checked_at: str
    correction_count: int

    def wire(self) -> dict:
        if self.status not in {"confirmed", "partial", "incomplete"} or self.correction_count not in {0, 1}:
            raise ValueError("Invalid checked publication metadata")
        return {"status": self.status, "checker_version": self.checker_version, "checked_at": self.checked_at, "correction_count": self.correction_count}


@dataclass
class PickPublication:
    thread_id: str
    run_id: str
    deadline: float | None = None
    active: bool = False
    _fallback: dict | None = field(default=None, repr=False)
    _final: dict | None = field(default=None, repr=False)
    _pending: list[dict] = field(default_factory=list, repr=False)
    _tool_ids: set[str] = field(default_factory=set, repr=False)
    _final_usage: dict = field(default_factory=dict, repr=False)
    on_tool_message: Callable[[dict], None] | None = field(default=None, repr=False)
    _tool_results: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)

    @property
    def message_id(self) -> str:
        return f"pick-{self.run_id}"

    def record_final_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            self._final_usage[key] = self._final_usage.get(key, 0) + usage.get(key, 0)
        for key in ("input_token_details", "output_token_details"):
            for name, value in (usage.get(key) or {}).items():
                if isinstance(value, int):
                    details = self._final_usage.setdefault(key, {})
                    details[name] = details.get(name, 0) + value

    def _message(self, content: str, metadata: PickCompletionMetadata) -> dict:
        return {"type": "ai", "id": self.message_id, "content": content, "additional_kwargs": {"pick_completion": metadata.wire()}, "tool_calls": []}

    def prepare(self, content: str, metadata: PickCompletionMetadata) -> None:
        if not self.active:
            self._fallback = self._message(content, metadata)

    def activate(self) -> None:
        if self._fallback is None:
            raise RuntimeError("Pick lifecycle has not prepared publication")
        self.active = True

    def approve(self, content: str, metadata: PickCompletionMetadata) -> dict:
        if not self.active:
            raise RuntimeError("Pick publication is not active")
        if self._final is None:
            self._final = self._message(content, metadata)
            if self._final_usage:
                self._final["usage_metadata"] = deepcopy(self._final_usage)
            self._pending.append(self._final)
        return deepcopy(self._final)

    def correction_started(self) -> None:
        if self._fallback is not None:
            self._fallback["additional_kwargs"]["pick_completion"]["correction_count"] = 1

    def incomplete(self) -> dict:
        if self._final is None:
            if self._fallback is None:
                raise RuntimeError("Pick publication is not active")
            self._final = deepcopy(self._fallback)
            if self._final_usage:
                self._final["usage_metadata"] = deepcopy(self._final_usage)
            self._pending.append(self._final)
        return deepcopy(self._final)

    def tool_message(self, message: dict, *, publish: bool = True) -> dict:
        """Keep tool correlation while removing all provisional provider prose."""
        safe = {"type": "ai", "id": message["id"], "content": "", "tool_calls": deepcopy(message["tool_calls"]), "additional_kwargs": {}}
        if message.get("usage_metadata"):
            safe["usage_metadata"] = deepcopy(message["usage_metadata"])
        # Preserve termination guards without carrying arbitrary provider text.
        finish = (message.get("response_metadata") or {}).get("finish_reason")
        if finish in {"length", "max_tokens", "content_filter", "safety", "stop", "tool_calls"}:
            safe["response_metadata"] = {"finish_reason": finish}
        if publish and safe["id"] not in self._tool_ids:
            self._tool_ids.add(safe["id"])
            self._pending.append(safe)
            if self.on_tool_message is not None:
                self.on_tool_message(deepcopy(safe))
        return safe

    def record_tool_result(self, name: str, call_id: str, content: object) -> None:
        digest = sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self._tool_results[call_id] = (name, digest)

    def has_tool_result(self, name: str, call_id: str, content: object) -> bool:
        digest = sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return self._tool_results.get(call_id) == (name, digest)

    def drain_tools(self) -> list[dict]:
        tools = [message for message in self._pending if message.get("tool_calls")]
        self._pending = [message for message in self._pending if not message.get("tool_calls")]
        return deepcopy(tools)

    def drain(self) -> list[dict]:
        pending, self._pending = self._pending, []
        return deepcopy(pending)

    @property
    def has_final(self) -> bool:
        return self._final is not None
