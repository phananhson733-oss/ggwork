"""Pick-only publication capability installed in the host's live task store.

This is ephemeral transport state, never a second message repository. Only the
host installs the capability; caller context and message metadata cannot opt in.
"""

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal

PICK_EXECUTION_ERROR = "本轮未能完成可核对的答复，请重试。"


@dataclass(frozen=True)
class PickCompletionMetadata:
    """Sanitized public verification metadata; never raw claim audit records."""

    status: Literal["confirmed", "partial", "incomplete"]
    checker_version: str
    checked_at: str
    correction_count: int

    def wire(self) -> dict[str, Any]:
        """Return the bounded public metadata fields after validating status and count."""
        if self.status not in {"confirmed", "partial", "incomplete"} or self.correction_count not in {0, 1}:
            raise ValueError("Invalid checked publication metadata")
        return {"status": self.status, "checker_version": self.checker_version, "checked_at": self.checked_at, "correction_count": self.correction_count}


@dataclass
class PickPublication:
    """Ephemeral, host-installed receipt gate for one owner-scoped pick run."""

    thread_id: str
    run_id: str
    deadline: float | None = None
    active: bool = False
    _fallback: dict[str, Any] | None = field(default=None, repr=False)
    _final: dict[str, Any] | None = field(default=None, repr=False)
    _pending: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _tool_ids: set[str] = field(default_factory=set, repr=False)
    _final_usage: dict[str, Any] = field(default_factory=dict, repr=False)
    on_tool_message: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    _tool_results: dict[str, tuple[str | None, str]] = field(default_factory=dict, repr=False)
    _human_inputs: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def message_id(self) -> str:
        """Return the stable canonical assistant message identifier for this run."""
        return f"pick-{self.run_id}"

    def record_final_usage(self, usage: dict[str, Any] | None) -> None:
        """Accumulate final and correction usage without accepting provider prose."""
        if not usage:
            return
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            self._final_usage[key] = self._final_usage.get(key, 0) + usage.get(key, 0)
        for key in ("input_token_details", "output_token_details"):
            for name, value in (usage.get(key) or {}).items():
                if isinstance(value, int):
                    details = self._final_usage.setdefault(key, {})
                    details[name] = details.get(name, 0) + value

    def _message(self, content: str, metadata: PickCompletionMetadata) -> dict[str, Any]:
        return {"type": "ai", "id": self.message_id, "content": content, "additional_kwargs": {"pick_completion": metadata.wire()}, "tool_calls": []}

    def prepare(self, content: str, metadata: PickCompletionMetadata) -> None:
        """Prepare the fixed fallback before the host activates the capability."""
        if not self.active:
            self._fallback = self._message(content, metadata)

    def activate(self) -> None:
        """Activate only a capability already prepared by the trusted lifecycle."""
        if self._fallback is None:
            raise RuntimeError("Pick lifecycle has not prepared publication")
        self.active = True

    def approve(self, content: str, metadata: PickCompletionMetadata) -> dict[str, Any]:
        """Freeze and return the first approved canonical response."""
        if not self.active:
            raise RuntimeError("Pick publication is not active")
        if self._final is None:
            self._final = self._message(content, metadata)
            if self._final_usage:
                self._final["usage_metadata"] = deepcopy(self._final_usage)
            self._pending.append(self._final)
        return deepcopy(self._final)

    def correction_started(self) -> None:
        """Mark the fallback as having consumed the single permitted correction."""
        if self._fallback is not None:
            self._fallback["additional_kwargs"]["pick_completion"]["correction_count"] = 1

    def incomplete(self) -> dict[str, Any]:
        """Freeze the prepared fallback if no canonical response was approved."""
        if self._final is None:
            if self._fallback is None:
                raise RuntimeError("Pick publication is not active")
            self._final = deepcopy(self._fallback)
            if self._final_usage:
                self._final["usage_metadata"] = deepcopy(self._final_usage)
            self._pending.append(self._final)
        return deepcopy(self._final)

    def tool_message(self, message: dict[str, Any], *, publish: bool = True) -> dict[str, Any]:
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

    def record_tool_result(self, name: str | None, call_id: str, content: object) -> None:
        """Bind an actual host-observed tool result to its name, call ID and content."""
        digest = sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self._tool_results[call_id] = (name, digest)

    def has_tool_result(self, name: str, call_id: str, content: object) -> bool:
        """Check exact receipt identity and content without trusting model claims."""
        digest = sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return self._tool_results.get(call_id) == (name, digest)

    def _human_input_digest(self, message: dict[str, Any]) -> tuple[str, str] | None:
        artifact = message.get("artifact")
        request = artifact.get("human_input") if isinstance(artifact, Mapping) else None
        call_id = message.get("tool_call_id")
        if (
            message.get("type") != "tool"
            or message.get("name") != "ask_clarification"
            or not isinstance(call_id, str)
            or not isinstance(request, Mapping)
            or request.get("kind") != "human_input_request"
            or request.get("source") != "ask_clarification"
            or request.get("tool_call_id") != call_id
            or request.get("request_id") != message.get("id")
            or not self.has_tool_result("ask_clarification", call_id, message.get("content"))
        ):
            return None
        basis = [message.get("id"), call_id, message.get("content"), request, message.get("additional_kwargs", {}), message.get("status", "success")]
        return call_id, sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def record_human_input(self, message: dict[str, Any]) -> None:
        """Bind a current host-executed clarification artifact to its exact tool receipt."""
        receipt = self._human_input_digest(message)
        if receipt is not None:
            self._human_inputs[receipt[0]] = receipt[1]

    def has_human_input(self, message: dict[str, Any]) -> bool:
        """Recognize only an unchanged current-run operational request, never prose facts."""
        receipt = self._human_input_digest(message)
        return receipt is not None and self._human_inputs.get(receipt[0]) == receipt[1]

    def drain_tools(self) -> list[dict[str, Any]]:
        """Drain only pending sanitized tool-call messages for publication."""
        tools = [message for message in self._pending if message.get("tool_calls")]
        self._pending = [message for message in self._pending if not message.get("tool_calls")]
        return deepcopy(tools)

    def drain(self) -> list[dict[str, Any]]:
        """Drain all pending canonical messages as detached copies."""
        pending, self._pending = self._pending, []
        return deepcopy(pending)

    @property
    def has_final(self) -> bool:
        """Whether a canonical final or fixed fallback has been frozen."""
        return self._final is not None
