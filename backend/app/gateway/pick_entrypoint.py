"""Start the personal pick deployment with its state on a persistent volume.

PICK_DB_BACKEND must name the database explicitly: ``sqlite`` keeps it on the volume, ``postgres`` points
at PICK_DATABASE_URL. The runtime yaml only ever holds the literal ``$PICK_DATABASE_URL``; AppConfig
resolves it at load time, so no credential is written to the volume.
"""

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qsl, urlsplit

import uvicorn
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HOME = "/data"
RUNTIME_CONFIG = "pick-runtime.yaml"
EXTENSIONS_CONFIG = "extensions_config.json"
BACKENDS = ("sqlite", "postgres")
# One URL feeds asyncpg (through SQLAlchemy) and libpq (psycopg); each rejects the other's SSL query
# parameter, so TLS comes from PGSSLMODE, which both read.
POSTGRES_DATABASE = {
    "backend": "postgres",
    "postgres_url": "$PICK_DATABASE_URL",
    "postgres_schema": "deerflow",
    "pool_size": 3,
    "pool_recycle": 300,
    "command_timeout": 30,
    "checkpoint_channel_mode": "full",
}


def runtime_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The DEER_FLOW_* paths of the pick deployment: values already in ``environ`` win, the rest default.

    Shared by main() and the ``railway ssh`` commands (create_user), whose shells have none of these.
    """
    home = Path(environ.get("DEER_FLOW_HOME") or DEFAULT_HOME).resolve()
    return {
        "DEER_FLOW_HOME": str(home),
        "DEER_FLOW_PROJECT_ROOT": environ.get("DEER_FLOW_PROJECT_ROOT") or str(PROJECT_ROOT),
        "DEER_FLOW_CONFIG_PATH": environ.get("DEER_FLOW_CONFIG_PATH") or str(home / RUNTIME_CONFIG),
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": environ.get("DEER_FLOW_EXTENSIONS_CONFIG_PATH") or str(home / EXTENSIONS_CONFIG),
    }


def prepare_config(home: Path, template: Path, backend: str) -> Path:
    if backend not in BACKENDS:
        raise ValueError(f"unknown database backend {backend!r}")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    template_config = yaml.safe_load(template.read_text())
    if backend == "sqlite":
        database = {**template_config["database"], "sqlite_dir": str(home / "data")}
    else:
        database = dict(POSTGRES_DATABASE)
    # Replacing the value keeps the key order, so sqlite mode writes exactly what it always wrote.
    config = {**template_config, "database": database}
    path = home / RUNTIME_CONFIG
    temporary = path.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    temporary.chmod(0o600)
    temporary.replace(path)
    extensions = home / EXTENSIONS_CONFIG
    if not extensions.exists():
        with extensions.open("x") as stream:
            json.dump({"mcpServers": {}, "skills": {}}, stream)
        extensions.chmod(0o600)
    return path


def _exit(message: str) -> NoReturn:
    print(f"pick_entrypoint: {message}", file=sys.stderr)
    raise SystemExit(1)


def _database_backend(environ: Mapping[str, str]) -> str:
    """Validate the database variables before anything is written; the URL itself is never echoed."""
    backend = environ.get("PICK_DB_BACKEND", "")
    if backend not in BACKENDS:
        _exit("PICK_DB_BACKEND must be set to 'sqlite' or 'postgres'.")
    if backend == "sqlite":
        return backend
    url = environ.get("PICK_DATABASE_URL", "")
    if not url:
        _exit("PICK_DB_BACKEND=postgres needs PICK_DATABASE_URL.")
    shape = "PICK_DATABASE_URL must be a postgresql://user:password@host:port/database URL without a driver suffix."
    try:
        parts = urlsplit(url)
    except ValueError:
        _exit(shape)
    if parts.scheme not in ("postgresql", "postgres") or not parts.hostname:
        _exit(shape)
    if any(key.lower().startswith("ssl") for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        _exit("PICK_DATABASE_URL must not carry ssl* parameters such as sslmode or ssl (asyncpg and libpq share it); set PGSSLMODE=require instead.")
    return backend


def main() -> None:
    backend = _database_backend(os.environ)
    paths = runtime_environment(os.environ)
    home = Path(paths["DEER_FLOW_HOME"])
    # The runtime files are regenerated here, so an explicit path elsewhere would either be overwritten or
    # silently not be the config the gateway was started with.
    for name, owned in (("DEER_FLOW_CONFIG_PATH", home / RUNTIME_CONFIG), ("DEER_FLOW_EXTENSIONS_CONFIG_PATH", home / EXTENSIONS_CONFIG)):
        if Path(paths[name]).resolve() != owned:
            _exit(f"{name} must be unset or {owned}; the entrypoint generates that file.")
    prepare_config(home, PROJECT_ROOT / "config.pick.example.yaml", backend)
    os.environ.update(paths)
    os.environ.setdefault("PICK_RUN_TIMEOUT_SECONDS", "120")
    uvicorn.run("app.gateway.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8001")), workers=1)


if __name__ == "__main__":
    main()
