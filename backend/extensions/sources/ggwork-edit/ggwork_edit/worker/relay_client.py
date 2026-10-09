"""Outgoing native relay transport; local authorization/identity stays in callbacks."""

import asyncio

import httpx


class RelayFailure(Exception):
    def __init__(self, code="transfer_failed"):
        if code not in {"file_missing", "file_changed", "grant_denied", "transfer_failed"}:
            code = "transfer_failed"
        self.code = code
        super().__init__(code)


class RelayClient:
    def __init__(self, http, device_id, *, receive, read):
        self.http = http
        self.base = f"/api/editing/worker/devices/{device_id}/relay/commands"
        self.receive = receive
        self.read = read

    async def poll_once(self):
        response = await self.http.get(self.base, follow_redirects=False)
        response.raise_for_status()
        items = response.json()["items"]
        if not items:
            return False
        command = items[0]
        path = f"{self.base}/{command['id']}"
        data, headers = b"", {}
        try:
            if command["operation"] == "upload":
                body = await self.http.get(path + "/body", follow_redirects=False)
                body.raise_for_status()
                await asyncio.to_thread(self.receive, command, body.content)
            elif command["operation"] == "read":
                data = await asyncio.to_thread(self.read, command)
            else:
                raise RelayFailure()
        except RelayFailure as exc:
            headers["X-Relay-Error"] = exc.code
        except (OSError, ValueError):
            headers["X-Relay-Error"] = "transfer_failed"
        response = await self.http.post(path + "/response", content=data, headers=headers, follow_redirects=False)
        response.raise_for_status()
        return True

    async def run(self, stop_event, *, poll_seconds=0.25):
        while not stop_event.is_set():
            try:
                handled = await self.poll_once()
            except httpx.HTTPError:
                # Expired commands and connection loss restart access, never claim resume.
                handled = False
            if not handled:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
                except TimeoutError:
                    pass
