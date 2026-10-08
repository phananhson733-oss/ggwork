"""Bounded process-local relay. No media files or restart/resume claims."""

import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import HTTPException

CHUNK_BYTES = 1024 * 1024
ERRORS = {"file_missing", "file_changed", "grant_denied", "transfer_failed"}


def selection_identity(manifest):
    if not manifest:
        return None
    keys = ("media_id", "name", "episode", "relative_path", "size_bytes")
    return manifest["version"], manifest["grant_id"], [[source[k] for k in keys] for source in manifest["files"]]


@dataclass
class Transfer:
    owner: str
    device: str
    task: str
    metadata: dict
    size: int
    id: str = field(default_factory=lambda: uuid4().hex)
    offset: int = 0
    busy: bool = False
    cancelled: bool = False
    readers: set = field(default_factory=set)
    touched: float = field(default_factory=time.monotonic)

    def status(self):
        return {
            "transfer_id": self.id,
            "offset": self.offset,
            "size_bytes": self.size,
            "state": "received" if self.offset == self.size else "receiving",
            "chunk_bytes": CHUNK_BYTES,
        }


class Relay:
    def __init__(self, service, *, timeout=30, idle_timeout=120, per_device=4, total=64):
        self.service = service
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.per_device = per_device
        self.total = total
        self.transfers = {}
        self.commands = {}

    async def start(self, deps):
        pass

    async def stop(self):
        readers = [reader for transfer in self.transfers.values() for reader in transfer.readers]
        for transfer in list(self.transfers.values()):
            self.remove(transfer)
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)

    def sweep(self):
        for transfer in list(self.transfers.values()):
            if not transfer.busy and time.monotonic() - transfer.touched > self.idle_timeout:
                self.remove(transfer)

    def remove(self, transfer):
        transfer.cancelled = True
        for reader in transfer.readers:
            reader.cancel()
        # Keep quota reserved until every buffered request-body reader exits.
        if not transfer.readers:
            self.transfers.pop(transfer.id, None)
        for command_id, command in list(self.commands.items()):
            if command["transfer"] is transfer:
                self.commands.pop(command_id, None)
                if not command["future"].done():
                    command["future"].set_result(("transfer_failed", b""))

    async def live(self, owner, device, task_id):
        repo = self.service.repository(owner)
        devices = await repo.devices()
        device_info = next((d for d in devices if d["id"] == device), None)
        if device_info is None or device_info["revoked"]:
            raise HTTPException(403, "device_revoked")
        if not device_info["online"]:
            raise HTTPException(409, "device_offline")
        return await repo.get_worker_task(device, task_id)

    def reserve(self, owner, device, task_id, metadata, size):
        self.sweep()
        if len(self.transfers) >= self.total or sum(t.device == device for t in self.transfers.values()) >= self.per_device:
            raise HTTPException(429, "relay_capacity")
        transfer = Transfer(owner, device, task_id, metadata, size)
        self.transfers[transfer.id] = transfer
        return transfer

    async def upload(self, owner, task_id, media_id):
        task = await self.service.repository(owner).get_task(task_id)
        await self.live(owner, task["device_id"], task_id)
        manifest = task["source_manifest"]
        if task["status"] != "waiting" or task["manifest_frozen"] or not manifest:
            raise HTTPException(409, "selection_not_receiving")
        source = next((s for s in manifest["files"] if s["media_id"] == media_id), None)
        if not source or source["state"] == "verified":
            raise HTTPException(409, "selection_not_receiving")
        self.sweep()
        if any(t.owner == owner and t.task == task_id and t.metadata.get("media_id") == media_id for t in self.transfers.values()):
            raise HTTPException(409, "upload_already_active")
        return self.reserve(owner, task["device_id"], task_id, {"media_id": media_id, "source_manifest": manifest}, source["size_bytes"])

    async def output(self, owner, task_id, output_id):
        task = await self.service.repository(owner).get_task(task_id)
        result = next((o["result"] for o in task["outputs"] if o["id"] == output_id and o["status"] == "completed"), None)
        if not result:
            raise HTTPException(404, "output_not_available")
        return task, result

    def output_transfer(self, owner, task, output_id, result):
        transfer = self.reserve(
            owner,
            task["device_id"],
            task["id"],
            {"output_id": output_id, **{k: result[k] for k in ("artifact_id", "sha256", "size_bytes")}},
            result["size_bytes"],
        )
        transfer.busy = True
        return transfer

    def lookup(self, owner, transfer_id):
        self.sweep()
        transfer = self.transfers.get(transfer_id)
        if transfer is None or transfer.owner != owner or transfer.cancelled:
            raise HTTPException(404, "transfer_restart_required")
        return transfer

    async def check(self, transfer):
        self.lookup(transfer.owner, transfer.id)
        task = await self.live(transfer.owner, transfer.device, transfer.task)
        self.lookup(transfer.owner, transfer.id)
        if "media_id" in transfer.metadata:
            if (
                task["status"] != "waiting"
                or task["manifest_frozen"]
                or selection_identity(task["source_manifest"]) != selection_identity(transfer.metadata["source_manifest"])
            ):
                raise HTTPException(409, "selection_changed")
        else:
            result = next((o["result"] for o in task["outputs"] if o["id"] == transfer.metadata["output_id"]), None)
            if not result or any(result[k] != transfer.metadata[k] for k in ("artifact_id", "sha256", "size_bytes")):
                raise HTTPException(409, "file_changed")
        return task

    async def exchange(self, transfer, operation, offset, length, data=b"", final=False):
        await self.check(transfer)
        command_id = uuid4().hex
        public = {
            "id": command_id,
            "operation": operation,
            "task_id": transfer.task,
            "transfer_id": transfer.id,
            "offset": offset,
            "length": length,
            **transfer.metadata,
        }
        if operation == "upload":
            public["final"] = final
        future = asyncio.get_running_loop().create_future()
        self.commands[command_id] = {"public": public, "transfer": transfer, "data": data, "future": future, "delivered": False, "responding": False}
        try:
            error, result = await asyncio.wait_for(future, self.timeout)
            if error:
                raise HTTPException(409, error)
            # Native may publish its verified manifest before acknowledging final upload.
            task = await self.live(transfer.owner, transfer.device, transfer.task)
            self.lookup(transfer.owner, transfer.id)
            if operation == "upload":
                original = transfer.metadata["source_manifest"]
                current = task["source_manifest"]
                if task["status"] in {"stopping", "stopped", "failed"} or selection_identity(current) != selection_identity(original):
                    raise HTTPException(409, "selection_changed")
            return result
        except TimeoutError:
            raise HTTPException(504, "transfer_timeout_restart_required") from None
        finally:
            self.commands.pop(command_id, None)
            transfer.touched = time.monotonic()

    async def command(self, owner, device, command_id):
        item = self.commands.get(command_id)
        if item is None or item["transfer"].owner != owner or item["transfer"].device != device:
            raise HTTPException(404, "command_expired")
        await self.live(owner, device, item["transfer"].task)
        return item
