"""Authenticated bounded HTTP byte transfer with native acknowledgments."""

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ggwork_edit.relay import CHUNK_BYTES, ERRORS
from ggwork_edit.routes import EditingRoute, owner


async def single_worker():
    try:
        valid = all(int(os.environ.get(name, "1")) == 1 for name in ("GATEWAY_WORKERS", "WEB_CONCURRENCY"))
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "relay_requires_single_gateway_worker")


class RelayResponse(StreamingResponse):
    def __init__(self, content, *, relay, transfer, **kwargs):
        super().__init__(content, **kwargs)
        self.relay = relay
        self.transfer = transfer

    async def __call__(self, scope, receive, send):
        async def bounded_send(message):
            async with asyncio.timeout(self.relay.idle_timeout):
                await send(message)

        try:
            await super().__call__(scope, receive, bounded_send)
        finally:
            self.relay.remove(self.transfer)


class Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_id: str = Field(min_length=1, max_length=200)


async def read_body(request):
    data = bytearray()
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(data) + len(chunk) > CHUNK_BYTES:
                    raise HTTPException(413, "chunk_too_large")
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "chunk_body_timeout") from None
    return bytes(data)


async def bounded_body(request, relay, transfer):
    relay.lookup(transfer.owner, transfer.id)
    reader = asyncio.create_task(read_body(request))
    transfer.readers.add(reader)
    try:
        return await reader
    except asyncio.CancelledError:
        if transfer.cancelled:
            raise HTTPException(404, "transfer_restart_required") from None
        raise
    finally:
        transfer.readers.discard(reader)
        if transfer.cancelled:
            relay.remove(transfer)


def build_relay_router(relay):
    router = APIRouter(prefix="/api/editing", route_class=EditingRoute, dependencies=[Depends(single_worker)])

    @router.post("/tasks/{task_id}/uploads")
    async def create(task_id: str, payload: Upload, request: Request):
        return (await relay.upload(owner(request), task_id, payload.media_id)).status()

    @router.delete("/uploads/{transfer_id}")
    async def cancel(transfer_id: str, request: Request):
        transfer = relay.lookup(owner(request), transfer_id)
        if "media_id" not in transfer.metadata:
            raise HTTPException(404, "upload_not_found")
        relay.remove(transfer)
        return {"state": "cancelled"}

    @router.put("/uploads/{transfer_id}")
    async def chunk(transfer_id: str, request: Request, offset: int = Query(ge=0)):
        transfer = relay.lookup(owner(request), transfer_id)
        if "media_id" not in transfer.metadata or transfer.busy or transfer.offset != offset:
            raise HTTPException(409, "offset_or_transfer_conflict")
        transfer.busy = True
        try:
            await relay.check(transfer)
            data = await bounded_body(request, relay, transfer)
            if not data or offset + len(data) > transfer.size:
                raise HTTPException(400, "invalid_chunk_size")
            final = offset + len(data) == transfer.size
            await relay.exchange(transfer, "upload", offset, len(data), data, final)
            transfer.offset += len(data)
            result = transfer.status()
            if final:
                relay.remove(transfer)
            return result
        except BaseException:
            relay.remove(transfer)
            raise
        finally:
            transfer.busy = False

    @router.get("/tasks/{task_id}/outputs/{output_id}/access")
    async def access(task_id: str, output_id: str, request: Request):
        user = owner(request)
        task, result = await relay.output(user, task_id, output_id)
        transfer = None
        try:
            await relay.live(user, task["device_id"], task_id)
            transfer = relay.output_transfer(user, task, output_id, result)
            await relay.exchange(transfer, "read", 0, 1)
            return {"access_status": "available"}
        except HTTPException as exc:
            if exc.detail in {
                "device_offline",
                "device_revoked",
                "file_missing",
                "file_changed",
                "grant_denied",
                "transfer_failed",
                "transfer_timeout_restart_required",
                "relay_capacity",
            }:
                return {"access_status": exc.detail}
            raise
        finally:
            if transfer:
                relay.remove(transfer)

    @router.get("/tasks/{task_id}/outputs/{output_id}/content")
    async def content(task_id: str, output_id: str, request: Request, download: bool = False):
        user = owner(request)
        task, result = await relay.output(user, task_id, output_id)
        await relay.live(user, task["device_id"], task_id)
        size = result["size_bytes"]
        start, end = 0, size - 1
        range_header = request.headers.get("range")
        if range_header:
            try:
                unit, value = range_header.split("=", 1)
                first, last = value.split("-", 1)
                if unit != "bytes" or not (first or last) or "," in value:
                    raise ValueError
                if not first:
                    suffix = int(last)
                    if suffix <= 0:
                        raise ValueError
                    start = max(0, size - suffix)
                else:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
            except ValueError:
                raise HTTPException(416, "range_not_satisfiable", headers={"Content-Range": f"bytes */{size}"}) from None
        transfer = relay.output_transfer(user, task, output_id, result)
        try:
            first = await relay.exchange(transfer, "read", start, min(CHUNK_BYTES, end - start + 1))
        except BaseException:
            relay.remove(transfer)
            raise

        async def chunks():
            nonlocal first
            try:
                yield first
                offset = start + len(first)
                first = b""
                while offset <= end:
                    if await request.is_disconnected():
                        return
                    data = await relay.exchange(transfer, "read", offset, min(CHUNK_BYTES, end - offset + 1))
                    yield data
                    offset += len(data)
            finally:
                relay.remove(transfer)

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="clip.mp4"' if download else 'inline; filename="clip.mp4"',
            "X-Content-Type-Options": "nosniff",
        }
        if range_header:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return RelayResponse(chunks(), relay=relay, transfer=transfer, status_code=206 if range_header else 200, media_type="video/mp4", headers=headers)

    return router


def build_relay_worker_router(relay):
    router = APIRouter(prefix="/api/editing/worker", route_class=EditingRoute, dependencies=[Depends(single_worker)])

    @router.get("/devices/{device_id}/relay/commands")
    async def commands(device_id: str, request: Request):
        user = owner(request, device_id=device_id)
        # Recheck credential revocation even when there are no pending commands.
        devices = await relay.service.repository(user).devices()
        if not any(d["id"] == device_id and not d["revoked"] for d in devices):
            raise HTTPException(403, "device_revoked")
        for item in list(relay.commands.values()):
            transfer = item["transfer"]
            if transfer.owner == user and transfer.device == device_id and not item["delivered"]:
                item["delivered"] = True
                await relay.check(transfer)
                return {"items": [item["public"]]}
        return {"items": []}

    @router.get("/devices/{device_id}/relay/commands/{command_id}/body")
    async def body(device_id: str, command_id: str, request: Request):
        item = await relay.command(owner(request, device_id=device_id), device_id, command_id)
        if item["public"]["operation"] != "upload":
            raise HTTPException(409, "wrong_operation")
        await relay.check(item["transfer"])
        return Response(item["data"], media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @router.post("/devices/{device_id}/relay/commands/{command_id}/response")
    async def respond(device_id: str, command_id: str, request: Request):
        item = await relay.command(owner(request, device_id=device_id), device_id, command_id)
        if item["responding"]:
            raise HTTPException(409, "response_in_progress")
        item["responding"] = True
        data = await bounded_body(request, relay, item["transfer"])
        error = request.headers.get("x-relay-error")
        if error and error not in ERRORS:
            raise HTTPException(400, "invalid_relay_error")
        expected = item["public"]["length"] if item["public"]["operation"] == "read" else 0
        if not error and len(data) != expected:
            error = "transfer_failed"
        if item["future"].done():
            raise HTTPException(409, "command_expired")
        item["future"].set_result((error, data if not error else b""))
        return {"acknowledged": True}

    return router
