"""Durable device protocol with a control loop independent of native processing."""

import asyncio
import contextlib
import json
import re
import signal
import time
from uuid import uuid4

import httpx

from .native import NativeWorker, WorkerStopped, doctor
from .storage import WorkerError, identifier, no_symlink, selected_identity

# Explicit initial policy, not a measured throughput or maximum-resource promise.
HEARTBEAT_SECONDS = 10
POLL_SECONDS = 2
PLAN_LOOKUP_LIMIT = 3
TERMINAL = {"completed", "partial", "failed", "stopped"}


class WorkerSession:
    def __init__(self, store, http):
        self.store = store
        self.http = http
        self.base = "/api/editing/worker/devices/" + store.config()["device_id"]
        self.stop = asyncio.Event()
        self.shutdown = asyncio.Event()
        self.native = NativeWorker(store, self.stop)
        self.state = store.journal()
        self.report_lock = asyncio.Lock()
        self.authorization_lost = False
        self.preparation_cache = {}

    async def request(self, method, path, **kwargs):
        response = await self.http.request(method, self.base + path, follow_redirects=False, **kwargs)
        if response.status_code in (401, 403):
            self.authorization_lost = True
            self.stop.set()
            self.shutdown.set()
        response.raise_for_status()
        return response.json()

    def save(self):
        self.store.save_journal(self.state)

    async def claim(self):
        if self.state.get("task_id"):
            task = await self.request("GET", "/tasks/" + self.state["task_id"])
            current_attempt = task.get("attempt")
            if task["status"] == "queued" or current_attempt is None or current_attempt["id"] != self.state["attempt"]["id"]:
                # Owner retry/supersession is authoritative. A lost terminal ACK
                # must not cause replay against a newly queued or different attempt.
                self.state = {}
                self.save()
            else:
                return task
        if "claim_request_id" not in self.state:
            self.state = {"claim_request_id": uuid4().hex}
            self.save()
        result = await self.request("POST", "/claim", json={"request_id": self.state["claim_request_id"]})
        if result["task"] is None:
            self.state = {}
            self.save()
            return None
        self.state.update(task_id=result["task"]["id"], attempt=result["attempt"], planner_submit_allowed=True)
        self.save()
        return result["task"]

    async def report(self, kind, **fields):
        async with self.report_lock:
            if not self.state.get("attempt"):
                return None
            if kind == "heartbeat":
                attempt = self.state["attempt"]
                body = {"attempt_id": attempt["id"], "fence": attempt["fence"], "event_id": uuid4().hex, "kind": kind}
                result = await self.request("POST", "/tasks/" + self.state["task_id"] + "/report", json=body)
                if result["stop_requested"]:
                    self.stop.set()
                return result
            # A lost response must retry the exact event and payload, never a new publication.
            if self.state.get("pending_report"):
                pending = self.state["pending_report"]
                result = await self.request("POST", "/tasks/" + self.state["task_id"] + "/report", json=pending)
                self.state.pop("pending_report")
                self.save()
                if result["stop_requested"]:
                    self.stop.set()
                if pending["kind"] == kind and all(pending.get(k) == v for k, v in fields.items()):
                    return result
            attempt = self.state["attempt"]
            body = {"attempt_id": attempt["id"], "fence": attempt["fence"], "event_id": uuid4().hex, "kind": kind, **fields}
            self.state["pending_report"] = body
            self.save()
            result = await self.request("POST", "/tasks/" + self.state["task_id"] + "/report", json=body)
            self.state.pop("pending_report")
            self.save()
            if result["stop_requested"]:
                self.stop.set()
            return result

    async def control(self, readiness):
        while not self.shutdown.is_set():
            try:
                await self.request("POST", "/heartbeat", json=readiness)
                if self.state.get("task_id"):
                    await self.report("heartbeat")
            except httpx.HTTPError:
                if self.authorization_lost:
                    return
            await self.wait(HEARTBEAT_SECONDS)

    async def wait(self, seconds):
        try:
            await asyncio.wait_for(self.shutdown.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def receipt_manifest(self, task):
        manifest = task["source_manifest"]
        files = []
        missing = []
        for source in manifest["files"]:
            try:
                sha = self.store.received_sha256(task["id"], manifest["grant_id"], source)
            except WorkerError as error:
                if str(error) != "source_receipt_missing":
                    raise
                missing.append(source)
            else:
                if source.get("sha256") not in (None, sha):
                    raise WorkerError("source_changed")
                files.append({**source, "sha256": sha})
        if missing:
            parent_id = task.get("parent_task_id")
            if not parent_id:
                raise WorkerError("source_receipt_missing")
            # A scoped parent 403 is not proof that the device token was revoked.
            response = await self.http.get(self.base + "/tasks/" + identifier(parent_id), follow_redirects=False)
            if response.status_code in (403, 404):
                raise WorkerError("source_changed")
            if response.status_code == 401:
                self.authorization_lost = True
                self.stop.set()
                self.shutdown.set()
            response.raise_for_status()
            try:
                parent = response.json()
                original = parent["source_manifest"]
                if (
                    parent["id"] != parent_id
                    or parent["device_id"] != self.store.config()["device_id"]
                    or parent["manifest_frozen"] is not True
                    or original["grant_id"] != manifest["grant_id"]
                ):
                    raise ValueError("parent identity differs")
                originals = {item["media_id"]: item for item in original["files"]}
                if len(originals) != len(original["files"]):
                    raise ValueError("parent source identities are duplicated")
                for source in missing:
                    prior = originals[source["media_id"]]
                    sha = prior["sha256"]
                    if (
                        selected_identity(original["grant_id"], prior) != selected_identity(manifest["grant_id"], source)
                        or prior["state"] != "verified"
                        or source.get("sha256") not in (None, sha)
                        or not isinstance(sha, str)
                        or not re.fullmatch(r"[a-f0-9]{64}", sha)
                    ):
                        raise ValueError("parent source differs")
                    files.append({**source, "sha256": sha})
            except (KeyError, TypeError, ValueError) as error:
                raise WorkerError("source_changed") from error
        by_id = {source["media_id"]: source for source in files}
        return {**manifest, "files": [by_id[source["media_id"]] for source in manifest["files"]]}

    async def prepare(self):
        tasks = await self.request("GET", "/preparations")
        for task in tasks["items"]:
            key = json.dumps(
                [task["source_manifest"], task.get("source_directory"), task["requirements"], self.store.receipt_revisions.get(task["id"], 0)], sort_keys=True
            )
            previous = self.preparation_cache.get(task["id"])
            # Native errors are retried at most once a minute; explicit owner recheck
            # clears the server error and immediately bypasses this backoff.
            if (
                previous
                and previous[0] == key
                and task.get("native_preparation_error") not in (None, "source_receipt_missing")
                and time.monotonic() - previous[1] < 60
            ):
                continue
            self.preparation_cache[task["id"]] = (key, time.monotonic())
            try:
                language = task["requirements"]["language"]
                if self.store.config()["model_language"] == "en" and language != "en":
                    raise WorkerError("model_language_unsupported")
                manifest = task["source_manifest"]
                if manifest is None and task.get("source_directory"):
                    source = task["source_directory"]
                    manifest = await self.native.discover(source["grant_id"], source["relative_path"])
                    await self.request("POST", "/tasks/" + task["id"] + "/discovery", json={"source_manifest": manifest})
                if manifest:
                    if not task.get("source_directory"):
                        manifest = await self.receipt_manifest(task)
                    verified = await self.native.verify_manifest(manifest)
                    await self.request("POST", "/tasks/" + task["id"] + "/manifest", json={"source_manifest": verified})
            except WorkerError as error:
                if self.shutdown.is_set():
                    return
                if task.get("native_preparation_error") != str(error):
                    await self.request("POST", "/tasks/" + task["id"] + "/preparation-error", json={"error": str(error)})

    async def controlled_request(self, method, path, **kwargs):
        if self.stop.is_set():
            raise WorkerStopped("stopped")
        pending = asyncio.create_task(self.request(method, path, **kwargs))
        stopped = asyncio.create_task(self.stop.wait())
        try:
            await asyncio.wait((pending, stopped), return_when=asyncio.FIRST_COMPLETED)
            if self.stop.is_set():
                raise WorkerStopped("stopped")
            return await pending
        finally:
            pending.cancel()
            stopped.cancel()
            await asyncio.gather(pending, stopped, return_exceptions=True)

    async def recover_plan(self, task_id):
        outcome = self.state["planning_request"]
        while outcome["lookups"] < PLAN_LOOKUP_LIMIT:
            outcome["lookups"] += 1
            self.save()
            try:
                current = await self.controlled_request("GET", "/tasks/" + task_id)
            except httpx.HTTPError:
                if self.authorization_lost:
                    raise
            else:
                if current["status"] == "stopping":
                    raise WorkerStopped("stopped")
                if (
                    current.get("plan") is not None
                    or current["status"] in TERMINAL | {"queued"}
                    or (current.get("attempt") or {}).get("id") != self.state["attempt"]["id"]
                ):
                    return current
                if outcome.get("definite"):
                    break
            if outcome["lookups"] < PLAN_LOOKUP_LIMIT:
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=POLL_SECONDS)
                except TimeoutError:
                    pass
                if self.stop.is_set():
                    raise WorkerStopped("stopped")
        raise WorkerError(outcome["error"])

    async def request_plan(self, task_id, attempt, transcripts):
        if "planning_request" not in self.state:
            # Persist BEFORE sending. An unknown transport outcome or a process
            # restart may query the attempt, but can never blindly invoke it again.
            self.state["planning_request"] = {"lookups": 0, "error": "planner_outcome_unknown"}
            self.state.pop("planner_submit_allowed", None)
            self.save()
            try:
                return await self.controlled_request(
                    "POST",
                    "/tasks/" + task_id + "/plan",
                    json={"attempt_id": attempt["id"], "fence": attempt["fence"], "transcripts": transcripts},
                    timeout=180,
                )
            except httpx.HTTPStatusError as error:
                if self.authorization_lost:
                    raise
                self.state["planning_request"].update(
                    definite=True,
                    error="planner_unavailable" if error.response.status_code >= 500 else "gateway_request_rejected",
                )
                self.save()
            except httpx.TransportError:
                pass  # The durable marker already records the uncertain outcome.
        return await self.recover_plan(task_id)

    def remember_uncertain_planning(self, task):
        if (
            task.get("plan") is None
            and task.get("stage") in ("planning", "awaiting_plan")
            and not self.state.get("planner_submit_allowed")
            and "planning_request" not in self.state
        ):
            # Either authoritative observation can prove a legacy planning stage;
            # an older receipt must not erase evidence seen before its replay.
            self.state["planning_request"] = {"lookups": 0, "error": "planner_outcome_unknown"}
            self.save()

    async def execute(self, task):
        if self.shutdown.is_set():
            return
        if task["status"] in TERMINAL:
            self.state = {}
            self.save()
            return
        self.stop.clear()
        if task["status"] == "stopping":
            # The exclusive lock is inherited by every child. A restarted worker cannot
            # reach this point until all children from its predecessor have exited.
            self.state.pop("pending_report", None)
            self.save()
            await self.report("stopped")
            self.state = {}
            self.save()
            return
        attempt = self.state["attempt"]
        task_id = task["id"]
        try:
            heartbeat = await self.report("heartbeat")  # renew before replaying output after a restart
            if heartbeat["stop_requested"]:
                raise WorkerStopped("stopped")
            task = heartbeat["task"]
            if task["status"] in TERMINAL:
                self.state = {}
                self.save()
                return
            self.remember_uncertain_planning(task)
            pending = self.state.get("pending_report")
            if (
                task.get("plan") is not None
                and pending
                and pending.get("kind") == "failure"
                and pending.get("error") in ("planner_outcome_unknown", "planner_unavailable")
            ):
                # A late stored plan wins over a deferred planner failure after reconnect.
                self.state.pop("pending_report")
                self.save()
            if self.state.get("pending_report"):
                pending = self.state["pending_report"]
                result = await self.report(pending["kind"], **{k: v for k, v in pending.items() if k not in ("kind", "attempt_id", "fence", "event_id")})
                task = result["task"]
                if result["stop_requested"]:
                    raise WorkerStopped("stopped")
                if task["status"] in TERMINAL:
                    self.state = {}
                    self.save()
                    return
                self.remember_uncertain_planning(task)
            manifest, requirements = task["source_manifest"], task["requirements"]
            if task.get("plan") is None:
                transcripts = None
                if "planning_request" not in self.state:
                    await self.report("stage", stage="transcribing")
                    transcripts = await self.native.transcribe(manifest, requirements["language"], attempt["id"])
                    await self.report("stage", stage="planning")
                task = await self.request_plan(task_id, attempt, transcripts)
                if task["status"] in TERMINAL:
                    self.state = {}
                    self.save()
                    return
                if task["status"] == "queued" or (task.get("attempt") or {}).get("id") != attempt["id"]:
                    return  # claim() reconciles an explicit retry or supersession.
            while not task["plan_confirmed"]:
                if self.stop.is_set():
                    raise WorkerStopped("stopped")
                await self.wait(POLL_SECONDS)
                task = await self.request("GET", "/tasks/" + task_id)
                if task["status"] == "stopping":
                    raise WorkerStopped("stopped")
            for output_id in attempt["output_ids"]:
                if any(o["id"] == output_id and o["status"] == "completed" for o in task["outputs"]):
                    continue
                if self.stop.is_set():
                    raise WorkerStopped("stopped")
                try:
                    await self.report("stage", stage="rendering", output_id=output_id)
                    artifact_id = attempt["id"] + "_" + output_id
                    index = no_symlink(self.store.home / "artifacts" / (artifact_id + ".json"))
                    if index.exists():
                        result = json.loads(index.read_text(encoding="utf-8"))["result"]
                        await asyncio.to_thread(self.native.read_artifact, {**result, "offset": 0, "length": min(16, result["size_bytes"])})
                    else:
                        result = await self.native.render(manifest, requirements, task["plan"], attempt["id"], output_id)
                    await self.report("stage", stage="verifying", output_id=output_id)
                    await self.report("output", output_id=output_id, result=result)
                except WorkerStopped:
                    raise
                except WorkerError as error:
                    await self.report("failure", output_id=output_id, error=str(error))
            await self.report("complete")
            self.state = {}
            self.save()
        except WorkerStopped:
            if not self.authorization_lost:
                current = await self.request("GET", "/tasks/" + task_id)
                if current["status"] == "stopping":
                    self.state.pop("pending_report", None)
                    self.save()
                    await self.report("stopped")
                    self.state = {}
                    self.save()
                # Local Ctrl-C preserves the active attempt for restart; it does not
                # manufacture a server stop request or a completed result.
        except httpx.HTTPStatusError:
            if self.authorization_lost:
                return
            current = await self.request("GET", "/tasks/" + task_id)
            self.state.pop("pending_report", None)
            self.save()
            if current["status"] == "stopping":
                await self.report("stopped")
            elif current["status"] not in TERMINAL:
                await self.report("failure", error="gateway_request_rejected")
            self.state = {}
            self.save()
        except WorkerError as error:
            if not self.authorization_lost:
                result = await self.report("failure", error=str(error))
                if result["task"]["status"] in TERMINAL:
                    self.state = {}
                    self.save()
                # A server transaction may preserve a concurrently stored plan.
                # Keep this attempt so the next authoritative read can execute it.


async def run(store):
    config = store.config()
    readiness = await doctor(store)
    async with httpx.AsyncClient(
        base_url=config["gateway"], headers={"Authorization": "Bearer " + config["token"]}, timeout=15, follow_redirects=False, trust_env=False
    ) as http:
        session = WorkerSession(store, http)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: (session.stop.set(), session.shutdown.set()))
        control = asyncio.create_task(session.control(readiness))
        relay = None
        try:
            from .relay_client import RelayClient, RelayFailure
            from .transfer import Receiver

            receiver = Receiver(store)

            def receive(command, data):
                try:
                    receiver.receive(command, data)
                except (WorkerError, OSError, ValueError) as error:
                    raise RelayFailure("transfer_failed") from error

            def read(command):
                try:
                    return session.native.read_artifact(command)
                except WorkerError as error:
                    code = str(error)
                    raise RelayFailure(code if code in ("file_missing", "file_changed", "grant_denied") else "file_changed") from error

            relay = asyncio.create_task(RelayClient(http, config["device_id"], receive=receive, read=read).run(session.shutdown))
            while not session.shutdown.is_set():
                try:
                    if readiness["ready"]:
                        if not session.state.get("task_id"):
                            await session.prepare()
                        task = await session.claim()
                        if task:
                            await session.execute(task)
                    await session.wait(POLL_SECONDS)
                except httpx.HTTPStatusError as error:
                    if session.authorization_lost:
                        break
                    if error.response.status_code == 409 and session.state.get("task_id"):
                        current = await session.request("GET", "/tasks/" + session.state["task_id"])
                        if current["status"] == "stopping":
                            session.state.pop("pending_report", None)
                            session.save()
                            await session.execute(current)
                            continue
                    await session.wait(POLL_SECONDS)
                except httpx.TransportError:
                    await session.wait(POLL_SECONDS)
        finally:
            session.stop.set()
            session.shutdown.set()
            control.cancel()
            if relay:
                relay.cancel()
            await asyncio.gather(control, *([relay] if relay else []), return_exceptions=True)
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.remove_signal_handler(sig)
