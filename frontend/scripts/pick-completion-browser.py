"""True loopback Gateway/PG/browser acceptance; synthetic model and source only.

This SOURCE mode intentionally imports the selected checkout. Installed-package
acceptance must use a separate harness without these PYTHONPATH source entries.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PARITY_PATH = ROOT / "frontend/scripts/pick-common-query-parity.py"
spec = importlib.util.spec_from_file_location("parity", PARITY_PATH)
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


def clean_environment():
    return {
        k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ
    }


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def run(cluster_file: Path, output: Path, chromium_executable: Path | None = None):
    import httpx
    import yaml

    settings = json.loads(cluster_file.read_text(encoding="utf-8"))
    admin = parity.validate_cluster_settings(settings)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    paths = [
        ROOT / part
        for part in (
            "frontend/scripts",
            "customizations/pick-workbench/tests/mirror",
            "customizations/pick-workbench/tests",
            "customizations/pick-workbench",
            "backend/packages/extension-api",
            "backend/packages/harness",
            "backend",
        )
    ]
    sys.path[:0] = list(map(str, paths))
    import board_fixture
    import pg

    cluster = pg.PgCluster(admin)
    fixture = board_fixture.up(
        cluster,
        password=secrets.token_urlsafe(24),
        url_file=output / "reader.private",
        data_dir=output / "pick",
    )
    process = None
    try:
        config = yaml.safe_load(
            (ROOT / "config.pick.example.yaml").read_text(encoding="utf-8")
        )
        config["models"] = [
            {
                "name": "azure-pick",
                "display_name": "Synthetic scripted QA",
                "use": "pick_completion_scripted:CompletionScriptedModel",
                "model": "offline",
            }
        ]
        config["tools"] = [
            t for t in config["tools"] if t["name"] == "pick_query_candidates"
        ]
        config["tool_groups"] = [{"name": "pick"}]
        config["database"] = {
            "backend": "postgres",
            "postgres_url": "$PICK_DATABASE_URL",
            "postgres_schema": "deerflow",
            "checkpoint_channel_mode": "full",
        }
        for feature in (
            "title",
            "suggestions",
            "memory",
            "summarization",
            "task_continuity",
            "skill_evolution",
            "scheduler",
        ):
            config[feature] = {"enabled": False}
        config["auth"]["local"]["allow_registration"] = True
        config["plugins"][0]["config"] = {"data_dir": str(output / "pick")}
        parity.private(output / "config.yaml", yaml.safe_dump(config))
        parity.private(
            output / "extensions.json", json.dumps({"mcpServers": {}, "skills": {}})
        )
        parity.private(
            output / "qa_gateway.py",
            """import os
from pathlib import Path
import ggwork_pick
assert Path(ggwork_pick.__file__).resolve().is_relative_to(Path(os.environ["QA_SOURCE_ROOT"]) / "customizations/pick-workbench")
from ggwork_pick.query_reader import QueryReader
QueryReader.from_env = classmethod(lambda cls: cls(os.environ["PICK_MIRROR_READER_URL"], ssl=False))
from deerflow.config.app_config import get_app_config
assert all(m.use == "pick_completion_scripted:CompletionScriptedModel" for m in get_app_config().models)
from app.gateway.pick_asgi import app
""",
        )
        gateway_port, frontend_port = unused_port(), unused_port()
        origin = f"http://127.0.0.1:{gateway_port}"
        database_url = cluster.url.set(
            drivername="postgresql", database=fixture["database"]
        ).render_as_string(hide_password=False)
        env = {
            **clean_environment(),
            "PYTHON_DOTENV_DISABLED": "1",
            "DEER_FLOW_HOME": str(output / "home"),
            "DEER_FLOW_PROJECT_ROOT": str(ROOT),
            "DEER_FLOW_CONFIG_PATH": str(output / "config.yaml"),
            "DEER_FLOW_EXTENSIONS_CONFIG_PATH": str(output / "extensions.json"),
            "PICK_DATABASE_URL": database_url,
            "PICK_DB_BACKEND": "postgres",
            "PICK_MIRROR_READER_URL": (output / "reader.private").read_text().strip(),
            "PGSSLMODE": "disable",
            "PYTHONPATH": os.pathsep.join([str(output), *map(str, paths)]),
            "PYTHONUNBUFFERED": "1",
            "PICK_RUN_TIMEOUT_SECONDS": "60",
            "PICK_COMPLETION_QA": "loopback-synthetic-only",
            "PICK_COMPLETION_LEDGER": str(output / "model-ledger.jsonl"),
            "QA_SOURCE_ROOT": str(ROOT),
        }
        log = output / "gateway.private.log"
        with log.open("w", encoding="utf-8") as stream:
            os.chmod(log, 0o600)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "qa_gateway:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(gateway_port),
                    "--workers",
                    "1",
                    "--loop",
                    "asyncio",
                ],
                cwd=ROOT / "backend",
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        with parity.direct_http_client(origin) as client:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Gateway exited; inspect {log}")
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("Gateway readiness timeout")
            assert (
                client.post(
                    "/api/v1/auth/initialize",
                    json={
                        "email": "qa-completion-admin@example.com",
                        "password": "Qa9!" + secrets.token_urlsafe(24),
                    },
                ).status_code
                == 201
            )
        password = "Qa9!" + secrets.token_urlsafe(24)
        email = "qa-completion-browser@example.com"
        with parity.direct_http_client(origin) as client:
            registered = client.post(
                "/api/v1/auth/register", json={"email": email, "password": password}
            )
            assert (
                registered.status_code == 201
                and registered.json()["system_role"] == "user"
            )
            owner = registered.json()["id"]
        state = {
            "mode": "source",
            "origin": "synthetic_scripted",
            "gateway_url": origin,
            "frontend_url": f"http://127.0.0.1:{frontend_port}",
            "owner": owner,
            "email": email,
            "password": password,
            "source_root": str(ROOT),
        }
        parity.private(output / "session.private.json", json.dumps(state))
        test_env = {
            **clean_environment(),
            "PYTHON_DOTENV_DISABLED": "1",
            "PICK_COMPLETION_FIXTURE": str(output / "session.private.json"),
            "PICK_COMPLETION_OUTPUT": str(output),
            "PICK_COMPLETION_FRONTEND_PORT": str(frontend_port),
            "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL": origin,
            "SKIP_ENV_VALIDATION": "1",
            "NEXT_TELEMETRY_DISABLED": "1",
        }
        if chromium_executable is not None:
            if not chromium_executable.is_file() or not os.access(
                chromium_executable, os.X_OK
            ):
                raise ValueError(
                    "Explicit Chromium executable must exist and be executable"
                )
            test_env["PICK_COMPLETION_CHROMIUM"] = str(chromium_executable.resolve())
        with (output / "browser.log").open("w", encoding="utf-8") as stream:
            result = subprocess.Popen(
                [
                    "pnpm",
                    "exec",
                    "playwright",
                    "test",
                    "--config",
                    "playwright.pick-completion-real.config.ts",
                ],
                cwd=ROOT / "frontend",
                env=test_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                result.wait(timeout=900)
            finally:
                if result.poll() is None:
                    os.killpg(result.pid, signal.SIGTERM)
                    try:
                        result.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(result.pid, signal.SIGKILL)
                        result.wait(timeout=5)
        print(
            json.dumps(
                {
                    "exit_code": result.returncode,
                    "mode": "source",
                    "provider_calls": 0,
                    "report": str(output / "browser.log"),
                }
            )
        )
        return result.returncode
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        board_fixture.down(cluster, database=fixture["database"], role=fixture["role"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chromium-executable", type=Path)
    args = parser.parse_args()
    with parity.isolated_pg_environment():
        raise SystemExit(
            run(args.cluster_file, args.output.resolve(), args.chromium_executable)
        )
