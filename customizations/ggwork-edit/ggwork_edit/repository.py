"""Durable owner-scoped transactions shared by every editing entry point.

Each owner's mutations serialize on PostgreSQL advisory transaction locks or
SQLite's writer lock. The complete task aggregate and immutable receipts commit
together, so stop/publication and competing retries have one database order.
"""

import copy
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, insert, select, text, update

from ggwork_edit.models import records


class ConflictError(ValueError):
    pass


def stamp():
    return datetime.now(UTC).isoformat(timespec="microseconds")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class EditingRepository:
    def __init__(self, service, owner):
        if not owner or owner in ("default", "system:shared") or len(owner) > 128:
            raise PermissionError("Authenticated owner required")
        self.service = service
        self.owner = owner

    @asynccontextmanager
    async def transaction(self, *, write=True):
        async with self.service.session_factory() as session:
            async with session.begin():
                if not write:
                    yield session
                    return
                if session.bind.dialect.name == "sqlite":
                    await session.execute(text("BEGIN IMMEDIATE"))
                else:
                    key = int.from_bytes(hashlib.sha256(("ggwe:" + self.owner).encode()).digest()[:8], signed=True)
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                yield session

    def where(self, kind, identity):
        return (records.c.owner_id == self.owner, records.c.kind == kind, records.c.id == identity)

    async def read(self, session, kind, identity, *, required=True):
        value = (await session.execute(select(records.c.data).where(*self.where(kind, identity)))).scalar_one_or_none()
        if value is None and required:
            raise LookupError("Not found")
        return copy.deepcopy(value)

    async def save(self, session, kind, identity, value, *, new=False):
        if new:
            await session.execute(insert(records).values(owner_id=self.owner, kind=kind, id=identity, data=value))
        else:
            await session.execute(update(records).where(*self.where(kind, identity)).values(data=value))

    async def receipt(self, session, key, payload):
        old = await self.read(session, "receipt", key, required=False)
        if old and old["digest"] != digest(payload):
            raise ConflictError("Idempotency key already used with different input")
        return old

    async def create_task(self, payload, *, source_thread_id=None):
        if payload.source_thread_id and payload.source_thread_id != source_thread_id:
            raise PermissionError("Source conversation must come from authenticated runtime")
        data = payload.model_dump(mode="json")
        data["source_thread_id"] = source_thread_id
        async with self.transaction() as session:
            receipt = await self.receipt(session, "create:" + digest(payload.request_id), data)
            if receipt:
                return await self.view(session, await self.read(session, "task", receipt["task_id"]))
            if payload.parent_task_id:
                await self.read(session, "task", payload.parent_task_id)
            if payload.device_id:
                await self.read(session, "device", payload.device_id)
            self.browser_manifest(data["source_manifest"])
            now = stamp()
            task = {
                "id": uuid4().hex,
                "title": payload.title,
                "requirements": data["requirements"],
                "device_id": payload.device_id,
                "source_thread_id": source_thread_id,
                "parent_task_id": payload.parent_task_id,
                "created_at": now,
                "updated_at": now,
                "status": "waiting",
                "stage": "preparing",
                "result": "pending",
                "source_manifest": data["source_manifest"],
                "native_preparation_error": None,
                "manifest_frozen": False,
                "attempt": None,
                "fence": 0,
                "source_directory": data["source_directory"],
                "plan": None,
                "plan_confirmed": False,
                "outputs": [{"id": f"out-{i + 1}", "status": "pending", "result": None, "error": None} for i in range(payload.requirements.output_count)],
            }
            await self.save(session, "task", task["id"], task, new=True)
            await self.save(session, "receipt", "create:" + digest(payload.request_id), {"digest": digest(data), "task_id": task["id"]}, new=True)
            return await self.view(session, task)

    @staticmethod
    def browser_manifest(manifest):
        if manifest and any(f["state"] != "selected" or f["sha256"] is not None or f["duration_seconds"] is not None for f in manifest["files"]):
            raise ConflictError("Only the authenticated device can verify sources")

    async def view(self, session, task):
        result = copy.deepcopy(task)
        result.pop("fence", None)
        result.setdefault("native_preparation_error", None)
        result["requested_count"] = task["requirements"]["output_count"]
        result["completed_count"] = sum(o["status"] == "completed" for o in task["outputs"])
        result["preparation_reasons"] = await self.preparation_reasons(session, task) if task["status"] == "waiting" else []
        device = self.device_view(await self.read(session, "device", task["device_id"])) if task["device_id"] else None
        result["device_status"] = "unassigned" if device is None else "revoked" if device["revoked"] else "online" if device["online"] else "offline"
        result["access_status"] = "device_revoked" if device and device["revoked"] else "unchecked" if device and device["online"] else "device_offline"
        result["available_actions"] = ["stop"] if task["status"] in ("waiting", "queued", "running", "awaiting_plan") else []
        if (
            task["status"] in ("failed", "partial", "stopped")
            and task["manifest_frozen"]
            and any(o["status"] in ("failed", "stopped") for o in task["outputs"])
        ):
            result["available_actions"].append("retry")
        if task["status"] == "awaiting_plan":
            result["available_actions"].append("confirm_plan")
        for output in result["outputs"]:
            output["access_status"] = result["access_status"]
        return result

    async def get_task(self, task_id):
        async with self.transaction(write=False) as session:
            return await self.view(session, await self.read(session, "task", task_id))

    async def list_tasks(self, *, limit=100, offset=0):
        return (await self.list_tasks_page(limit=limit, offset=offset))["items"]

    async def list_tasks_page(self, *, limit=100, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination")
        async with self.transaction(write=False) as session:
            conditions = (records.c.owner_id == self.owner, records.c.kind == "task")
            total = (await session.execute(select(func.count()).select_from(records).where(*conditions))).scalar_one()
            query = select(records.c.data).where(*conditions)
            query = query.order_by(records.c.data["created_at"].as_string().desc(), records.c.id).limit(limit).offset(offset)
            items = [await self.view(session, task) for task in (await session.execute(query)).scalars().all()]
            return {
                "items": items,
                "limit": limit,
                "offset": offset,
                "total": total,
                "next_offset": offset + len(items) if offset + len(items) < total else None,
            }

    async def register_device(self, name):
        import secrets

        from ggwork_edit.models import credentials

        token = "ggwe_" + secrets.token_urlsafe(40)
        device = {
            "id": uuid4().hex,
            "name": name,
            "revoked": False,
            "platform": "unsupported",
            "ready": False,
            "reasons": ["preparation_required"],
            "grants": [],
            "worker_version": None,
            "last_seen_at": None,
        }
        async with self.transaction() as session:
            await self.save(session, "device", device["id"], device, new=True)
            await session.execute(insert(credentials).values(digest=hashlib.sha256(token.encode()).hexdigest(), owner_id=self.owner, device_id=device["id"]))
        return {"device": self.device_view(device), "token": token}

    @staticmethod
    def device_view(device):
        value = copy.deepcopy(device)
        value["online"] = bool(
            not value["revoked"] and value["last_seen_at"] and datetime.fromisoformat(value["last_seen_at"]) > datetime.now(UTC) - timedelta(seconds=90)
        )
        return value

    async def live_device(self, session, device_id):
        device = await self.read(session, "device", device_id)
        if device["revoked"]:
            raise PermissionError("Device revoked")
        return device

    async def devices(self):
        async with self.transaction(write=False) as session:
            rows = (await session.execute(select(records.c.data).where(records.c.owner_id == self.owner, records.c.kind == "device"))).scalars().all()
            return [self.device_view(d) for d in rows]

    async def revoke_device(self, device_id):
        async with self.transaction() as session:
            device = await self.read(session, "device", device_id)
            device["revoked"] = True
            await self.save(session, "device", device_id, device)
            return self.device_view(device)

    async def device_heartbeat(self, device_id, payload):
        async with self.transaction() as session:
            device = await self.live_device(session, device_id)
            device.update(payload.model_dump(mode="json"))
            device["ready"] = payload.ready and payload.platform == "darwin-arm64" and bool(payload.grants)
            device["last_seen_at"] = stamp()
            await self.save(session, "device", device_id, device)
            # Preparation completed later must continue an existing intent exactly once.
            tasks = (await session.execute(select(records.c.data).where(records.c.owner_id == self.owner, records.c.kind == "task"))).scalars().all()
            for task in tasks:
                if task["device_id"] == device_id and task["status"] == "waiting":
                    await self.admit(session, task)
                    await self.save(session, "task", task["id"], task)
            return self.device_view(device)

    async def enabled(self, session):
        settings = await self.read(session, "settings", "owner", required=False)
        return settings is None or settings["skill_enabled"]

    async def capabilities(self):
        async with self.transaction(write=False) as session:
            enabled = await self.enabled(session)
            reasons = ([] if self.service.hook_available else ["planner_unavailable"]) + ([] if enabled else ["skill_disabled"])
            return {
                "profiles": [{"id": p, "available": not reasons, "reasons": reasons} for p in ("highlight", "hook")],
                "skill_enabled": enabled,
                "limits": {
                    "max_sources": 500,
                    "max_outputs": 10,
                    "min_duration_seconds": 5,
                    "max_duration_seconds": 180,
                    "device_offline_seconds": 90,
                    "attempt_lease_seconds": 90,
                },
            }

    async def set_enabled(self, enabled):
        async with self.transaction() as session:
            old = await self.read(session, "settings", "owner", required=False)
            await self.save(session, "settings", "owner", {"skill_enabled": enabled}, new=old is None)
        return await self.capabilities()

    async def preparation_reasons(self, session, task):
        reasons = [task["native_preparation_error"]] if task.get("native_preparation_error") else []
        if not self.service.hook_available:
            reasons.append("planner_unavailable")
        if not await self.enabled(session):
            reasons.append("skill_disabled")
        if not task["device_id"]:
            reasons.append("device_required")
        else:
            device = self.device_view(await self.read(session, "device", task["device_id"]))
            if device["revoked"]:
                reasons.append("device_revoked")
            elif not device["online"]:
                reasons.append("device_offline")
            if not device["ready"]:
                reasons.append("device_not_ready")
            manifest = task["source_manifest"]
            if manifest and manifest["grant_id"] not in device["grants"]:
                reasons.append("directory_not_authorized")
        if not task["source_manifest"]:
            reasons.append("sources_required")
        elif any(f["state"] != "verified" for f in task["source_manifest"]["files"]):
            reasons.append("sources_unverified")
        return reasons

    async def admit(self, session, task):
        if task["status"] == "waiting" and not await self.preparation_reasons(session, task):
            task.update(status="queued", stage="queued", manifest_frozen=True, updated_at=stamp())

    async def prepare(self, task_id, payload):
        manifest = payload.source_manifest.model_dump(mode="json") if payload.source_manifest else None
        self.browser_manifest(manifest)
        async with self.transaction() as session:
            task = await self.read(session, "task", task_id)
            if task["manifest_frozen"] or task["status"] != "waiting":
                raise ConflictError("Source manifest is frozen")
            await self.live_device(session, payload.device_id)
            if manifest and task["source_manifest"] and manifest["version"] <= task["source_manifest"]["version"]:
                raise ConflictError("Selection revision must increase")
            if task["device_id"] and task["device_id"] != payload.device_id and task["source_manifest"]:
                # Verification is bound to the original Mac; a new Mac must verify again.
                for source in task["source_manifest"]["files"]:
                    source.update(state="selected", sha256=None, duration_seconds=None)
            if manifest:
                task.update(source_manifest=manifest, source_directory=None)
            elif payload.source_directory:
                directory = payload.source_directory.model_dump(mode="json")
                if directory != task.get("source_directory"):
                    task.update(source_directory=directory, source_manifest=None)
            task.update(device_id=payload.device_id, native_preparation_error=None, updated_at=stamp())
            await self.save(session, "task", task_id, task)
            return await self.view(session, task)

    async def verify_manifest(self, device_id, task_id, manifest):
        incoming = manifest.model_dump(mode="json")
        async with self.transaction() as session:
            await self.live_device(session, device_id)
            task = await self.read(session, "task", task_id)
            if task["device_id"] != device_id:
                raise PermissionError("Wrong device")
            old = task["source_manifest"]
            if task["manifest_frozen"]:
                if old != incoming:
                    raise ConflictError("Source manifest is frozen")
                return await self.view(session, task)

            def identity(m):
                return {
                    "version": m["version"],
                    "grant_id": m["grant_id"],
                    "files": [{k: f[k] for k in ("media_id", "name", "episode", "relative_path", "size_bytes")} for f in m["files"]],
                }

            if old is None or identity(old) != identity(incoming):
                raise ConflictError("Verification must cover exact selected source set")
            for before, after in zip(old["files"], incoming["files"], strict=True):
                if before["state"] == "verified" and before != after:
                    raise ConflictError("Verified source changed; explicitly revise selection")
            task.update(source_manifest=incoming, native_preparation_error=None, updated_at=stamp())
            await self.admit(session, task)
            await self.save(session, "task", task_id, task)
            return await self.view(session, task)

    async def claim(self, device_id, request_id):
        async with self.transaction() as session:
            device = self.device_view(await self.live_device(session, device_id))
            receipt = await self.receipt(session, "claim:" + digest(request_id), {"device_id": device_id})
            if receipt:
                task = await self.read(session, "task", receipt["task_id"])
                if task["attempt"]["id"] != receipt["attempt_id"] or task["status"] not in ("running", "awaiting_plan", "stopping"):
                    return {"task": None, "attempt": None}
                return {"task": await self.view(session, task), "attempt": task["attempt"]}
            if not device["online"] or not device["ready"] or not self.service.hook_available or not await self.enabled(session):
                return {"task": None, "attempt": None}
            tasks = (await session.execute(select(records.c.data).where(records.c.owner_id == self.owner, records.c.kind == "task"))).scalars().all()
            if any(t["device_id"] == device_id and t["status"] in ("running", "awaiting_plan", "stopping") for t in tasks):
                return {"task": None, "attempt": None}
            for task in sorted(tasks, key=lambda t: t["created_at"]):
                if task["device_id"] == device_id and task["status"] == "queued" and not await self.preparation_reasons(session, task):
                    task["fence"] += 1
                    attempt = {
                        "id": uuid4().hex,
                        "fence": task["fence"],
                        "output_ids": [o["id"] for o in task["outputs"] if o["status"] == "pending"],
                        "stage": task.get("retry_stage", "transcribing"),
                        "lease_expires_at": (datetime.now(UTC) + timedelta(seconds=90)).isoformat(),
                    }
                    task.update(status="running", stage=attempt["stage"], attempt=attempt, updated_at=stamp())
                    await self.save(session, "task", task["id"], task)
                    await self.save(
                        session,
                        "receipt",
                        "claim:" + digest(request_id),
                        {"digest": digest({"device_id": device_id}), "task_id": task["id"], "attempt_id": attempt["id"]},
                        new=True,
                    )
                    return {"task": await self.view(session, task), "attempt": attempt}
            return {"task": None, "attempt": None}

    async def worker_task(self, session, device_id, task_id):
        await self.live_device(session, device_id)
        task = await self.read(session, "task", task_id)
        if task["device_id"] != device_id:
            raise PermissionError("Wrong device")
        return task

    @staticmethod
    def check_attempt(task, attempt_id, fence):
        attempt = task["attempt"]
        if attempt is None or attempt["id"] != attempt_id or attempt["fence"] != fence:
            raise ConflictError("Stale execution attempt")
        return attempt

    async def report(self, device_id, task_id, payload):
        data = payload.model_dump(mode="json")
        key = "event:" + digest([task_id, payload.attempt_id, payload.event_id])
        async with self.transaction() as session:
            task = await self.worker_task(session, device_id, task_id)
            old = await self.receipt(session, key, data)
            if old:
                return {"task": await self.view(session, task), "stop_requested": task["status"] == "stopping", "fence": task["fence"]}
            attempt = self.check_attempt(task, payload.attempt_id, payload.fence)
            if task["status"] not in ("running", "awaiting_plan", "stopping"):
                raise ConflictError("Attempt is terminal")
            stopping = task["status"] == "stopping"
            if stopping and payload.kind not in ("heartbeat", "stopped"):
                raise ConflictError("Stop accepted; publication fenced")
            if payload.kind == "heartbeat":
                attempt["lease_expires_at"] = (datetime.now(UTC) + timedelta(seconds=90)).isoformat()
            elif payload.kind == "stopped":
                if not stopping:
                    raise ConflictError("No accepted stop to acknowledge")
                for output in task["outputs"]:
                    if output["id"] in attempt["output_ids"] and output["status"] not in ("completed", "failed"):
                        output["status"] = "stopped"
                task.update(status="stopped", stage="stopped", result="partial" if any(o["status"] == "completed" for o in task["outputs"]) else "stopped")
            else:
                if datetime.fromisoformat(attempt["lease_expires_at"]) <= datetime.now(UTC):
                    raise ConflictError("Lease expired; heartbeat before reporting")
                if not self.service.hook_available or not await self.enabled(session):
                    raise ConflictError("Editing capability disabled")
                if payload.kind == "stage":
                    if payload.stage is None:
                        raise ConflictError("Stage required")
                    if payload.stage == "awaiting_plan" and task.get("plan_confirmed"):
                        raise ConflictError("Plan already confirmed")
                    if payload.stage == "awaiting_plan" and not task.get("plan"):
                        raise ConflictError("No stored plan")
                    if task["requirements"]["review_plan"] and payload.stage in ("rendering", "verifying") and not task.get("plan_confirmed", False):
                        raise ConflictError("Plan confirmation required")
                    task["stage"] = payload.stage
                    attempt["stage"] = payload.stage
                    task["status"] = "awaiting_plan" if payload.stage == "awaiting_plan" else "running"
                elif payload.kind in ("output", "failure"):
                    if payload.output_id is None and payload.kind == "failure":
                        for output in task["outputs"]:
                            if output["id"] in attempt["output_ids"] and output["status"] != "completed":
                                output.update(status="failed", error=payload.error or "stage_failed")
                        task.update(
                            status="partial" if any(o["status"] == "completed" for o in task["outputs"]) else "failed",
                            result="partial" if any(o["status"] == "completed" for o in task["outputs"]) else "failed",
                        )
                    else:
                        output = next((o for o in task["outputs"] if o["id"] == payload.output_id), None)
                        if output is None or output["id"] not in attempt["output_ids"]:
                            raise ConflictError("Output is outside this attempt")
                        if output["status"] == "completed":
                            raise ConflictError("Delivered output is immutable")
                        if payload.kind == "output":
                            if payload.result is None:
                                raise ConflictError("Verified result required")
                            if task["requirements"]["review_plan"] and not task.get("plan_confirmed", False):
                                raise ConflictError("Plan confirmation required")
                            result = payload.result.model_dump(mode="json")
                            width, height = (int(x) for x in task["requirements"]["aspect_ratio"].split(":"))
                            if abs(result["width"] / result["height"] - width / height) > 0.02:
                                raise ConflictError("Output aspect does not match request")
                            if abs(result["duration_seconds"] - task["requirements"]["duration_seconds"]) > 1:
                                raise ConflictError("Output duration does not match request")
                            if any(o["result"] and o["result"]["artifact_id"] == result["artifact_id"] for o in task["outputs"]):
                                raise ConflictError("Artifact identity already delivered")
                            output.update(status="completed", result=result, error=None)
                        else:
                            output.update(status="failed", error=payload.error or "output_failed")
                elif payload.kind == "complete":
                    if any(o["status"] not in ("completed", "failed", "stopped") for o in task["outputs"]):
                        raise ConflictError("Each requested output needs an explicit outcome")
                    count = sum(o["status"] == "completed" for o in task["outputs"])
                    outcome = "completed" if count == len(task["outputs"]) else "partial" if count else "failed"
                    task.update(status=outcome, stage="finished", result=outcome)
            task["updated_at"] = stamp()
            await self.save(session, "task", task_id, task)
            await self.save(session, "receipt", key, {"digest": digest(data)}, new=True)
            return {"task": await self.view(session, task), "stop_requested": task["status"] == "stopping", "fence": task["fence"]}

    async def stop_task(self, task_id):
        async with self.transaction() as session:
            task = await self.read(session, "task", task_id)
            if task["status"] in ("running", "awaiting_plan"):
                task.update(status="stopping", fence=task["fence"] + 1, updated_at=stamp())
            elif task["status"] in ("waiting", "queued"):
                task.update(
                    status="stopped",
                    stage="stopped",
                    result="partial" if any(o["status"] == "completed" for o in task["outputs"]) else "stopped",
                    updated_at=stamp(),
                )
                for output in task["outputs"]:
                    if output["status"] == "pending":
                        output["status"] = "stopped"
            await self.save(session, "task", task_id, task)
            return await self.view(session, task)

    async def retry(self, task_id, payload):
        data = {"task_id": task_id, **payload.model_dump(mode="json")}
        key = "retry:" + digest(payload.request_id)
        async with self.transaction() as session:
            task = await self.read(session, "task", task_id)
            if await self.receipt(session, key, data):
                return await self.view(session, task)
            if task["status"] not in ("failed", "partial", "stopped"):
                raise ConflictError("Retry requires a terminal failed or stopped scope")
            if not task["manifest_frozen"]:
                raise ConflictError("Create a new prepared request")
            ids = payload.output_ids
            if payload.stage:
                if ids or any(o["status"] == "completed" for o in task["outputs"]):
                    raise ConflictError("Stage retry cannot replace delivered outputs")
                ids = [o["id"] for o in task["outputs"]]
                task.update(plan=None, plan_confirmed=False)
                task.pop("plan_attempt_id", None)
            if not ids or len(set(ids)) != len(ids):
                raise ConflictError("Explicit failed output scope required")
            outputs = {o["id"]: o for o in task["outputs"]}
            if any(i not in outputs or outputs[i]["status"] not in ("failed", "stopped") for i in ids):
                raise ConflictError("Only failed or stopped outputs may be retried")
            for identity in ids:
                outputs[identity].update(status="pending", error=None)
            task.update(status="queued", stage="queued", result="pending", retry_stage=payload.stage or "rendering", updated_at=stamp())
            await self.save(session, "task", task_id, task)
            await self.save(session, "receipt", key, {"digest": digest(data)}, new=True)
            return await self.view(session, task)

    async def preparations(self, device_id):
        async with self.transaction(write=False) as session:
            await self.live_device(session, device_id)
            tasks = (await session.execute(select(records.c.data).where(records.c.owner_id == self.owner, records.c.kind == "task"))).scalars().all()
            return [await self.view(session, t) for t in tasks if t["device_id"] == device_id and t["status"] == "waiting"]

    async def discover(self, device_id, task_id, manifest):
        incoming = manifest.model_dump(mode="json")
        self.browser_manifest(incoming)
        async with self.transaction() as session:
            task = await self.worker_task(session, device_id, task_id)
            directory = task.get("source_directory")
            if not directory or task["status"] != "waiting" or task["manifest_frozen"]:
                raise ConflictError("No pending directory discovery")
            if directory["grant_id"] != incoming["grant_id"]:
                raise ConflictError("Discovery grant differs from selection")
            prefix = directory["relative_path"].rstrip("/")
            if prefix != "." and any(not f["relative_path"].startswith(prefix + "/") for f in incoming["files"]):
                raise ConflictError("Discovered source is outside selected directory")
            if task["source_manifest"] is not None:
                if task["source_manifest"] != incoming:
                    raise ConflictError("Discovered selection already fixed; explicitly revise selection")
            else:
                task["source_manifest"] = incoming
            task.update(native_preparation_error=None, updated_at=stamp())
            await self.save(session, "task", task_id, task)
            return await self.view(session, task)

    async def preparation_error(self, device_id, task_id, payload):
        async with self.transaction() as session:
            task = await self.worker_task(session, device_id, task_id)
            if task["status"] != "waiting" or task["manifest_frozen"]:
                raise ConflictError("Preparation is already admitted or terminal")
            task.update(native_preparation_error=payload.error, updated_at=stamp())
            await self.save(session, "task", task_id, task)
            return await self.view(session, task)

    async def get_worker_task(self, device_id, task_id):
        async with self.transaction(write=False) as session:
            return await self.view(session, await self.worker_task(session, device_id, task_id))

    async def store_plan(self, device_id, task_id, attempt_id, fence, plan):
        # Trusted planner validates its structured plan before this persistence seam.
        # No worker-facing route accepts arbitrary plan dictionaries.
        from ggwork_edit.contracts import StrictInput

        StrictInput.storable(plan)
        json.dumps(plan, allow_nan=False)
        async with self.transaction() as session:
            task = await self.worker_task(session, device_id, task_id)
            self.check_attempt(task, attempt_id, fence)
            if task["status"] not in ("running", "awaiting_plan") or task["fence"] != fence:
                raise ConflictError("Plan attempt is no longer active")
            if not self.service.hook_available or not await self.enabled(session):
                raise ConflictError("Editing capability disabled")
            if task.get("plan") is not None and task.get("plan_attempt_id") == attempt_id:
                if task["plan"] != plan:
                    raise ConflictError("Attempt plan is immutable")
                return await self.view(session, task)
            task.update(plan=copy.deepcopy(plan), plan_attempt_id=attempt_id, plan_confirmed=not task["requirements"]["review_plan"], updated_at=stamp())
            if task["requirements"]["review_plan"]:
                task.update(status="awaiting_plan", stage="awaiting_plan")
            await self.save(session, "task", task_id, task)
            return await self.view(session, task)

    async def confirm_plan(self, task_id):
        async with self.transaction() as session:
            task = await self.read(session, "task", task_id)
            if task.get("plan_confirmed"):
                return await self.view(session, task)
            if task["status"] != "awaiting_plan" or not task.get("plan"):
                raise ConflictError("No pending plan")
            if not self.service.hook_available or not await self.enabled(session):
                raise ConflictError("Editing capability disabled")
            task.update(plan_confirmed=True, status="running", stage="rendering", updated_at=stamp())
            await self.save(session, "task", task_id, task)
            return await self.view(session, task)


async def authenticate(service, token):
    from deerflow_extension_api import ExtensionCredential

    from ggwork_edit.models import credentials

    if service.session_factory is None or not token.startswith("ggwe_") or len(token) > 200:
        return None
    async with service.session_factory() as session:
        record = (await session.execute(select(credentials).where(credentials.c.digest == hashlib.sha256(token.encode()).hexdigest()))).mappings().one_or_none()
        if record is None:
            return None
        device = await service.repository(record["owner_id"]).read(session, "device", record["device_id"], required=False)
        if device is None or device["revoked"]:
            return None
        return ExtensionCredential(record["owner_id"], record["device_id"])
