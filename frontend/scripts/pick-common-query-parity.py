"""Real Gateway + temporary PostgreSQL differential harness; no model/provider calls.

Run with the repository's installed Python environment. --cluster-file must describe
an existing loopback throwaway PostgreSQL cluster. Secrets stay in mode-0600 files;
only synthetic database/role names, statuses and report paths are printed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def private(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(value)


def validate_cluster_settings(settings: dict) -> str:
    """Reject libpq URL overrides before any connection or CREATE/DROP operation."""
    admin = settings.get("test_pg_url", "")
    parsed = urlsplit(admin)
    if (
        settings.get("purpose") != "throwaway tests only"
        or parsed.scheme not in {"postgresql", "postgres"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Parity requires a local throwaway PostgreSQL URL without query/fragment overrides"
        )
    return admin


@contextmanager
def isolated_pg_environment():
    """libpq environment defaults cannot override the explicitly validated test URL."""
    previous = {key: value for key, value in os.environ.items() if key.startswith("PG")}
    for key in previous:
        os.environ.pop(key)
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("PG"):
                os.environ.pop(key)
        os.environ.update(previous)


def direct_http_client(origin: str):
    """Synthetic Gateway credentials must never transit ambient HTTP proxies."""
    import httpx

    return httpx.Client(base_url=origin, timeout=15, trust_env=False)


def frontend_environment(fixture: Path, origin: str) -> dict[str, str]:
    values = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "TMPDIR")
        if key in os.environ
    }
    return {
        **values,
        "PICK_COMMON_PARITY_FIXTURE": str(fixture),
        "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL": origin,
    }


def require_passing_report(output: str, *, expected_tests: int) -> None:
    """A successful runner exit alone can conceal an entirely skipped fixture suite."""
    report = json.loads(output[output.index("{") :])
    summary = report["summary"]
    if report["status"] != "pass" or any(
        summary[key] != value
        for key, value in {
            "testFiles": 1,
            "tests": expected_tests,
            "passedTests": expected_tests,
            "failedTests": 0,
            "skippedTests": 0,
        }.items()
    ):
        raise ValueError(f"Parity matrix is incomplete or failed: {summary}")


def _run(
    cluster_file: Path, output: Path, gateway_root: Path, candidate_adapters: bool
) -> int:
    import httpx
    import yaml

    settings = json.loads(cluster_file.read_text(encoding="utf-8"))
    admin = validate_cluster_settings(settings)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths = [
        gateway_root / part
        for part in (
            "customizations/pick-workbench/tests/mirror",
            "customizations/pick-workbench/tests",
            "customizations/pick-workbench",
            "backend/packages/extension-api",
            "backend/packages/harness",
            "backend",
        )
    ]
    sys.path[:0] = [str(path) for path in paths]
    import board_fixture
    import pg

    # Expand only the synthetic source world BEFORE production publication.
    # No published mirror is mutated: all adapter/oracle reads remain pinned.
    import gate_world

    original_world = board_fixture.board_world

    def expanded_world(title, youtube_rule, as_of):
        world = original_world(title, youtube_rule, as_of)
        prototype = world.tables["catalog_rows"][0]
        extra = [
            {
                **prototype,
                "row_key": f"parity-{n:04d}",
                "title": "Repeated title",
                "title_cn": f"中文标题 {n}",
                "title_key": f"parity-title-{n}",
                "lang": ["", "2", "10", "英语", "中文"][n % 5],
                "has_signal": False,
                "latest_evidence_on": None,
                "in_site_ids": [],
                "off_on": "2026-09-01" if n % 7 == 0 else None,
                "youtube": n % 2 == 0,
            }
            for n in range(230)
        ]
        world = gate_world.with_table(
            world, "catalog_rows", [*world.tables["catalog_rows"], *extra]
        )
        world = gate_world.with_counts(
            world, catalog_rows=len(world.tables["catalog_rows"])
        )
        world = gate_world.with_manifest(
            world, ("meta", "freshness", "rows"), len(world.tables["catalog_rows"])
        )
        posted = [
            {**row, "archived": True} if row["post_count"] > 0 else row
            for row in world.tables["catalog_posted"]
        ]
        world = gate_world.with_table(world, "catalog_posted", posted)
        signals = [
            {
                **row,
                "payload": {
                    **row["payload"],
                    "h": [*row["payload"]["h"], ["2025-09-07", "9.7–9.13"]],
                    "weeks": 3,
                },
            }
            if row["kind"] == "kw"
            else row
            for row in world.tables["catalog_signals"]
        ]
        return gate_world.with_table(world, "catalog_signals", signals)

    board_fixture.board_world = expanded_world
    cluster = pg.PgCluster(admin)
    fixture = board_fixture.up(
        cluster,
        password=secrets.token_urlsafe(24),
        url_file=output / "reader.private",
        data_dir=output / "pick",
    )
    process = None
    try:
        database_url = cluster.url.set(
            drivername="postgresql", database=fixture["database"]
        ).render_as_string(hide_password=False)
        config = yaml.safe_load(
            (gateway_root / "config.pick.example.yaml").read_text(encoding="utf-8")
        )
        config.update(
            models=[], tools=[], tool_groups=[], extensions={"middlewares": []}
        )
        config["database"] = {
            "backend": "postgres",
            "postgres_url": "$PICK_DATABASE_URL",
            "postgres_schema": "deerflow",
            "checkpoint_channel_mode": "full",
        }
        config["title"] = {"enabled": False}
        config["auth"]["local"]["allow_registration"] = True
        config["plugins"][0]["config"] = {"data_dir": str(output / "pick")}
        private(output / "config.yaml", yaml.safe_dump(config))
        private(
            output / "extensions.json", json.dumps({"mcpServers": {}, "skills": {}})
        )
        # Transport-only test seam: the local PostgreSQL cluster has no TLS. The
        # real Gateway app, sessions, CSRF, route and query service remain intact.
        private(
            output / "parity_gateway.py",
            """import os
from ggwork_pick.query_reader import QueryReader
QueryReader.from_env = classmethod(lambda cls: cls(os.environ["PICK_MIRROR_READER_URL"], ssl=False))
from app.gateway.pick_asgi import app
""",
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "TMPDIR")
            if key in os.environ
        }
        env.update(
            PYTHON_DOTENV_DISABLED="1",
            DEER_FLOW_HOME=str(output / "home"),
            DEER_FLOW_PROJECT_ROOT=str(gateway_root),
            DEER_FLOW_CONFIG_PATH=str(output / "config.yaml"),
            DEER_FLOW_EXTENSIONS_CONFIG_PATH=str(output / "extensions.json"),
            PICK_DATABASE_URL=database_url,
            PICK_DB_BACKEND="postgres",
            PICK_MIRROR_READER_URL=(output / "reader.private").read_text().strip(),
            PGSSLMODE="disable",
            PYTHONPATH=os.pathsep.join([str(output), *map(str, paths)]),
            PYTHONUNBUFFERED="1",
        )
        origin = f"http://127.0.0.1:{port}"
        log = output / "gateway.private.log"
        with log.open("w", encoding="utf-8") as stream:
            os.chmod(log, 0o600)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "parity_gateway:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                ],
                cwd=gateway_root / "backend",
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        with direct_http_client(origin) as client:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Gateway exited before readiness; inspect {log}"
                    )
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError(f"Gateway not ready; inspect {log}")
            initialized = client.post(
                "/api/v1/auth/initialize",
                json={
                    "email": "parity@example.com",
                    "password": "Qa9!" + secrets.token_urlsafe(24),
                },
            )
            if initialized.status_code != 201:
                raise RuntimeError(
                    f"Synthetic account initialization returned {initialized.status_code}"
                )
            state = {
                "reader_url": env["PICK_MIRROR_READER_URL"],
                "gateway_url": origin,
                "cookies": dict(client.cookies),
                "versions": fixture["versions"],
                "owner": initialized.json()["id"],
                "candidate_adapters": candidate_adapters,
            }
            # Auth/CSRF errors are real HTTP responses, not synthetic fixture answers.
            query = {"domain": "catalog", "scope": "full_catalog"}
            with direct_http_client(origin) as anonymous:
                anonymous.cookies.set("csrf_token", "anonymous-parity")
                assert (
                    anonymous.post(
                        "/api/pick/query",
                        headers={"X-CSRF-Token": "anonymous-parity"},
                        json=query,
                    ).status_code
                    == 401
                )
            assert client.post("/api/pick/query", json=query).status_code == 403
            csrf = client.cookies.get("csrf_token")
            assert csrf
            ok = client.post(
                "/api/pick/query", headers={"X-CSRF-Token": csrf}, json=query
            )
            if ok.status_code != 200:
                raise RuntimeError(
                    f"Authenticated common query returned {ok.status_code}; inspect {log}"
                )
            imported = client.post(
                "/api/pick/imports",
                headers={"X-CSRF-Token": csrf},
                data={"kind": "catalog", "source_ref": "synthetic-owner-private"},
                files={
                    "files": (
                        "private.json",
                        json.dumps(
                            [
                                {
                                    "source": "synthetic",
                                    "source_id": "owner-private",
                                    "language": "en",
                                    "title": "Private fixture",
                                }
                            ]
                        ).encode(),
                        "application/json",
                    )
                },
            )
            assert imported.status_code == 201
            owned = client.post(
                "/api/pick/query", headers={"X-CSRF-Token": csrf}, json=query
            )
            assert (
                owned.status_code == 200
                and owned.json()["pin"]["mirror_version"] is None
            )
            with direct_http_client(origin) as other:
                registered = other.post(
                    "/api/v1/auth/register",
                    json={
                        "email": "other@example.com",
                        "password": "Qa9!" + secrets.token_urlsafe(24),
                    },
                )
                assert registered.status_code == 201
                inaccessible = other.post(
                    "/api/pick/query",
                    headers={"X-CSRF-Token": other.cookies.get("csrf_token")},
                    json={**query, "pin": owned.json()["pin"]},
                )
                assert inaccessible.status_code == 404
                ordinary = other.post(
                    "/api/pick/query",
                    headers={"X-CSRF-Token": other.cookies.get("csrf_token")},
                    json=query,
                )
                assert ordinary.status_code == 200
                assert registered.json()["system_role"] == "user"
                state.update(
                    cookies=dict(other.cookies),
                    owner=registered.json()["id"],
                    system_role="user",
                )
            private(output / "session.private.json", json.dumps(state))
        test_env = frontend_environment(output / "session.private.json", origin)
        command = [
            "pnpm",
            "exec",
            "rstest",
            "run",
            "common-query-parity.integration.test.ts",
            "--reporter",
            "json",
            "--silent=true",
        ]
        with (output / "parity-results.log").open("w", encoding="utf-8") as stream:
            tests = subprocess.Popen(
                command,
                cwd=ROOT / "frontend",
                env=test_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                return_code = tests.wait(timeout=240)
            finally:
                if tests.poll() is None:
                    # Only the process group created above; kill every timed-out
                    # test worker before tearing down its Gateway/database.
                    os.killpg(tests.pid, signal.SIGKILL)
                    tests.wait(timeout=5)
        if return_code == 0:
            require_passing_report(
                (output / "parity-results.log").read_text(encoding="utf-8"),
                expected_tests=5,
            )
        print(
            json.dumps(
                {
                    "status": "passed" if return_code == 0 else "failed",
                    "model_runs": 0,
                    "frontend_role": "user",
                    "report": str(output / "parity-results.log"),
                    "versions": fixture["versions"],
                }
            )
        )
        return return_code
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        board_fixture.down(cluster, database=fixture["database"], role=fixture["role"])


def run(
    cluster_file: Path, output: Path, gateway_root: Path, candidate_adapters: bool
) -> int:
    with isolated_pg_environment():
        return _run(cluster_file, output, gateway_root, candidate_adapters)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--gateway-root",
        type=Path,
        default=ROOT,
        help="Development service checkout; final acceptance uses this assembled checkout",
    )
    parser.add_argument(
        "--candidate-adapters",
        action="store_true",
        help="Pre-cutover candidate gate; default validates actual index-exported readers",
    )
    args = parser.parse_args()
    raise SystemExit(
        run(
            args.cluster_file.resolve(),
            args.output.resolve(),
            args.gateway_root.resolve(),
            args.candidate_adapters,
        )
    )
