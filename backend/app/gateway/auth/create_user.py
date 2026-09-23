"""CLI tool to add a local account while self-registration is off.

Usage (inside the gateway container, e.g. over ``railway ssh``):
    cd /app/backend && DEER_FLOW_HOME=/data python -m app.gateway.auth.create_user --email user@example.com
    ... --role admin

The account must finish setup on first login. Its random initial password is written to
``$DEER_FLOW_HOME/credentials/<email>.txt`` (mode 0600) and never printed, so CI / log aggregators
never see the cleartext secret.

Such a shell has none of the DEER_FLOW_* variables pick_entrypoint sets for the gateway process, so the
same defaults are filled here (``pick_entrypoint.runtime_environment``). The runtime yaml the gateway
generated is required and never regenerated; without it the tool exits before opening the database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

import yaml
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.gateway import pick_entrypoint

ROLES = ("user", "admin")


def _email(value: str) -> str:
    """The stored form (the repository lowercases it); it also names the credentials file."""
    email = TypeAdapter(EmailStr).validate_python(value).lower()
    if "/" in email or "\\" in email:
        raise ValueError("path separator in email")
    return email


def _database_config(path: Path):
    """Only the database section: creating an account needs no model keys or extension config."""
    from deerflow.config.app_config import CONFIG_FILE_DATABASE_DEFAULTS, AppConfig
    from deerflow.config.database_config import DatabaseConfig

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Raises ValueError naming only the variable when e.g. $PICK_DATABASE_URL is not in this shell.
    section = AppConfig.resolve_env_variables({**CONFIG_FILE_DATABASE_DEFAULTS, **(data.get("database") or {})})
    return DatabaseConfig.model_validate(section)


def _stage_credentials(directory: Path, email: str, password: str) -> Path:
    """Write the password before the account exists, so a created account never has a lost password.

    A unique 0600 file created with O_EXCL; it replaces ``<email>.txt`` only once the account is stored.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    staged = directory / f".{email}.{secrets.token_hex(4)}.tmp"
    content = f"# DeerFlow account created by create_user\n# Hand the password to its owner through the password manager, then delete this file.\n# The first login asks for setup.\n#\nemail: {email}\npassword: {password}\n"
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return staged


async def _run(email: str, role: str, config_path: Path, credentials: Path) -> int:
    from app.gateway.auth.local_provider import LocalAuthProvider
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config

    try:
        database = _database_config(config_path)
    except ValidationError:
        # The pydantic message would echo input values, and the resolved URL holds the password.
        print(f"Error: the database section of {config_path} is invalid.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if database.backend == "memory":
        print(f"Error: {config_path} uses the memory backend; the account would not persist.", file=sys.stderr)
        return 1

    await init_engine_from_config(database)
    try:
        sf = get_session_factory()
        if sf is None:
            print("Error: persistence engine not available (check config.database).", file=sys.stderr)
            return 1
        provider = LocalAuthProvider(SQLiteUserRepository(sf))
        if await provider.get_user_by_email(email) is not None:
            print(f"Error: user '{email}' already exists.", file=sys.stderr)
            return 1
        password = secrets.token_urlsafe(24)
        staged = _stage_credentials(credentials, email, password)
        try:
            await provider.create_user(email=email, password=password, system_role=role, needs_setup=True)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
    except ValueError as exc:
        # The repository's own uniqueness check ("... already ..."), for an insert racing this one.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        await close_engine()

    target = credentials / f"{email}.txt"
    staged.replace(target)
    print(f"Created {role} account: {email}")
    print(f"Credentials written to: {target} (mode 0600)")
    print("Next login will require setup.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a local account that must finish setup on first login")
    parser.add_argument("--email", required=True, help="Email of the new account")
    parser.add_argument("--role", choices=ROLES, default="user", help="System role (default: user)")
    args = parser.parse_args(argv)

    try:
        email = _email(args.email)
    except ValueError:
        print("Error: --email is not a valid email address.", file=sys.stderr)
        return 1

    paths = pick_entrypoint.runtime_environment(os.environ)
    os.environ.update(paths)
    config_path = Path(paths["DEER_FLOW_CONFIG_PATH"])
    if not config_path.is_file():
        print(
            f"Error: {config_path} does not exist; start the gateway once (python -m app.gateway.pick_entrypoint) so it writes the runtime config.",
            file=sys.stderr,
        )
        return 1
    return asyncio.run(_run(email, args.role, config_path, Path(paths["DEER_FLOW_HOME"]) / "credentials"))


if __name__ == "__main__":
    sys.exit(main())
