"""Actual executor/process cleanup after the ordinary-phase await is cancelled."""

import asyncio
import json
import os
import signal
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from test_lark_tool import _runtime

from ggwork_pick import lark_runner, lark_tool
from ggwork_pick.context import PickTask
from ggwork_pick.middleware import PickToolGate


@pytest.fixture
def worker_finished(monkeypatch):
    """Observe completion while retaining the real executor and worker function."""
    finished = threading.Event()
    original = lark_runner.in_lark_thread

    async def observed(func, *args):
        def work():
            try:
                return func(*args)
            finally:
                finished.set()

        return await original(work)

    monkeypatch.setattr(lark_runner, "in_lark_thread", observed)
    return finished


@pytest.mark.asyncio
async def test_expired_ordinary_await_kills_process_group_cleans_scratch_and_reuses_slot(monkeypatch, tmp_path, worker_finished):
    ready, survived = tmp_path / "ready.json", tmp_path / "survived"
    child = f"import threading; from pathlib import Path; threading.Event().wait(3.5); Path({str(survived)!r}).write_text('late work')"
    binary = tmp_path / "synthetic-lark"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys, threading\nfrom pathlib import Path\n"
        "if 'list' in sys.argv:\n    print('slot reusable')\n    sys.exit(0)\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"ready = Path({str(ready)!r})\n"
        "temporary = ready.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(), 'scratch':str(Path.cwd().parent)}))\n"
        "temporary.replace(ready)\nthreading.Event().wait(30)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=tmp_path / "host"))
    monkeypatch.delenv(lark_runner.RUN_AS_ENV, raising=False)
    monkeypatch.delenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raising=False)
    # Binary discovery is the external-system seam. Executor, slot, scratch,
    # Popen, pipe drain and process-group cancellation all use production code.
    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda **_kwargs: SimpleNamespace(available=True, path=str(binary), version="0.0.0"))
    previous_binary = dict(lark_runner._BINARY_CACHE)
    lark_runner._BINARY_CACHE.clear()
    runtime = _runtime()
    task = runtime.context[EXTENSION_TASK_STORE_KEY].get(PickTask)
    task.deadline = asyncio.get_running_loop().time() + 22
    request = SimpleNamespace(
        runtime=runtime, tool=lark_tool.lark_cli_tool, tool_call={"id": "synthetic", "name": "lark_cli", "args": {"argv": ["skills", "read", "lark-doc"]}}
    )

    async def handler(_):
        return await lark_tool.lark_cli_tool.coroutine(argv=request.tool_call["args"]["argv"], runtime=runtime)

    pending = asyncio.create_task(PickToolGate().awrap_tool_call(request, handler))
    info = None
    try:
        async with asyncio.timeout(4):
            while not await asyncio.to_thread(ready.exists):
                if pending.done():
                    pytest.fail(f"synthetic command ended before readiness: {await pending}")
                await asyncio.sleep(0.01)
        info = json.loads(await asyncio.to_thread(ready.read_text, encoding="utf-8"))
        with pytest.raises(TimeoutError):
            await pending
        # The asyncio await has ended. Independently observe real worker cleanup,
        # rather than treating future cancellation as evidence the process died.
        scratch = Path(info["scratch"])
        async with asyncio.timeout(1):
            while await asyncio.to_thread(scratch.exists):
                await asyncio.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.kill(info["pid"], 0)
        assert await asyncio.to_thread(worker_finished.wait, 1)
        assert 18 < task.remaining() <= 20
        fresh = _runtime()
        assert await lark_tool.lark_cli_tool.coroutine(argv=["skills", "list"], runtime=fresh) == "slot reusable"
        await asyncio.sleep(1.9)
        assert not survived.exists()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        if info and Path(info["scratch"]).exists():
            try:
                os.killpg(info["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
            async with asyncio.timeout(3):
                while await asyncio.to_thread(Path(info["scratch"]).exists):
                    await asyncio.sleep(0.01)
        lark_runner._BINARY_CACHE.clear()
        lark_runner._BINARY_CACHE.update(previous_binary)


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_exits_first", [False, True])
async def test_cancelled_cold_probe_uses_remaining_deadline_and_never_starts_command(monkeypatch, tmp_path, worker_finished, parent_exits_first):
    ready, command = tmp_path / "probe.json", tmp_path / "command-started"
    survived = tmp_path / "probe-descendant-survived"
    child_ready = tmp_path / "probe-child-ready"
    child = (
        f"import threading; from pathlib import Path; Path({str(child_ready)!r}).write_text('ready'); "
        f"threading.Event().wait(3.5); Path({str(survived)!r}).write_text('late work')"
    )
    binary = tmp_path / "synthetic-cold-lark"
    binary.write_text(
        f"#!{sys.executable}\nimport json, os, subprocess, sys, threading\nfrom pathlib import Path\n"
        "if '--version' in sys.argv:\n"
        f"    child = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"    while not Path({str(child_ready)!r}).exists(): threading.Event().wait(0.01)\n"
        f"    ready = Path({str(ready)!r})\n"
        "    pending = ready.with_suffix('.tmp')\n"
        "    pending.write_text(json.dumps({'pid':os.getpid(), 'child':child.pid}))\n"
        "    pending.replace(ready)\n" + ("    print('0.0.0', flush=True)\n" if parent_exits_first else "    threading.Event().wait(30)\n") + "else:\n"
        f"    Path({str(command)!r}).write_text('must not start')\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=tmp_path / "host"))
    monkeypatch.delenv(lark_runner.RUN_AS_ENV, raising=False)
    monkeypatch.delenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raising=False)
    monkeypatch.setattr(lark_cli, "_resolve_lark_cli_path", lambda: str(binary))
    previous_binary = dict(lark_runner._BINARY_CACHE)
    lark_runner._BINARY_CACHE.clear()
    runtime = _runtime()
    task = runtime.context[EXTENSION_TASK_STORE_KEY].get(PickTask)
    task.deadline = asyncio.get_running_loop().time() + 22
    pending = asyncio.create_task(lark_tool.lark_cli_tool.coroutine(argv=["skills", "list"], runtime=runtime))
    pid = child_pid = None
    try:
        async with asyncio.timeout(3):
            while not await asyncio.to_thread(ready.exists):
                if pending.done():
                    pytest.fail(f"synthetic probe ended before readiness: {await pending}")
                await asyncio.sleep(0.01)
        info = json.loads(await asyncio.to_thread(ready.read_text, encoding="utf-8"))
        pid, child_pid = info["pid"], info["child"]
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with asyncio.timeout(3):
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
        remaining = max(0.0, task.ordinary_deadline - asyncio.get_running_loop().time())
        assert await asyncio.to_thread(worker_finished.wait, remaining + 1), "probe worker is still waiting on descendant pipes"
        assert 18 < task.remaining() <= 20
        assert not command.exists()
        await asyncio.sleep(1.9)
        assert not survived.exists()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        for owned_pid in (pid, child_pid):
            if owned_pid:
                try:
                    os.kill(owned_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if pid:
            assert await asyncio.to_thread(worker_finished.wait, 3)
            async with asyncio.timeout(3):
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(0.01)
        lark_runner._BINARY_CACHE.clear()
        lark_runner._BINARY_CACHE.update(previous_binary)
