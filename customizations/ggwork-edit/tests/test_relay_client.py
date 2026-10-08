"""Native transport must never redirect private media bytes to another origin."""

import httpx
import pytest

from ggwork_edit.worker.relay_client import RelayClient


@pytest.mark.asyncio
async def test_native_response_never_follows_redirect_with_video_bytes():
    observed = []

    def gateway(request):
        observed.append((str(request.url), request.content))
        if request.url.path.endswith("/commands"):
            return httpx.Response(200, json={"items": [{"id": "c1", "operation": "read", "offset": 0, "length": 5}]})
        return httpx.Response(307, headers={"Location": "https://other.invalid/collect"}) if request.url.host == "gateway.invalid" else httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), base_url="https://gateway.invalid", follow_redirects=True) as http:
        native = RelayClient(http, "device", receive=lambda command, data: None, read=lambda command: b"VIDEO")
        with pytest.raises(httpx.HTTPStatusError):
            await native.poll_once()
    assert observed == [
        ("https://gateway.invalid/api/editing/worker/devices/device/relay/commands", b""),
        ("https://gateway.invalid/api/editing/worker/devices/device/relay/commands/c1/response", b"VIDEO"),
    ]
