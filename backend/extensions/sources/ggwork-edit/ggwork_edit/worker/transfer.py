"""Explicit, bounded receive grants. A received file is not yet an admitted source."""

import asyncio
import hashlib
import json
import os

from .native import NativeWorker
from .storage import WorkerError, confined, digest, identifier, no_symlink, private_json


class Receiver:
    def __init__(self, store):
        self.store = store

    def receive(self, command, data):
        config = self.store.config()
        manifest = command["source_manifest"]
        grant_id = manifest["grant_id"]
        if grant_id not in config.get("receiving_grants", []):
            raise WorkerError("grant_denied")
        source = next((s for s in manifest["files"] if s["media_id"] == command["media_id"]), None)
        if source is None or not data or len(data) > 1024 * 1024 or len(data) != command["length"]:
            raise WorkerError("transfer_failed")
        target = confined(no_symlink(config["grants"][grant_id]), source["relative_path"])
        if not target.parent.is_dir() or target.exists():
            raise WorkerError("transfer_target_exists")
        transfer_id = identifier(command["transfer_id"])
        stage = confined(target.parent, "." + transfer_id + ".part")
        records = self.store.home / "transfers"
        records.mkdir(mode=0o700, exist_ok=True)
        record = no_symlink(records / (transfer_id + ".json"))
        identity = hashlib.sha256(json.dumps([command["task_id"], manifest, source], sort_keys=True).encode()).hexdigest()
        offset = command["offset"]
        if not record.exists():
            if offset != 0:
                raise WorkerError("transfer_offset_invalid")
            fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            private_json(record, {"identity": identity, "offset": 0})
        state = json.loads(record.read_text(encoding="utf-8"))
        if state["identity"] != identity or state["offset"] != offset or stage.stat().st_size != offset:
            raise WorkerError("transfer_offset_invalid")
        end = offset + len(data)
        if end > source["size_bytes"] or bool(command["final"]) != (end == source["size_bytes"]):
            raise WorkerError("transfer_size_invalid")
        fd = os.open(no_symlink(stage), os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(fd, "ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        private_json(record, {"identity": identity, "offset": end})
        if command["final"]:
            # This callback is on a relay-owned thread, never the heartbeat loop.
            asyncio.run(NativeWorker(self.store).probe(stage, suffix=target.suffix))
            sha = digest(stage)
            if source.get("sha256") and source["sha256"] != sha:
                raise WorkerError("source_changed")
            os.link(stage, target)  # exclusive, same filesystem, no source overwrite
            stage.unlink()
            private_json(record, {"identity": identity, "offset": end, "received_sha256": sha})
