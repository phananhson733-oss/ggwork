"""Create/run/stop an isolated localhost feedback browser fixture; never production."""

import argparse
import json
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[4]
HOME = ROOT / "backend/.deer-flow/feedback-e2e"


def private(name, value):
    path = HOME / name
    path.write_text(value)
    path.chmod(0o600)


def environment(ports):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PICK_", "DEER_FLOW_"))}
    env.update(
        PYTHONPATH=":".join(
            str(ROOT / p)
            for p in (
                "backend/packages/harness",
                "backend/packages/extension-api",
                "customizations/pick-workbench",
                "backend",
                "customizations/pick-workbench/tests",
            )
        ),
        DEER_FLOW_HOME=str(HOME / "home"),
        DEER_FLOW_PROJECT_ROOT=str(ROOT),
        DEER_FLOW_CONFIG_PATH=str(HOME / "config.yaml"),
        DEER_FLOW_EXTENSIONS_CONFIG_PATH=str(HOME / "extensions.json"),
        PICK_RUN_TIMEOUT_SECONDS="120",
        FEEDBACK_E2E_HOME=str(HOME),
        PICK_FEEDBACK_E2E_HOME=str(HOME),
        PICK_FEEDBACK_E2E_URL=f"http://127.0.0.1:{ports['frontend']}",
        DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=f"http://127.0.0.1:{ports['gateway']}",
        NEXT_TELEMETRY_DISABLED="1",
        NEXT_PUBLIC_BACKEND_BASE_URL="",
        NEXT_PUBLIC_LANGGRAPH_BASE_URL="",
        NEXT_PUBLIC_STATIC_WEBSITE_ONLY="false",
    )
    return env


def process_identity(pid):
    """Read identity in one ps snapshot; absent or unparsable processes are not ours."""
    raw = subprocess.run(
        ["ps", "-p", str(pid), "-o", "pid=,pgid=,lstart=,command="], capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"}
    ).stdout.strip()
    parts = raw.split(None, 7)
    if len(parts) != 8:
        return None
    try:
        return {"pid": int(parts[0]), "pgid": int(parts[1]), "birth": " ".join(parts[2:7]), "command": parts[7]}
    except ValueError:
        return None


def matches_fixture(identity, name, port):
    if not identity or identity.get("pid") != identity.get("pgid"):
        return False
    tokens = shlex.split(identity.get("command", ""))
    marker = "feedback.e2e_support:app" if name == "gateway" else "next"
    try:
        return marker in tokens and tokens[tokens.index("--port") + 1] == str(port) and "127.0.0.1" in tokens
    except (ValueError, IndexError):
        return False


def stop_process(name, port):
    path = HOME / f"{name}.process.json"
    if not path.exists():
        return False  # Legacy PID-only files cannot establish process ownership.
    saved = json.loads(path.read_text())
    if not {"pid", "pgid", "birth", "command"}.issubset(saved) or not matches_fixture(saved, name, port):
        return False
    current = process_identity(saved["pid"])
    if current != saved:
        return False
    try:
        os.killpg(saved["pgid"], signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    raise RuntimeError("Local fixture did not become ready; inspect private logs")


def up():
    if HOME.exists():
        raise RuntimeError(f"Fixture directory already exists: {HOME}; use existing instance or archive it after stopping")
    HOME.mkdir(parents=True, mode=0o700)
    (HOME / "home").mkdir(mode=0o700)
    ports = {"gateway": free_port(), "frontend": free_port()}
    private("ports.json", json.dumps(ports))
    config = yaml.safe_load((ROOT / "config.pick.example.yaml").read_text())
    config["database"] = {"backend": "sqlite", "sqlite_dir": str(HOME / "home/data"), "checkpoint_channel_mode": "full"}
    config["models"] = [
        {
            "name": "azure-pick",
            "use": "langchain_openai:ChatOpenAI",
            "model": "synthetic-unused",
            "api_key": "synthetic-unused",
            "base_url": "http://127.0.0.1:1/v1",
            "supports_thinking": False,
            "supports_vision": False,
        }
    ]
    private("config.yaml", yaml.safe_dump(config))
    private("extensions.json", '{"mcpServers":{},"skills":{}}')
    private("state.json", '{"mode":"ok","views":150}')
    env = environment(ports)
    commands = {
        "gateway": ([sys.executable, "-m", "uvicorn", "feedback.e2e_support:app", "--host", "127.0.0.1", "--port", str(ports["gateway"])], ROOT / "backend"),
        "frontend": (["pnpm", "exec", "next", "dev", "--port", str(ports["frontend"]), "--hostname", "127.0.0.1"], ROOT / "frontend"),
    }
    for name, (command, cwd) in commands.items():
        with (HOME / f"{name}.log").open("w") as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        private(f"{name}.pid", str(process.pid))
    url = f"http://127.0.0.1:{ports['gateway']}"
    wait_ready(url + "/health/ready")
    wait_ready(f"http://127.0.0.1:{ports['frontend']}/api/v1/auth/setup-status")
    for name in commands:
        identity = process_identity(int((HOME / f"{name}.pid").read_text()))
        if not matches_fixture(identity, name, ports[name]):
            raise RuntimeError("Fixture process ownership could not be verified")
        private(f"{name}.process.json", json.dumps(identity))
    admin_password = "Qa-" + secrets.token_urlsafe(24) + "-9z"
    response = httpx.post(url + "/api/v1/auth/initialize", json={"email": "admin-feedback@example.com", "password": admin_password})
    assert response.status_code == 201, "Admin initialize failed"
    result = subprocess.run(
        [sys.executable, "-m", "app.gateway.auth.create_user", "--email", "qa-feedback@example.com"], cwd=ROOT / "backend", env=env, capture_output=True
    )
    assert result.returncode == 0, "QA account creation failed (output suppressed to protect credentials)"
    cred = HOME / "home/credentials/qa-feedback@example.com.txt"
    first = re.search(r"^password: (.+)$", cred.read_text(), re.M)[1]
    password = "Qa-" + secrets.token_urlsafe(24) + "-9z"
    with httpx.Client(base_url=url) as client:
        assert client.post("/api/v1/auth/login/local", data={"username": "qa-feedback@example.com", "password": first}).status_code == 200
        assert (
            client.post(
                "/api/v1/auth/change-password",
                json={"current_password": first, "new_password": password, "new_email": "qa-feedback@example.com"},
                headers={"X-CSRF-Token": client.cookies["csrf_token"]},
            ).status_code
            == 200
        )
    private("credentials.json", json.dumps({"email": "qa-feedback@example.com", "password": password}))
    cred.unlink()
    print(f"Fixture: {HOME}\nGateway: {url}\nFrontend: http://127.0.0.1:{ports['frontend']}")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "test", "down"))
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if args.command == "up":
        up()
    elif args.command == "test":
        ports = json.loads((HOME / "ports.json").read_text())
        wait_ready(f"http://127.0.0.1:{ports['gateway']}/health/ready")
        raise SystemExit(
            subprocess.run(
                ["pnpm", "exec", "playwright", "test", "--config", "playwright.feedback.config.ts", f"--repeat-each={args.repeat}"],
                cwd=ROOT / "frontend",
                env=environment(ports),
            ).returncode
        )
    else:
        ports = json.loads((HOME / "ports.json").read_text())
        for name in ("frontend", "gateway"):
            if not stop_process(name, ports[name]):
                print(f"Skipped {name}: process identity absent or different")
        print("Stopped matching fixture processes; private files preserved for inspection")


if __name__ == "__main__":
    main()
