"""Start the personal pick deployment with its state on a persistent volume.

PICK_DB_BACKEND must name the database explicitly: ``sqlite`` keeps it on the volume, ``postgres`` points
at PICK_DATABASE_URL. The runtime yaml only ever holds the literal ``$PICK_DATABASE_URL``; AppConfig
resolves it at load time, so no credential is written to the volume. The model's effort, output cap and
timeouts are ``$PICK_LLM_*`` placeholders in the template too, so Railway variables tune them without a rebuild.
"""

import json
import math
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
# What the gateway runs with when Railway sets none of these. Every PICK_LLM_* name is a placeholder in
# config.pick.example.yaml, which AppConfig refuses to load without it; PICK_RUN_TIMEOUT_SECONDS bounds a whole turn
# (host watchdog and the extension's own deadline). User decision 2026-09-28: thinking on = high, off = low (titles
# and other background calls run with thinking off).
MODEL_DEFAULTS = {
    "PICK_LLM_EFFORT_THINKING_ON": "high",
    "PICK_LLM_EFFORT_THINKING_OFF": "low",
    # Responses max_output_tokens, reasoning included.
    "PICK_LLM_MAX_OUTPUT_TOKENS": "32000",
    # Also the longest silence between two streamed bytes, and a high-effort call can reason that long.
    "PICK_LLM_REQUEST_TIMEOUT_SECONDS": "300",
    "PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS": "300",
    "PICK_RUN_TIMEOUT_SECONDS": "600",
}
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")


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


def _positive_number(name: str, value: str, *, integer: bool) -> None:
    # Python reads full-width digits and pydantic does not, so only ASCII passes here as it would there.
    try:
        number = (int(value) if integer else float(value)) if value.isascii() else None
    except ValueError:
        number = None
    if number is None or not math.isfinite(number) or number <= 0:
        kind = "a positive whole number" if integer else "a positive number of seconds"
        _exit(f"{name} must be {kind}, got {value!r}.")


def _model_settings(environ: Mapping[str, str]) -> dict[str, str]:
    """MODEL_DEFAULTS overlaid with what the environment sets, each value checked before anything is written.

    A value the provider or the run watchdog would reject fails the deploy here instead of every request later.
    """
    settings = {name: environ.get(name, default) for name, default in MODEL_DEFAULTS.items()}
    for name, value in settings.items():
        if name.startswith("PICK_LLM_EFFORT_"):
            if value not in REASONING_EFFORTS:
                _exit(f"{name} must be one of {', '.join(REASONING_EFFORTS)}, got {value!r}.")
        else:
            _positive_number(name, value, integer=name == "PICK_LLM_MAX_OUTPUT_TOKENS")
    return settings


def main() -> None:
    backend = _database_backend(os.environ)
    model_settings = _model_settings(os.environ)
    paths = runtime_environment(os.environ)
    home = Path(paths["DEER_FLOW_HOME"])
    # The runtime files are regenerated here, so an explicit path elsewhere would either be overwritten or
    # silently not be the config the gateway was started with.
    for name, owned in (("DEER_FLOW_CONFIG_PATH", home / RUNTIME_CONFIG), ("DEER_FLOW_EXTENSIONS_CONFIG_PATH", home / EXTENSIONS_CONFIG)):
        if Path(paths[name]).resolve() != owned:
            _exit(f"{name} must be unset or {owned}; the entrypoint generates that file.")
    prepare_config(home, PROJECT_ROOT / "config.pick.example.yaml", backend)
    os.environ.update(paths)
    os.environ.update(model_settings)
    # The gateway behind the JSON body sanitizer: NUL and lone surrogates in a message would pass the routes and
    # then fail the host's write on PostgreSQL (plan 6.7). Both backends get it so they behave alike.
    uvicorn.run("app.gateway.pick_asgi:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8001")), workers=1)


if __name__ == "__main__":
    main()
