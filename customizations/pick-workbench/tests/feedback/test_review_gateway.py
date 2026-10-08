"""Real loopback Gateway sessions + real frontend HTTP adapters; synthetic private data only."""

import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import yaml
from sqlalchemy.engine import make_url


def private(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def stop(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_review_real_ordinary_auth_csrf_and_frontend_requests(pick_db_url, tmp_path):
    root = Path(__file__).resolve().parents[4]
    config = yaml.safe_load((root / "config.pick.example.yaml").read_text())
    config.update(models=[], tools=[], tool_groups=[], extensions={"middlewares": []})
    config["scheduler"] = {"enabled": False}
    config["title"] = {"enabled": False}
    config["auth"]["local"]["allow_registration"] = True
    config["plugins"][0]["config"] = {"data_dir": str(tmp_path / "pick")}
    postgres = pick_db_url.startswith("postgresql")
    database_url = pick_db_url if postgres else f"sqlite+aiosqlite:///{tmp_path / 'db' / 'deerflow.db'}"
    config["database"] = (
        {"backend": "postgres", "postgres_url": "$PICK_DATABASE_URL", "postgres_schema": "deerflow", "checkpoint_channel_mode": "full"}
        if postgres
        else {"backend": "sqlite", "sqlite_dir": str(tmp_path / "db"), "checkpoint_channel_mode": "full"}
    )
    private(tmp_path / "config.yaml", yaml.safe_dump(config))
    private(tmp_path / "extensions.json", json.dumps({"mcpServers": {}, "skills": {}}))
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        DEER_FLOW_HOME=str(tmp_path / "home"),
        DEER_FLOW_PROJECT_ROOT=str(root),
        DEER_FLOW_CONFIG_PATH=str(tmp_path / "config.yaml"),
        DEER_FLOW_EXTENSIONS_CONFIG_PATH=str(tmp_path / "extensions.json"),
        PYTHONPATH=os.pathsep.join(
            str(root / part) for part in ("customizations/pick-workbench", "backend/packages/extension-api", "backend/packages/harness", "backend")
        ),
        PYTHONUNBUFFERED="1",
        PICK_FEEDBACK_SCHEDULE_ENABLED="0",
    )
    if postgres:
        env["PICK_DATABASE_URL"] = make_url(pick_db_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = None
    frontend = None

    def start():
        log = tmp_path / "gateway.log"
        private(log, "")
        with log.open("a") as stream:
            running = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.gateway.pick_asgi:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=root / "backend",
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        try:
            with httpx.Client(base_url=origin, timeout=3, trust_env=False) as probe:
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    assert running.poll() is None, "Gateway startup failed; inspect private test log"
                    try:
                        if probe.get("/health").status_code == 200:
                            return running
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                raise AssertionError("Gateway startup timed out")
        except BaseException:
            stop(running)
            raise

    try:
        process = start()
        password = "Qa9!" + secrets.token_urlsafe(24)
        with httpx.Client(base_url=origin, timeout=10, trust_env=False) as admin, httpx.Client(base_url=origin, timeout=10, trust_env=False) as ordinary:
            assert admin.post("/api/v1/auth/initialize", json={"email": "review-admin@example.com", "password": password}).status_code == 201
            user = ordinary.post("/api/v1/auth/register", json={"email": "review-owner@example.com", "password": password})
            assert user.status_code == 201 and user.json()["system_role"] == "user"
            owner = user.json()["id"]
            stop(process)
            env.update(PICK_FEEDBACK_ENABLED="1", PICK_FEEDBACK_OWNER_ID=owner)
            process = start()

            async def seed():
                from engines import host_engine
                from sqlalchemy.ext.asyncio import async_sessionmaker
                from test_planning import draft_input

                from feedback.fakes import operating_rows
                from feedback.test_plan_review import published

                engine = host_engine(database_url)
                try:
                    service = SimpleNamespace(session_factory=async_sessionmaker(engine), data_dir=tmp_path / "pick")
                    source = operating_rows()
                    source["dramas"][0]["平台"] = ["Example"]
                    version = await published(service, source, owner=owner)
                    plan, _ = await draft_input(service, owner=owner, request_id="ordinary-plan", count=1)
                    return plan, version
                finally:
                    await engine.dispose()

            plan, version = asyncio.run(seed())
            assert ordinary.get("/api/pick/feedback/posts").json()["total"] == 3
            fake = dict(
                request_id="csrf",
                plan_id="none",
                row_id="none",
                expected_plan_version=1,
                feedback_version_id=version["id"],
                post_key="tiktok:post-0",
                confirmation="manual",
            )
            assert ordinary.post("/api/pick/feedback/plan-links", json=fake).status_code == 403
            assert admin.get("/api/pick/feedback/posts").json()["status"] == "auth_required"
            assert admin.post("/api/pick/feedback/plan-links", headers={"X-CSRF-Token": admin.cookies.get("csrf_token")}, json=fake).status_code == 403
            with httpx.Client(base_url=origin, timeout=10, trust_env=False) as anonymous:
                assert anonymous.get("/api/pick/feedback/posts").status_code == 401
            fixture = tmp_path / "frontend.private.json"
            private(fixture, json.dumps({"origin": origin, "cookies": dict(ordinary.cookies), "plan": plan}))
            child_env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
            child_env["PICK_REVIEW_GATEWAY_FIXTURE"] = str(fixture)
            private(tmp_path / "frontend-results.log", "")
            with (tmp_path / "frontend-results.log").open("w") as log:
                frontend = subprocess.Popen(
                    ["pnpm", "exec", "rstest", "run", "tests/unit/core/pick/review-gateway.integration.test.ts"],
                    cwd=root / "frontend",
                    env=child_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                assert frontend.wait(timeout=90) == 0, "Actual frontend HTTP test failed; inspect frontend-results.log"
    finally:
        stop(frontend)
        stop(process)
