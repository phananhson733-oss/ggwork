"""Verify the actual LangChain wire contract without using any real credential."""

import json
from pathlib import Path

import httpx
import pytest
from deerflow.config.app_config import AppConfig
from deerflow.models.factory import create_chat_model

TEMPLATE = Path(__file__).resolve().parents[3] / "config.pick.example.yaml"
# The defaults pick_entrypoint fills when Railway sets none (backend/tests/test_pick_cloud_entrypoint.py pins them there).
MODEL_DEFAULTS = {
    "PICK_LLM_EFFORT_THINKING_ON": "high",
    "PICK_LLM_EFFORT_THINKING_OFF": "low",
    "PICK_LLM_MAX_OUTPUT_TOKENS": "32000",
    "PICK_LLM_REQUEST_TIMEOUT_SECONDS": "300",
    "PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS": "300",
}


def _respond(requests):
    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_synthetic",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "synthetic-deployment",
                "output": [
                    {
                        "id": "fc_synthetic",
                        "type": "function_call",
                        "call_id": "call_synthetic",
                        "name": "pick_query_candidates",
                        "arguments": '{"filters":{"language":"en","limit":1}}',
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            },
        )

    return respond


@pytest.fixture
def pick_config(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "synthetic-deployment")
    for name, value in MODEL_DEFAULTS.items():
        monkeypatch.setenv(name, value)
    return lambda: AppConfig.from_file(str(TEMPLATE))


async def _sent_body(config, *, thinking_enabled):
    from ggwork_pick.tools import query_candidates_tool

    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_respond(requests))) as client:
        model = create_chat_model("azure-pick", thinking_enabled=thinking_enabled, app_config=config, attach_tracing=False, http_async_client=client)
        result = await model.bind_tools([query_candidates_tool]).ainvoke("Synthetic tool-call test")
    assert result.tool_calls[0]["name"] == "pick_query_candidates"
    return requests[0], json.loads(requests[0].content)


@pytest.mark.asyncio
async def test_azure_profile_uses_responses_and_keeps_tool_calls_stateless(pick_config):
    config = pick_config()
    assert [model.name for model in config.models] == ["azure-pick"]
    request, body = await _sent_body(config, thinking_enabled=False)
    assert request.url.path == "/openai/v1/responses"
    assert request.headers["api-key"] == "synthetic-test-key"
    assert body["store"] is False
    assert "previous_response_id" not in body
    assert "temperature" not in body
    assert body["parallel_tool_calls"] is False
    assert "reasoning.encrypted_content" in body["include"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("thinking_enabled", "effort"), [(True, "high"), (False, "low")])
async def test_thinking_switch_picks_the_effort_and_the_output_cap_reaches_the_wire(pick_config, thinking_enabled, effort):
    _, body = await _sent_body(pick_config(), thinking_enabled=thinking_enabled)
    assert body["reasoning"] == {"effort": effort}
    assert body["max_output_tokens"] == 32000


@pytest.mark.asyncio
async def test_railway_variables_change_the_request_without_a_rebuild(pick_config, monkeypatch):
    monkeypatch.setenv("PICK_LLM_EFFORT_THINKING_ON", "medium")
    monkeypatch.setenv("PICK_LLM_MAX_OUTPUT_TOKENS", "8000")
    monkeypatch.setenv("PICK_LLM_REQUEST_TIMEOUT_SECONDS", "90")
    config = pick_config()
    _, body = await _sent_body(config, thinking_enabled=True)
    assert body["reasoning"] == {"effort": "medium"}
    assert body["max_output_tokens"] == 8000
    # A string timeout would fail every request as a connection error; the config hands the client a float.
    model = create_chat_model("azure-pick", thinking_enabled=True, app_config=config, attach_tracing=False)
    assert model.request_timeout == 90.0 and type(model.request_timeout) is float
    assert model.stream_chunk_timeout == 300.0


def test_the_profile_does_not_load_without_its_model_variables(pick_config, monkeypatch):
    # Why pick_entrypoint fills defaults: AppConfig refuses a placeholder whose variable is unset.
    monkeypatch.delenv("PICK_LLM_EFFORT_THINKING_ON")
    with pytest.raises(ValueError, match="PICK_LLM_EFFORT_THINKING_ON"):
        pick_config()
