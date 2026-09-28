"""User- and model-visible text says GGWork, not the upstream DeerFlow brand.

GGWork is a DeerFlow fork. Internal identifiers (the ``deerflow`` package,
``DEER_FLOW_*`` env vars, ``X-DeerFlow-*`` headers, class names, API paths)
intentionally keep the upstream name; only text that reaches end users, IM
chats, the TUI, API error bodies, or the model is rebranded. These pins catch
upstream merges that reintroduce the old brand into those surfaces.
"""

import json
from pathlib import Path

import pytest

from app.channels.buzz import _CONNECT_REPLY_TEXT
from app.channels.manager import BOUND_IDENTITY_REQUIRED_MESSAGE, BOUND_IDENTITY_UNAVAILABLE_MESSAGE
from app.gateway.conversation_access import _source_id
from app.gateway.routers.assistants_compat import _get_default_assistant
from app.gateway.routers.channel_connections import _PROVIDER_META, _connect_instruction
from app.gateway.routers.input_polish import _build_system_instruction
from app.gateway.services import _mcp_task_notification_prompt
from deerflow.tools.builtins.background_tasks_tool import cancel_background_task
from deerflow.tools.builtins.batch_task_tool import batch_task
from deerflow.tools.builtins.setup_agent_tool import setup_agent
from deerflow.tui.cli import _HEADLESS_HELP, build_parser

UPSTREAM_BRAND = "DeerFlow"
BRAND = "GGWork"

BUILTIN_CATALOG = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow" / "capabilities" / "builtin.json"


@pytest.mark.parametrize("provider", sorted(_PROVIDER_META))
def test_channel_connect_instructions_name_ggwork_bot(provider):
    instruction = _connect_instruction(provider, "abc123")

    assert f"the {BRAND} " in instruction
    assert UPSTREAM_BRAND not in instruction


def test_channel_reply_messages_use_ggwork():
    messages = [BOUND_IDENTITY_REQUIRED_MESSAGE, BOUND_IDENTITY_UNAVAILABLE_MESSAGE, *_CONNECT_REPLY_TEXT.values()]

    assert _CONNECT_REPLY_TEXT["success"] == f"Buzz connected to {BRAND}."
    assert f"{BRAND} Settings" in BOUND_IDENTITY_REQUIRED_MESSAGE
    assert all(UPSTREAM_BRAND not in message for message in messages)


def test_input_polish_system_instruction_uses_ggwork():
    instruction = _build_system_instruction()

    assert instruction.startswith(f"You are {BRAND}'s pre-send prompt optimizer.")
    assert UPSTREAM_BRAND not in instruction


@pytest.mark.parametrize(
    "reference",
    ["ftp://example.test/workspace/chats/t1", "http://example.test/not-a-chat"],
)
def test_conversation_reference_errors_use_ggwork(reference):
    with pytest.raises(ValueError) as exc_info:
        _source_id(reference, "http://example.test/api/threads/runs")

    assert BRAND in str(exc_info.value)
    assert UPSTREAM_BRAND not in str(exc_info.value)


def test_mcp_task_notification_prompt_uses_ggwork():
    prompt = _mcp_task_notification_prompt({"status": "working", "tracking_degraded": True})

    assert f"{BRAND} will continue retrying" in prompt
    assert UPSTREAM_BRAND not in prompt


def test_default_assistant_description_uses_ggwork():
    assert _get_default_assistant().description == f"{BRAND} lead agent"


def test_model_facing_tool_descriptions_use_ggwork():
    texts = [
        cancel_background_task.args["task"]["description"],
        setup_agent.description,
        batch_task.description,
    ]

    assert all(BRAND in text for text in texts)
    assert all(UPSTREAM_BRAND not in text for text in texts)


def test_tui_cli_help_uses_ggwork():
    texts = [build_parser().description or "", _HEADLESS_HELP.splitlines()[0]]

    assert all(BRAND in text for text in texts)
    assert all(UPSTREAM_BRAND not in text for text in texts)


def test_builtin_capability_setup_text_uses_ggwork():
    catalog = json.loads(BUILTIN_CATALOG.read_text(encoding="utf-8"))
    entries = catalog if isinstance(catalog, list) else catalog.get("capabilities", [])
    setup_texts = [text for entry in entries for text in (entry.get("setup") or {}).values()]

    assert setup_texts, "expected native capabilities with setup guidance"
    assert any(BRAND in text for text in setup_texts)
    assert all(UPSTREAM_BRAND not in text for text in setup_texts)
