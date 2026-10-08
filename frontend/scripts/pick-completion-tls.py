"""Create/stop one explicitly owned loopback TLS PostgreSQL fixture; no downloads.

The cluster stays alive for caller-controlled reuse. Teardown requires its exact
private descriptor and stops only that descriptor's newly initialized data dir.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from urllib.parse import quote


def private(path: Path, text: str):
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(text)


def command(args, log: Path, env=None):
    with log.open("a", encoding="utf-8") as stream:
        os.chmod(log, 0o600)
        subprocess.run(
            args,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )


def create(output: Path, binaries: Path):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    log = output / "setup.private.log"
    for name in ("initdb", "pg_ctl", "psql"):
        if not (binaries / name).is_file():
            raise ValueError("Existing PostgreSQL binaries required")
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "TMPDIR")
        if key in os.environ
    }
    password = secrets.token_urlsafe(32)
    private(output / "password.private", password)
    private(
        output / "server.ext",
        "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n",
    )
    command(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=Owned Synthetic QA CA",
            "-keyout",
            str(output / "ca.key"),
            "-out",
            str(output / "ca.crt"),
        ],
        log,
        env,
    )
    command(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(output / "server.key"),
            "-out",
            str(output / "server.csr"),
        ],
        log,
        env,
    )
    command(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(output / "server.csr"),
            "-CA",
            str(output / "ca.crt"),
            "-CAkey",
            str(output / "ca.key"),
            "-CAcreateserial",
            "-days",
            "2",
            "-extfile",
            str(output / "server.ext"),
            "-out",
            str(output / "server.crt"),
        ],
        log,
        env,
    )
    for name in ("ca.key", "server.key"):
        (output / name).chmod(0o600)
    data = output / "data"
    command(
        [
            str(binaries / "initdb"),
            "-D",
            str(data),
            "-U",
            "synthetic_qa",
            "--pwfile",
            str(output / "password.private"),
            "--auth-host=scram-sha-256",
            "--auth-local=trust",
            "--encoding=UTF8",
            "--no-locale",
        ],
        log,
        env,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (data / "postgresql.conf").open("a", encoding="utf-8") as stream:
        stream.write(
            f"\nlisten_addresses='127.0.0.1'\nport={port}\nunix_socket_directories=''\nssl=on\nssl_cert_file='{output / 'server.crt'}'\nssl_key_file='{output / 'server.key'}'\n"
        )
    private(
        data / "pg_hba.conf",
        "local all all trust\nhostnossl all all 127.0.0.1/32 reject\nhostssl all all 127.0.0.1/32 scram-sha-256\n",
    )
    marker = secrets.token_hex(16)
    private(output / "owned-fixture.marker", marker)
    descriptor = {
        "purpose": "throwaway tests only",
        "fixture": "pick-completion-owned-tls-v1",
        "marker": marker,
        "bin": str(binaries),
        "data": str(data),
        "port": port,
        "test_pg_url": f"postgresql://synthetic_qa:{quote(password)}@127.0.0.1:{port}/postgres",
        "tls_ca_file": str(output / "ca.crt"),
    }
    private(output / "test-env.json", json.dumps(descriptor))
    command(
        [
            str(binaries / "pg_ctl"),
            "-D",
            str(data),
            "-l",
            str(output / "postgres.private.log"),
            "-w",
            "start",
        ],
        log,
        env,
    )
    print(
        json.dumps(
            {
                "status": "started",
                "port": port,
                "descriptor": str(output / "test-env.json"),
            }
        )
    )


def stop(descriptor: Path):
    state = json.loads(descriptor.read_text(encoding="utf-8"))
    root = descriptor.parent.resolve()
    if (
        state.get("fixture") != "pick-completion-owned-tls-v1"
        or (root / "owned-fixture.marker").read_text() != state.get("marker")
        or Path(state["data"]).resolve() != root / "data"
    ):
        raise ValueError(
            "Refusing to stop a cluster without its exact ownership marker"
        )
    command(
        [
            str(Path(state["bin"]) / "pg_ctl"),
            "-D",
            state["data"],
            "-m",
            "fast",
            "-w",
            "stop",
        ],
        root / "setup.private.log",
    )
    print(json.dumps({"status": "stopped", "descriptor": str(descriptor)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="operation", required=True)
    up = sub.add_parser("create")
    up.add_argument("--output", required=True, type=Path)
    up.add_argument("--bin", required=True, type=Path)
    down = sub.add_parser("stop")
    down.add_argument("--descriptor", required=True, type=Path)
    args = parser.parse_args()
    if args.operation == "create":
        create(args.output.resolve(), args.bin.resolve())
    else:
        stop(args.descriptor.resolve())
