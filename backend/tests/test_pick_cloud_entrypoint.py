import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]
TEMPLATE = BACKEND.parent / "config.pick.example.yaml"
RUNBOOK = BACKEND.parent / "docs/pick-workbench/supabase.md"
PASSWORD = "entrypoint-test-password"
URL = f"postgresql://deerflow_app.ref:{PASSWORD}@pooler.invalid:5432/postgres"
POSTGRES_DATABASE = {
    "backend": "postgres",
    "postgres_url": "$PICK_DATABASE_URL",
    "postgres_schema": "deerflow",
    "pool_size": 3,
    "pool_recycle": 300,
    "command_timeout": 30,
    "checkpoint_channel_mode": "full",
}
# What the gateway runs with when Railway sets none of these (user decision 2026-09-28: thinking on = high, off = low).
MODEL_DEFAULTS = {
    "PICK_LLM_EFFORT_THINKING_ON": "high",
    "PICK_LLM_EFFORT_THINKING_OFF": "low",
    "PICK_LLM_MAX_OUTPUT_TOKENS": "32000",
    "PICK_LLM_REQUEST_TIMEOUT_SECONDS": "300",
    "PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS": "300",
    "PICK_RUN_TIMEOUT_SECONDS": "600",
}
AZURE = {
    "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1/",
    "AZURE_OPENAI_API_KEY": "synthetic-test-key",
    "AZURE_OPENAI_DEPLOYMENT": "synthetic-deployment",
}


def legacy_prepare_config(home: Path, template: Path) -> Path:
    """The entrypoint before PICK_DB_BACKEND, kept verbatim so sqlite mode can be compared byte for byte."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = yaml.safe_load(template.read_text())
    config["database"]["sqlite_dir"] = str(home / "data")
    path = home / "pick-runtime.yaml"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    temporary.chmod(0o600)
    temporary.replace(path)
    extensions = home / "extensions_config.json"
    if not extensions.exists():
        with extensions.open("x") as stream:
            json.dump({"mcpServers": {}, "skills": {}}, stream)
        extensions.chmod(0o600)
    return path


@pytest.fixture
def environ(monkeypatch, tmp_path):
    """A process environment with no DEER_FLOW_* or PICK_* variables, restored afterwards.

    main() writes DEER_FLOW_* into os.environ itself; a snapshot restores what monkeypatch never saw.
    """
    saved = dict(os.environ)
    for name in list(os.environ):
        if name.startswith(("DEER_FLOW_", "PICK_")):
            del os.environ[name]
    yield os.environ
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def served(monkeypatch):
    """Replace uvicorn.run so main() returns instead of serving."""
    from app.gateway import pick_entrypoint

    calls = []
    monkeypatch.setattr(pick_entrypoint.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    return calls


def test_cloud_config_uses_persistent_home_and_preserves_extensions(tmp_path):
    from app.gateway.pick_entrypoint import prepare_config

    home = tmp_path / "data"
    home.mkdir()
    extensions = home / "extensions_config.json"
    extensions.write_text('{"mcpServers":{},"skills":{"synthetic":{}}}')
    path = prepare_config(home, TEMPLATE, backend="sqlite")
    config = yaml.safe_load(path.read_text())
    assert config["database"]["sqlite_dir"] == str(home / "data")
    assert config["models"][0]["name"] == "azure-pick"
    assert config["models"][0]["api_key"] == "$AZURE_OPENAI_API_KEY"
    assert "synthetic" in json.loads(extensions.read_text())["skills"]


def test_sqlite_output_is_byte_identical_to_the_previous_entrypoint(tmp_path):
    from app.gateway.pick_entrypoint import prepare_config

    before = legacy_prepare_config(tmp_path / "before", TEMPLATE)
    after = prepare_config(tmp_path / "after", TEMPLATE, backend="sqlite")
    expected = before.read_bytes().replace(str(tmp_path / "before").encode(), str(tmp_path / "after").encode())
    assert after.read_bytes() == expected
    assert (after.stat().st_mode & 0o777) == 0o600
    assert (tmp_path / "after" / "extensions_config.json").read_bytes() == (tmp_path / "before" / "extensions_config.json").read_bytes()


def test_postgres_config_names_the_url_variable_and_drops_sqlite_dir(tmp_path):
    from app.gateway.pick_entrypoint import prepare_config

    home = tmp_path / "home"
    path = prepare_config(home, TEMPLATE, backend="postgres")
    config = yaml.safe_load(path.read_text())
    template = yaml.safe_load(TEMPLATE.read_text())
    assert config["database"] == POSTGRES_DATABASE
    assert list(config) == list(template)
    assert {key: value for key, value in config.items() if key != "database"} == {key: value for key, value in template.items() if key != "database"}
    assert "postgres_url: $PICK_DATABASE_URL" in path.read_text()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert not (home / "data").exists()


def test_prepare_config_rejects_an_unknown_backend(tmp_path):
    from app.gateway.pick_entrypoint import prepare_config

    with pytest.raises(ValueError):
        prepare_config(tmp_path / "home", TEMPLATE, backend="mysql")
    assert not (tmp_path / "home" / "pick-runtime.yaml").exists()


@pytest.mark.parametrize("value", [None, "", "mysql", "SQLite", " postgres"])
def test_main_requires_an_explicit_backend(environ, served, tmp_path, capsys, value):
    from app.gateway.pick_entrypoint import main

    environ["DEER_FLOW_HOME"] = str(tmp_path / "home")
    if value is not None:
        environ["PICK_DB_BACKEND"] = value
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code not in (0, None)
    assert "PICK_DB_BACKEND" in capsys.readouterr().err
    assert served == []
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize("value", [None, "", "mysql", "SQLite", " postgres"])
def test_main_requires_an_explicit_backend_even_with_a_database_url(environ, served, tmp_path, capsys, value):
    # A URL alone does not pick PostgreSQL; the error names both accepted values.
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DATABASE_URL=URL)
    if value is not None:
        environ["PICK_DB_BACKEND"] = value
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code not in (0, None)
    assert "'sqlite' or 'postgres'" in capsys.readouterr().err
    assert served == []
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize("url", [None, ""])
def test_main_postgres_requires_the_url(environ, served, tmp_path, capsys, url):
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="postgres")
    if url is not None:
        environ["PICK_DATABASE_URL"] = url
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code not in (0, None)
    assert "PICK_DATABASE_URL" in capsys.readouterr().err
    assert served == []
    assert not (tmp_path / "home").exists()


def test_main_postgres_names_the_missing_url(environ, served, tmp_path, capsys):
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="postgres")
    with pytest.raises(SystemExit):
        main()
    assert "needs PICK_DATABASE_URL" in capsys.readouterr().err


@pytest.mark.parametrize(
    "query",
    ["?sslmode=require", "?ssl=true", "?application_name=pick&sslmode=disable", "?SSLMODE=require", "?sslrootcert=/ca.pem"],
)
def test_main_rejects_ssl_parameters_in_the_url(environ, served, tmp_path, capsys, query):
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="postgres", PICK_DATABASE_URL=URL + query)
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code not in (0, None)
    err = capsys.readouterr().err
    assert "PGSSLMODE" in err
    assert PASSWORD not in err
    assert served == []
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize("url", ["postgresql+asyncpg://u:p@h/db", "mysql://u:p@h/db", "not a url", "postgresql://u:p@[::1/db", "postgresql:///db"])
def test_main_rejects_a_url_both_drivers_cannot_share(environ, served, tmp_path, capsys, url):
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="postgres", PICK_DATABASE_URL=url)
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code not in (0, None)
    assert "PICK_DATABASE_URL" in capsys.readouterr().err
    assert served == []


def test_main_postgres_writes_the_placeholder_not_the_secret(environ, served, tmp_path):
    from app.gateway.pick_entrypoint import main

    home = tmp_path / "home"
    environ.update(DEER_FLOW_HOME=str(home), PICK_DB_BACKEND="postgres", PICK_DATABASE_URL=URL)
    main()
    path = home / "pick-runtime.yaml"
    text = path.read_text()
    assert PASSWORD not in text
    assert "pooler.invalid" not in text
    assert yaml.safe_load(text)["database"] == POSTGRES_DATABASE
    assert (path.stat().st_mode & 0o777) == 0o600
    assert environ["DEER_FLOW_CONFIG_PATH"] == str(path)
    assert len(served) == 1


def test_main_sqlite_writes_what_the_previous_entrypoint_wrote(environ, served, tmp_path):
    from app.gateway.pick_entrypoint import main

    home = tmp_path / "home"
    environ.update(DEER_FLOW_HOME=str(home), PICK_DB_BACKEND="sqlite")
    main()
    before = legacy_prepare_config(tmp_path / "before", TEMPLATE)
    expected = before.read_bytes().replace(str(tmp_path / "before").encode(), str(home).encode())
    assert (home / "pick-runtime.yaml").read_bytes() == expected
    assert len(served) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_main_serves_the_gateway_behind_the_json_body_sanitizer(environ, served, tmp_path, backend):
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND=backend, PICK_DATABASE_URL=URL)
    main()
    assert served[0][0] == ("app.gateway.pick_asgi:app",)


def test_main_fills_the_shared_defaults(environ, served, tmp_path):
    from app.gateway.pick_entrypoint import PROJECT_ROOT, main

    home = tmp_path / "home"
    environ.update(DEER_FLOW_HOME=str(home), PICK_DB_BACKEND="sqlite")
    main()
    assert {name: value for name, value in environ.items() if name.startswith("DEER_FLOW_")} == {
        "DEER_FLOW_HOME": str(home),
        "DEER_FLOW_PROJECT_ROOT": str(PROJECT_ROOT),
        "DEER_FLOW_CONFIG_PATH": str(home / "pick-runtime.yaml"),
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": str(home / "extensions_config.json"),
    }


@pytest.mark.parametrize("name", ["DEER_FLOW_CONFIG_PATH", "DEER_FLOW_EXTENSIONS_CONFIG_PATH"])
def test_main_refuses_to_write_over_a_config_it_does_not_own(environ, served, tmp_path, capsys, name):
    from app.gateway.pick_entrypoint import main

    foreign = tmp_path / "config.yaml"
    foreign.write_text("hand-written\n")
    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="sqlite")
    environ[name] = str(foreign)
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code not in (0, None)
    assert name in capsys.readouterr().err
    assert foreign.read_text() == "hand-written\n"
    assert served == []


def test_runtime_environment_fills_only_what_is_missing(tmp_path):
    from app.gateway.pick_entrypoint import PROJECT_ROOT, runtime_environment

    assert runtime_environment({}) == {
        "DEER_FLOW_HOME": "/data",
        "DEER_FLOW_PROJECT_ROOT": str(PROJECT_ROOT),
        "DEER_FLOW_CONFIG_PATH": "/data/pick-runtime.yaml",
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": "/data/extensions_config.json",
    }
    explicit = {
        "DEER_FLOW_HOME": str(tmp_path / "home"),
        "DEER_FLOW_PROJECT_ROOT": str(tmp_path),
        "DEER_FLOW_CONFIG_PATH": str(tmp_path / "other.yaml"),
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": str(tmp_path / "other.json"),
    }
    source = {**explicit, "UNRELATED": "1"}
    assert runtime_environment(source) == explicit
    assert source == {**explicit, "UNRELATED": "1"}
    assert PROJECT_ROOT == Path(__file__).resolve().parents[2]


def test_runtime_environment_resolves_a_relative_home(tmp_path, monkeypatch):
    from app.gateway.pick_entrypoint import runtime_environment

    monkeypatch.chdir(tmp_path)
    assert runtime_environment({"DEER_FLOW_HOME": "vol"})["DEER_FLOW_HOME"] == str(tmp_path.resolve() / "vol")


# What ``uvicorn.run("app.gateway.pick_asgi:app")`` does before serving: importing the module builds the app,
# and create_app() resolves the runtime yaml's $VARIABLES. load_dotenv() is disabled because a fresh worktree has
# no .env above it; on a developer checkout it finds the repo's git-ignored .env and hides a missing variable.
# The served object must be the real gateway behind the JSON body sanitizer (plan 6.7).
LAPTOP_START = """
import importlib

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
from app.gateway import pick_entrypoint


def serve(target, **kwargs):
    module, _, name = target.partition(":")
    served = getattr(importlib.import_module(module), name)
    from app.gateway.app import app as gateway
    from app.gateway.json_body_sanitizer import JsonBodySanitizer

    assert isinstance(served, JsonBodySanitizer) and served.app is gateway, target


pick_entrypoint.uvicorn.run = serve
pick_entrypoint.main()
"""


def laptop_cutover_step(runbook: str) -> str:
    """Runbook section 8, step 3: the gateway runs once on the operator's laptop to create the admin."""
    cutover = runbook.split("\n## 8. ", 1)[1].split("\n## 9. ", 1)[0]
    return cutover.split("\n3. **", 1)[1].split("\n4. **", 1)[0]


def exported_variables(step: str) -> dict[str, str | None]:
    """NAME -> value of every ``export`` in the step; None for a name whose value ``read`` takes from the prompt."""
    variables = {}
    for line in step.splitlines():
        if "export " in line:
            for word in line.split("export ", 1)[1].split("#", 1)[0].split():
                name, _, value = word.partition("=")
                variables[name] = value or None
    return variables


def laptop_row_variables(runbook: str) -> set[str]:
    row = next(line for line in runbook.splitlines() if line.startswith("| 本机，只在切换第 3 步 |"))
    return {name.strip("`") for name in row.split("|")[2].replace("、", " ").split()}


def test_the_laptop_cutover_step_starts_the_gateway_with_only_what_it_exports(tmp_path):
    """Runbook 8.3.3 runs pick_entrypoint in a fresh git worktree, with no repo .env to fill in the model keys."""
    runbook = RUNBOOK.read_text()
    variables = exported_variables(laptop_cutover_step(runbook))
    assert set(variables) == laptop_row_variables(runbook)
    assert variables["PICK_DATABASE_URL"] is None
    env = {name: os.environ[name] for name in ("PATH", "HOME", "TMPDIR") if name in os.environ}
    env.update({name: value for name, value in variables.items() if value is not None})
    env.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DATABASE_URL=URL)
    result = subprocess.run([sys.executable, "-c", LAPTOP_START], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    assert PASSWORD not in result.stdout + result.stderr


def test_the_pick_image_and_compose_serve_the_sanitized_gateway_with_the_postgres_drivers():
    # The workbench CI installs --extra postgres itself, so only these files show what the deployment gets.
    dockerfile = (BACKEND.parent / "docker/Dockerfile.pick-gateway").read_text()
    assert "uv sync --locked --no-dev --extra postgres" in dockerfile
    assert 'CMD ["sh", "-c", "cd backend && python -m app.gateway.pick_entrypoint"]' in dockerfile
    compose = yaml.safe_load((BACKEND.parent / "docker/docker-compose.pick.yaml").read_text())
    command = " ".join(compose["services"]["gateway"]["command"])
    assert "uvicorn app.gateway.pick_asgi:app " in command


def placeholders(node, prefix: str) -> set[str]:
    """The variable names of every ``$NAME`` value under ``node`` that starts with ``prefix``; comments are not values."""
    if isinstance(node, dict):
        return set().union(*(placeholders(value, prefix) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(placeholders(value, prefix) for value in node))
    return {node[1:]} if isinstance(node, str) and node.startswith(prefix) else set()


def test_main_fills_the_model_defaults(environ, served, tmp_path):
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="sqlite")
    main()
    assert {name: environ.get(name) for name in MODEL_DEFAULTS} == MODEL_DEFAULTS
    assert len(served) == 1


def test_main_keeps_the_model_settings_railway_sets(environ, served, tmp_path):
    from app.gateway.pick_entrypoint import main

    chosen = {
        "PICK_LLM_EFFORT_THINKING_ON": "xhigh",
        "PICK_LLM_EFFORT_THINKING_OFF": "minimal",
        "PICK_LLM_MAX_OUTPUT_TOKENS": "64000",
        "PICK_LLM_REQUEST_TIMEOUT_SECONDS": "420.5",
        "PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS": "360",
        "PICK_RUN_TIMEOUT_SECONDS": "900",
    }
    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="sqlite", **chosen)
    main()
    assert {name: environ.get(name) for name in MODEL_DEFAULTS} == chosen


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PICK_LLM_EFFORT_THINKING_ON", "hgih"),
        ("PICK_LLM_EFFORT_THINKING_ON", ""),
        ("PICK_LLM_EFFORT_THINKING_OFF", "HIGH"),
        ("PICK_LLM_EFFORT_THINKING_OFF", " low"),
        ("PICK_LLM_MAX_OUTPUT_TOKENS", "0"),
        ("PICK_LLM_MAX_OUTPUT_TOKENS", "32k"),
        ("PICK_LLM_MAX_OUTPUT_TOKENS", "1.5"),
        # Python's int() reads full-width digits, pydantic does not: the gateway would boot and fail the first request.
        ("PICK_LLM_MAX_OUTPUT_TOKENS", "\uff13\uff12\uff10\uff10\uff10"),
        ("PICK_LLM_REQUEST_TIMEOUT_SECONDS", "\uff13\uff10\uff10"),
        ("PICK_LLM_REQUEST_TIMEOUT_SECONDS", "0"),
        ("PICK_LLM_REQUEST_TIMEOUT_SECONDS", "nan"),
        ("PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS", "-1"),
        ("PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS", "five minutes"),
        ("PICK_RUN_TIMEOUT_SECONDS", "inf"),
        ("PICK_RUN_TIMEOUT_SECONDS", ""),
    ],
)
def test_main_refuses_a_model_setting_the_gateway_cannot_use(environ, served, tmp_path, capsys, name, value):
    # Checked before anything is written: a typo on Railway stops the deploy instead of failing each request.
    from app.gateway.pick_entrypoint import main

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="sqlite")
    environ[name] = value
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code not in (0, None)
    assert name in capsys.readouterr().err
    assert served == []
    assert not (tmp_path / "home").exists()


def test_the_template_reads_every_model_setting_from_the_environment():
    from app.gateway.pick_entrypoint import MODEL_DEFAULTS as defaults

    assert defaults == MODEL_DEFAULTS
    model = yaml.safe_load(TEMPLATE.read_text())["models"][0]
    assert model["when_thinking_enabled"] == {"reasoning": {"effort": "$PICK_LLM_EFFORT_THINKING_ON"}}
    assert model["when_thinking_disabled"] == {"reasoning": {"effort": "$PICK_LLM_EFFORT_THINKING_OFF"}}
    assert model["reasoning"] == {"effort": "$PICK_LLM_EFFORT_THINKING_OFF"}
    assert model["max_tokens"] == "$PICK_LLM_MAX_OUTPUT_TOKENS"
    assert model["request_timeout"] == "$PICK_LLM_REQUEST_TIMEOUT_SECONDS"
    assert model["stream_chunk_timeout"] == "$PICK_LLM_STREAM_CHUNK_TIMEOUT_SECONDS"
    # The run timeout is read by the gateway process, not the yaml; every other default feeds a placeholder.
    assert placeholders(yaml.safe_load(TEMPLATE.read_text()), "$PICK_") == set(MODEL_DEFAULTS) - {"PICK_RUN_TIMEOUT_SECONDS"}


def test_pick_compose_passes_the_same_model_defaults():
    compose = yaml.safe_load((BACKEND.parent / "docker/docker-compose.pick.yaml").read_text())
    environment = compose["services"]["gateway"]["environment"]
    assert {name: environment.get(name) for name in MODEL_DEFAULTS} == {name: f"${{{name}:-{value}}}" for name, value in MODEL_DEFAULTS.items()}


@pytest.mark.parametrize(("thinking", "effort"), [(True, "high"), (False, "low")])
def test_the_defaults_build_the_model_the_deployment_wants(environ, served, tmp_path, thinking, effort):
    # The whole path Railway takes: entrypoint defaults -> runtime yaml -> AppConfig's $VAR resolution -> the client.
    from app.gateway.pick_entrypoint import main
    from deerflow.config.app_config import AppConfig
    from deerflow.models.factory import create_chat_model

    environ.update(DEER_FLOW_HOME=str(tmp_path / "home"), PICK_DB_BACKEND="sqlite", **AZURE)
    main()
    config = AppConfig.from_file(environ["DEER_FLOW_CONFIG_PATH"])
    model = create_chat_model("azure-pick", thinking_enabled=thinking, app_config=config, attach_tracing=False)
    assert model.reasoning == {"effort": effort}
    assert model.max_tokens == 32000
    assert model.request_timeout == 300.0 and type(model.request_timeout) is float
    assert model.stream_chunk_timeout == 300.0
    assert model.max_retries == 0
