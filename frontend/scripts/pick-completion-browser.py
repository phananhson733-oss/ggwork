"""True loopback Gateway/PG/browser acceptance; synthetic model and source only.

Source mode intentionally imports this checkout. Installed mode excludes the
extension source directory and asserts imports resolve inside this interpreter's
site-packages; test fixtures never establish the package import path.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import sysconfig
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


def launch_gateway(env, port, output):
    import httpx

    log = output / "gateway.private.log"
    with log.open("a", encoding="utf-8") as stream:
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
                str(port),
                "--workers",
                "1",
            ],
            cwd=Path(env["DEER_FLOW_PROJECT_ROOT"]) / "backend",
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        with parity.direct_http_client(f"http://127.0.0.1:{port}") as client:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Gateway exited; inspect {log}")
                try:
                    if client.get("/health").status_code == 200:
                        return process
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            raise RuntimeError("Gateway readiness timeout")
    except BaseException:
        stop_group(process)
        raise


def stop_group(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    # A supervisor may exit before an owned descendant that ignores TERM.
    # Reap the dedicated group, not merely the process-group leader.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def launch_frontend(env, port, output):
    import httpx

    log = output / "frontend.private.log"
    with log.open("w", encoding="utf-8") as stream:
        os.chmod(log, 0o600)
        process = subprocess.Popen(
            [
                "pnpm",
                "exec",
                "next",
                "dev",
                "--webpack",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT / "frontend",
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        with parity.direct_http_client(f"http://127.0.0.1:{port}") as client:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Frontend exited; inspect {log}")
                try:
                    if client.get("/").status_code < 500:
                        return process
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            raise RuntimeError("Frontend readiness timeout")
    except BaseException:
        stop_group(process)
        raise


def run_browser(env, output):
    output.mkdir(exist_ok=True, mode=0o700)
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
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return result.wait(timeout=900)
        finally:
            stop_group(result)


def run(
    cluster_file: Path,
    output: Path,
    chromium_executable: Path | None = None,
    voiceover: bool = False,
    review: bool = False,
    mode: str = "source",
    gateway_root: Path = ROOT,
    expected_package_digest: str | None = None,
):
    # Fixture imports also run in this supervisor, not only the Gateway child.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    import yaml

    settings = json.loads(cluster_file.read_text(encoding="utf-8"))
    admin = parity.validate_cluster_settings(settings)
    ca_path = Path(settings["tls_ca_file"]) if settings.get("tls_ca_file") else None
    ca = ca_path.read_text(encoding="utf-8") if ca_path else None
    if ca_path:
        os.environ["PGSSLMODE"] = "verify-full"
        os.environ["PGSSLROOTCERT"] = str(ca_path)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    if mode not in {"source", "installed"}:
        raise ValueError("Explicit source or installed mode required")
    paths = [
        ROOT / part
        for part in (
            "frontend/scripts",
            "customizations/pick-workbench/tests/mirror",
            "customizations/pick-workbench/tests",
        )
    ]
    paths.append(gateway_root / "backend")
    if mode == "source":
        paths.extend(
            ROOT / part
            for part in (
                "customizations/pick-workbench",
                "backend/packages/extension-api",
                "backend/packages/harness",
            )
        )
    expected_package = (
        Path(sysconfig.get_path("purelib")) / "ggwork_pick"
        if mode == "installed"
        else ROOT / "customizations/pick-workbench/ggwork_pick"
    )
    if mode == "installed" and (
        expected_package.is_symlink()
        or not expected_package.resolve().is_relative_to(
            Path(sysconfig.get_path("purelib")).resolve()
        )
    ):
        raise RuntimeError(
            "Installed package must be physically contained in site-packages"
        )
    sys.path[:0] = list(map(str, paths))
    import ggwork_pick

    if Path(ggwork_pick.__file__).resolve().parent != expected_package.resolve():
        raise RuntimeError("Package import does not match the declared QA mode")
    from ggwork_pick.observe.selfcheck import package_digest

    actual_digest = package_digest(expected_package)
    if mode == "installed" and (
        not expected_package_digest or actual_digest != expected_package_digest
    ):
        raise RuntimeError(
            "Installed package digest must match the explicitly frozen candidate"
        )
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
    frontend = None
    try:
        config = yaml.safe_load(
            (gateway_root / "config.pick.example.yaml").read_text(encoding="utf-8")
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
        gateway_source = """import os
from pathlib import Path
import sysconfig
import ggwork_pick
assert Path(ggwork_pick.__file__).resolve().parent == Path(os.environ["QA_PACKAGE_ROOT"]).resolve()
if os.environ["QA_MODE"] == "installed":
    assert not Path(os.environ["QA_PACKAGE_ROOT"]).is_symlink()
    assert Path(ggwork_pick.__file__).resolve().is_relative_to(Path(sysconfig.get_path("purelib")).resolve())
from ggwork_pick.observe.selfcheck import package_digest
assert package_digest() == os.environ["QA_PACKAGE_DIGEST"]
from ggwork_pick.query_reader import QueryReader
QueryReader.from_env = classmethod(lambda cls: cls(os.environ["PICK_MIRROR_READER_URL"], ssl=False))
from deerflow.config.app_config import get_app_config
assert all(m.use == "pick_completion_scripted:CompletionScriptedModel" for m in get_app_config().models)
from app.gateway.pick_asgi import app
import asyncio, json, time, sys
package_root = Path(os.environ["QA_PACKAGE_ROOT"]).resolve()
loaded_files = [Path(module.__file__).resolve() for name, module in sys.modules.items() if (name == "ggwork_pick" or name.startswith("ggwork_pick.")) and getattr(module, "__file__", None)]
assert loaded_files and all(path.is_relative_to(package_root) for path in loaded_files)
loop = asyncio.get_running_loop()
with open(os.environ["PICK_COMPLETION_RUNTIME_PROOF"], "a", encoding="utf-8") as stream:
    os.chmod(os.environ["PICK_COMPLETION_RUNTIME_PROOF"], 0o600)
    stream.write(json.dumps({"mode": os.environ["QA_MODE"], "package_file": str(Path(ggwork_pick.__file__).resolve()), "package_digest": package_digest(), "loaded_package_modules": len(loaded_files), "loop_class": type(loop).__module__ + "." + type(loop).__qualname__, "loop_time": loop.time(), "monotonic": time.monotonic(), "reader_factory_module": QueryReader.from_env.__func__.__module__, "tls_ca_configured": bool(os.environ.get("PICK_MIRROR_CA_PEM")), "pythonpath": os.environ["PYTHONPATH"]}) + "\\n")
"""
        if ca:
            gateway_source = (
                "\n".join(
                    line
                    for line in gateway_source.splitlines()
                    if not line.startswith("QueryReader.from_env =")
                )
                + "\n"
            )
        parity.private(output / "qa_gateway.py", gateway_source)
        gateway_port, frontend_port = unused_port(), unused_port()
        origin = f"http://127.0.0.1:{gateway_port}"
        database_url = cluster.url.set(
            drivername="postgresql", database=fixture["database"]
        ).render_as_string(hide_password=False)
        env = {
            **clean_environment(),
            "PYTHON_DOTENV_DISABLED": "1",
            "DEER_FLOW_HOME": str(output / "home"),
            "DEER_FLOW_PROJECT_ROOT": str(gateway_root),
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
            "QA_PACKAGE_ROOT": str(expected_package),
            "QA_MODE": mode,
            "QA_PACKAGE_DIGEST": actual_digest,
            "PICK_COMPLETION_RUNTIME_PROOF": str(output / "runtime-proof.jsonl"),
        }
        if ca:
            env.update(
                PGSSLMODE="verify-full",
                PGSSLROOTCERT=str(ca_path),
                PICK_MIRROR_CA_PEM=ca,
            )
        process = launch_gateway(env, gateway_port, output)
        with parity.direct_http_client(origin) as client:
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
            "mode": mode,
            "origin": "synthetic_scripted",
            "gateway_url": origin,
            "frontend_url": f"http://127.0.0.1:{frontend_port}",
            "owner": owner,
            "email": email,
            "password": password,
            "source_root": str(ROOT),
            "tls": bool(ca),
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
        if ca:
            test_env.update(
                PICK_MIRROR_READER_URL=env["PICK_MIRROR_READER_URL"],
                PICK_MIRROR_CA_PEM=ca,
            )
        if voiceover:
            test_env["PICK_COMPLETION_VOICEOVER"] = "1"
        if chromium_executable is not None:
            if not chromium_executable.is_file() or not os.access(
                chromium_executable, os.X_OK
            ):
                raise ValueError(
                    "Explicit Chromium executable must exist and be executable"
                )
            test_env["PICK_COMPLETION_CHROMIUM"] = str(chromium_executable.resolve())
        frontend = launch_frontend(test_env, frontend_port, output)
        return_code = run_browser(test_env, output)
        if return_code == 0 and review:
            from pick_completion_feedback import publish_review_fixture

            evidence_file = next((output / "artifacts").glob("*/journey-evidence.json"))
            evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
            with parity.direct_http_client(origin) as client:
                assert (
                    client.post(
                        "/api/v1/auth/login/local",
                        data={"username": email, "password": password},
                    ).status_code
                    == 200
                )
                plan_response = client.get(f"/api/pick/plans/{evidence['plan_id']}")
                assert plan_response.status_code == 200
                plan = plan_response.json()
            stop_group(process)
            process = None
            version = asyncio.run(
                publish_review_fixture(database_url, output, owner, plan["rows"][0])
            )
            state["review"] = {
                "plan_id": plan["id"],
                "row_id": plan["rows"][0]["row_id"],
                "feedback_version_id": version["id"],
            }
            parity.private(output / "session.private.json", json.dumps(state))
            env.update(
                PICK_FEEDBACK_ENABLED="1",
                PICK_FEEDBACK_OWNER_ID=owner,
                PICK_FEEDBACK_SCHEDULE_ENABLED="0",
            )
            process = launch_gateway(env, gateway_port, output)
            review_output = output / "review"
            test_env.update(
                PICK_COMPLETION_PHASE="review",
                PICK_COMPLETION_OUTPUT=str(review_output),
            )
            test_env.pop("PICK_COMPLETION_VOICEOVER", None)
            return_code = run_browser(test_env, review_output)
        print(
            json.dumps(
                {
                    "exit_code": return_code,
                    "mode": mode,
                    "provider_calls": 0,
                    "report": str(output / "browser.log"),
                }
            )
        )
        return return_code
    finally:
        try:
            stop_group(frontend)
        finally:
            try:
                stop_group(process)
            finally:
                board_fixture.down(
                    cluster, database=fixture["database"], role=fixture["role"]
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chromium-executable", type=Path)
    parser.add_argument("--voiceover", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--mode", choices=("source", "installed"), default="source")
    parser.add_argument("--gateway-root", type=Path, default=ROOT)
    parser.add_argument("--expected-package-digest")
    args = parser.parse_args()
    with parity.isolated_pg_environment():
        raise SystemExit(
            run(
                args.cluster_file,
                args.output.resolve(),
                args.chromium_executable,
                args.voiceover,
                args.review,
                args.mode,
                args.gateway_root.resolve(),
                args.expected_package_digest,
            )
        )
