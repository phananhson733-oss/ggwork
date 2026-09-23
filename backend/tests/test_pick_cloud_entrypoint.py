import json
import os
from pathlib import Path

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[2] / "config.pick.example.yaml"
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
