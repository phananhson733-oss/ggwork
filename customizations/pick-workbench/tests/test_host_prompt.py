"""2026-10-05 (evaluation batch 2): the host's system prompt still told the pick agent to read_file a skill, save
deliverables under /mnt/user-data and present_files them, edit with str_replace, and call tools in parallel. None of
those tools reach the model (PickModelGate keeps the pick tools, ask_clarification and plugins), and the pick model
runs with parallel_tool_calls off. The gate now drops those sections and reminders before appending its own."""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.config.app_config import AppConfig

TEMPLATE = Path(__file__).resolve().parents[3] / "config.pick.example.yaml"
MODEL_ENV = {
    "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1/",
    "AZURE_OPENAI_API_KEY": "synthetic-test-key",
    "AZURE_OPENAI_DEPLOYMENT": "synthetic-deployment",
    "PICK_LLM_EFFORT_THINKING_ON": "high",
    "PICK_LLM_EFFORT_THINKING_OFF": "low",
    "PICK_LLM_MAX_OUTPUT_TOKENS": "32000",
    "PICK_LLM_REQUEST_TIMEOUT_SECONDS": "300",
    "PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS": "300",
}
# What the pick model can never call or open.
FOREIGN = re.compile(
    r"read_file|write_file|str_replace|present_files|list_uploaded_files|skill_manage|describe_skill|/mnt/user-data|/mnt/skills"
    r"|parallel tool|`task`|DELEGATION|Delegation"
)
KEPT = ("<role>", "<clarification_system>", "<citations>", "Clarification First", "Language Consistency", "Always Respond")


@pytest.fixture
def host_prompt(monkeypatch):
    for name, value in MODEL_ENV.items():
        monkeypatch.setenv(name, value)
    config = AppConfig.from_file(str(TEMPLATE))

    def render(**kwargs):
        return apply_prompt_template(app_config=config, skill_names=frozenset({"pick-drama"}), memory_enabled=False, user_id="alice", **kwargs)

    return render


def test_the_rendered_host_prompt_names_tools_the_pick_agent_lacks(host_prompt):
    # Pins the premise: if the host stops saying these, the cut below has nothing left to do.
    assert FOREIGN.search(host_prompt())


@pytest.mark.parametrize("subagent_enabled", [False, True])
def test_the_pick_cut_keeps_no_foreign_tool_and_keeps_the_rest(host_prompt, subagent_enabled):
    from ggwork_pick.host_prompt import pick_system

    cut = pick_system(host_prompt(subagent_enabled=subagent_enabled))
    assert not FOREIGN.findall(cut)
    assert "</subagent_system>" not in cut and "</skill_system>" not in cut and "</working_directory>" not in cut
    assert all(kept in cut for kept in KEPT)


def test_text_the_cut_does_not_recognise_passes_through():
    from ggwork_pick.host_prompt import pick_system

    assert pick_system("") == ""
    plain = "<role>\nYou are a helper.\n</role>\n<critical_reminders>\n- Clarity: be direct\n</critical_reminders>"
    assert pick_system(plain) == plain


@pytest.mark.asyncio
async def test_the_model_gate_sends_the_cut_prompt_with_the_pick_instructions(host_prompt):
    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import SystemMessage

    from ggwork_pick.context import PickTask
    from ggwork_pick.middleware import PICK_INSTRUCTIONS, PickModelGate

    task = PickTask(service=None, info=TaskInfo("t", "r", "c", "lead"))
    task.repository = AsyncMock()
    store = ExtensionData("t")
    store.set(task)
    runtime = SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store})
    request = ModelRequest(model=SimpleNamespace(), messages=[], runtime=runtime, tools=[], system_message=SystemMessage(content=host_prompt()))
    adjusted = await PickModelGate().awrap_model_call(request, AsyncMock(side_effect=lambda adjusted: adjusted))
    sent = adjusted.system_message.content
    assert PICK_INSTRUCTIONS in sent
    assert not FOREIGN.findall(sent.replace(PICK_INSTRUCTIONS, ""))
    assert all(kept in sent for kept in KEPT)
