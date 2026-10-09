"""Operator-enabled data-source process. No host/core or model shell permissions change."""

import asyncio
import logging
import os
import signal
from pathlib import Path
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


class NativeSourceProcess:
    def __init__(self):
        self.enabled = os.environ.get("PICK_SOURCE_ENABLED") == "1"
        self.task: asyncio.Task | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.stopping = False
        self.run_tag: str | None = None

    async def start(self):
        if not self.enabled:
            return
        owner = os.environ.get("PICK_SOURCE_OWNER_ID", "")
        if not owner or owner in {"default", "system:shared"} or not os.environ.get("PICK_SOURCE_DATABASE_URL") or not os.environ.get("PICK_SOURCE_REVISION"):
            raise ValueError("Native source requires an operator owner and private database")
        self.task = asyncio.create_task(self._supervise(), name="pick-native-source")
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(30):
                try:
                    response = await client.get("http://127.0.0.1:8003/health")
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        await self.stop()
        raise RuntimeError("Native source did not become ready")

    async def _supervise(self):
        root = Path(os.environ.get("PICK_SOURCE_ROOT", "/app/customizations/pick-source"))
        allowed = {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "PGSSLMODE",
            "PYTHONPATH",
            "DEER_FLOW_HOME",
            "DEER_FLOW_LARK_CLI_RUN_AS",
            "DEER_FLOW_LARK_CLI_PINNED_VERSION",
            "PLAYWRIGHT_BROWSERS_PATH",
            "QUEYU_STATE_FILE",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed or key.startswith(("PICK_SOURCE_", "CPS_", "PICK_REALSHORT_"))}
        env.update(PICK_SOURCE_BIND="127.0.0.1", PICK_SOURCE_PORT="8003")
        env["PICK_FEED_TOKEN"] = env.get("PICK_REALSHORT_FEED_TOKEN", "")
        env["PICK_EXPORT_TOKEN"] = env.get("PICK_REALSHORT_EXPORT_TOKEN", "")
        while not self.stopping:
            self.run_tag = uuid4().hex
            env["GGWORK_SOURCE_RUN_ID"] = self.run_tag
            try:
                self.process = await asyncio.create_subprocess_exec(
                    "node",
                    "--conditions=react-server",
                    "--import",
                    "tsx",
                    "runtime/server.ts",
                    cwd=root,
                    env=env,
                    start_new_session=True,
                )
                await self.process.wait()
            except OSError:
                logger.error("Native source process could not start")
            finally:
                await self._reap()
            if not self.stopping:
                logger.warning("Native source exited; restarting in five seconds")
                await asyncio.sleep(5)

    async def _reap(self):
        # Collectors form their own cancellation groups. Their inherited tag fences cleanup
        # to this source process, including descendants orphaned after a Node crash.
        def pids():
            found = set()
            if self.process is not None and self.process.returncode is None:
                found.add(self.process.pid)
            if self.run_tag and Path("/proc").exists():
                marker = f"GGWORK_SOURCE_RUN_ID={self.run_tag}".encode()
                for entry in Path("/proc").iterdir():
                    if entry.name.isdigit():
                        try:
                            if marker in (entry / "environ").read_bytes().split(b"\0"):
                                found.add(int(entry.name))
                        except OSError:
                            pass
            return found

        targets = await asyncio.to_thread(pids)
        if not targets:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in targets & await asyncio.to_thread(pids):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            if sig == signal.SIGTERM:
                await asyncio.sleep(3)
        if self.process is not None:
            await self.process.wait()

    async def stop(self):
        self.stopping = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self._reap()


async def native_status(service):
    if not service.native_source.enabled:
        return None
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get("http://127.0.0.1:8003/status", headers={"authorization": f"Bearer {service.sync_settings.export_token}"})
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict) or value.get("enabled") is not True or not isinstance(value.get("jobs"), list):
                raise ValueError("Invalid source status")
            # The fixed local service emits only these safe receipt fields.
            keys = {"name", "status", "attempted_at", "completed_at", "last_success_at", "error_code"}
            return {"enabled": True, "jobs": [{k: v for k, v in job.items() if k in keys} for job in value["jobs"]]}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"enabled": True, "jobs": [], "error": "unavailable"}


def require_resource_owner(owner_id: str):
    configured = os.environ.get("PICK_SOURCE_OWNER_ID", "")
    if not configured or configured in {"default", "system:shared"} or owner_id != configured:
        raise PermissionError("Only the configured source owner can read private resources")


async def native_resource(service, owner_id: str, row: str):
    require_resource_owner(owner_id)
    if not service.native_source.enabled:
        raise RuntimeError("Native source is disabled")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            "http://127.0.0.1:8003/resource", params={"row": row}, headers={"authorization": f"Bearer {service.sync_settings.export_token}"}
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
