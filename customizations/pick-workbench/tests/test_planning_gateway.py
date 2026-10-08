"""Real loopback Gateway cookies/CSRF, using isolated SQLite and no configured models."""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml


def test_plan_routes_use_real_gateway_authentication_and_csrf(tmp_path):
    root = Path(__file__).resolve().parents[3]
    config = yaml.safe_load((root / "config.pick.example.yaml").read_text(encoding="utf-8"))
    config.update(models=[], tools=[], tool_groups=[], extensions={"middlewares": []})
    config["database"] = {"backend": "sqlite", "sqlite_dir": str(tmp_path / "db"), "checkpoint_channel_mode": "full"}
    config["title"] = {"enabled": False}
    config["scheduler"] = {"enabled": False}
    config["auth"]["local"]["allow_registration"] = True
    config["plugins"][0]["config"] = {"data_dir": str(tmp_path / "pick")}
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    extensions = tmp_path / "extensions.json"
    extensions.write_text(json.dumps({"mcpServers": {}, "skills": {}}), encoding="utf-8")
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        DEER_FLOW_HOME=str(tmp_path / "home"),
        DEER_FLOW_PROJECT_ROOT=str(root),
        DEER_FLOW_CONFIG_PATH=str(config_file),
        DEER_FLOW_EXTENSIONS_CONFIG_PATH=str(extensions),
        PYTHONPATH=os.pathsep.join(
            str(root / p) for p in ("customizations/pick-workbench", "backend/packages/extension-api", "backend/packages/harness", "backend")
        ),
        PYTHONUNBUFFERED="1",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log = tmp_path / "gateway.log"
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.gateway.pick_asgi:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
            cwd=root / "backend",
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    try:
        with httpx.Client(base_url=origin, timeout=10, trust_env=False) as client:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                assert process.poll() is None, "Isolated Gateway exited before readiness; inspect private test log"
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("Isolated Gateway did not become ready")
            password = "Qa9!" + secrets.token_urlsafe(24)
            initialized = client.post("/api/v1/auth/initialize", json={"email": "planning@example.com", "password": password})
            assert initialized.status_code == 201, initialized.text
            body = {"request_id": "cookie-plan", "title": "Cookie-owned draft", "timezone": "UTC", "rows": []}
            csrf = client.cookies.get("csrf_token")
            assert csrf and client.cookies.get("access_token")
            assert client.post("/api/pick/plans", json=body).status_code == 403
            created = client.post("/api/pick/plans", headers={"X-CSRF-Token": csrf}, json=body)
            assert created.status_code == 200, created.text
            plan = created.json()
            patch = {**body, "request_id": "cookie-patch", "expected_version": 1, "timezone_change": None, "title": "Edited through real session"}
            assert client.patch("/api/pick/plans/" + plan["id"], json=patch).status_code == 403
            saved = client.patch("/api/pick/plans/" + plan["id"], headers={"X-CSRF-Token": csrf}, json=patch)
            assert saved.status_code == 200 and saved.json()["version"] == 2
            with httpx.Client(base_url=origin, timeout=10, trust_env=False) as other:
                assert other.get("/api/pick/plans/" + plan["id"]).status_code == 401
                registered = other.post("/api/v1/auth/register", json={"email": "planning-other@example.com", "password": password})
                assert registered.status_code == 201
                assert other.get("/api/pick/plans/" + plan["id"]).status_code == 404
                assert other.get("/api/pick/plans").json()["items"] == []
                assert other.patch("/api/pick/plans/" + plan["id"], headers={"X-CSRF-Token": other.cookies.get("csrf_token")}, json=patch).status_code == 404
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
