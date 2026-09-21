"""Verify the actual LangChain wire contract without using any real credential."""

import json
from pathlib import Path

import httpx
import pytest
from deerflow.config.app_config import AppConfig
from deerflow.models.factory import create_chat_model


@pytest.mark.asyncio
async def test_azure_profile_uses_responses_and_keeps_tool_calls_stateless(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "synthetic-deployment")
    config = AppConfig.from_file(str(Path(__file__).resolve().parents[3] / "config.pick.example.yaml"))
    assert [model.name for model in config.models] == ["azure-pick"]
    requests = []

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

    from ggwork_pick.tools import query_candidates_tool

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = create_chat_model("azure-pick", app_config=config, attach_tracing=False, http_async_client=client)
        result = await model.bind_tools([query_candidates_tool]).ainvoke("Synthetic tool-call test")
    assert result.tool_calls[0]["name"] == "pick_query_candidates"
    assert result.tool_calls[0]["args"]["filters"]["language"] == "en"
    request = requests[0]
    assert request.url.path == "/openai/v1/responses"
    assert request.headers["api-key"] == "synthetic-test-key"
    body = json.loads(request.content)
    assert body["store"] is False
    assert "previous_response_id" not in body
    assert "temperature" not in body
    assert body["parallel_tool_calls"] is False
    assert "reasoning.encrypted_content" in body["include"]
