"""Pinned (image-bundled) lark-cli: no npm or GitHub at runtime, no Gateway secrets in the CLI's environment.

The GGWork pick image ships one verified lark-cli binary and sets DEER_FLOW_LARK_CLI_PINNED_VERSION; see
docs/pick-workbench/lark-personal-auth.md.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli

PIN = "v1.0.96"
SECRETS = {"PICK_DATABASE_URL": "postgresql://u:secret@db/x", "AZURE_OPENAI_API_KEY": "sk-secret", "PICK_FEED_TOKEN": "feed-secret"}


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    base = tmp_path / "home"
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=base))
    return base


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, PIN)
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    lark_cli._EMBEDDED_SKILLS_CACHE.clear()
    yield
    lark_cli._EMBEDDED_SKILLS_CACHE.clear()


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("pinned lark-cli must not reach the network")

    monkeypatch.setattr(lark_cli.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(lark_cli, "_install_managed_gateway_lark_cli", refuse)
    monkeypatch.setattr(lark_cli, "_download_lark_archive", refuse)
    monkeypatch.setattr(lark_cli, "_resolve_latest_lark_cli_version", refuse)


def _config():
    return SimpleNamespace(sandbox=SimpleNamespace(use="deerflow.sandbox.local:LocalSandboxProvider"))


def _fake_cli(monkeypatch, *, version: str | None = "lark-cli version 1.0.96", skills: list[str] | None = None, calls: list | None = None):
    """subprocess.run stand-in for the bundled binary: --version, skills list and auth status."""

    def run(args, **kwargs):
        if calls is not None:
            calls.append((list(args), kwargs))
        if args[1:] == ["--version"]:
            if version is None:
                raise FileNotFoundError(args[0])
            return subprocess.CompletedProcess(args, 0, version, "")
        if args[1:] == ["skills", "list"]:
            payload = {"ok": True, "skills": [{"name": name} for name in (skills or [])]}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(args, 1, "", json.dumps({"ok": False, "error": {"type": "config", "message": "not configured"}}))

    monkeypatch.setattr(lark_cli.subprocess, "run", run)
    monkeypatch.setattr(lark_cli.shutil, "which", lambda name, path=None: "/usr/local/bin/lark-cli" if name == "lark-cli" else None)


@pytest.mark.parametrize(("raw", "tag"), [("v1.0.96", "v1.0.96"), ("1.0.96", "v1.0.96"), (" v1.0.96 ", "v1.0.96")])
def test_pinned_version_reads_a_release_tag(monkeypatch, raw, tag):
    monkeypatch.setenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raw)
    assert lark_cli.pinned_lark_cli_version() == tag


def test_unset_pin_means_the_upstream_managed_install(monkeypatch):
    monkeypatch.delenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raising=False)
    assert lark_cli.pinned_lark_cli_version() is None


@pytest.mark.parametrize("raw", ["latest", "v1.0", "1.0.96; rm -rf /"])
def test_a_malformed_pin_is_refused(monkeypatch, raw):
    monkeypatch.setenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raw)
    with pytest.raises(ValueError, match=lark_cli.LARK_CLI_PINNED_VERSION_ENV):
        lark_cli.pinned_lark_cli_version()


def test_pinned_env_leaves_gateway_secrets_out(home, pinned):
    env = lark_cli.lark_cli_env("alice")

    assert not set(SECRETS) & set(env)
    assert env["PATH"] == lark_cli.LARK_CLI_MINIMAL_PATH
    assert env["LARKSUITE_CLI_CONFIG_DIR"] == str(lark_cli.lark_cli_config_dir("alice"))
    assert env["LARKSUITE_CLI_DATA_DIR"] == str(lark_cli.lark_cli_data_dir("alice"))
    assert env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] == "1"


def test_pinned_env_for_validation_directories_leaves_gateway_secrets_out(home, pinned, tmp_path):
    env = lark_cli._lark_cli_env_for_directories(config_dir=tmp_path / "c", data_dir=tmp_path / "d")

    assert not set(SECRETS) & set(env)
    assert env["PATH"] == lark_cli.LARK_CLI_MINIMAL_PATH
    assert env["LARKSUITE_CLI_CONFIG_DIR"] == str(tmp_path / "c")


def test_unpinned_env_keeps_the_host_environment(home, monkeypatch):
    monkeypatch.delenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raising=False)
    monkeypatch.setenv("SOME_HOST_SETTING", "kept")

    assert lark_cli.lark_cli_env("alice")["SOME_HOST_SETTING"] == "kept"


def test_pinned_mode_ignores_a_managed_npm_cli(home, pinned, monkeypatch):
    managed = lark_cli.lark_cli_managed_gateway_dir() / "node_modules" / ".bin" / "lark-cli"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n", encoding="utf-8")
    seen: list[str | None] = []
    monkeypatch.setattr(lark_cli.shutil, "which", lambda name, path=None: seen.append(path) or "/usr/local/bin/lark-cli")

    assert lark_cli._resolve_lark_cli_path() == "/usr/local/bin/lark-cli"
    assert seen == [lark_cli.LARK_CLI_MINIMAL_PATH]


def test_pinned_probe_runs_the_cli_without_gateway_secrets(home, pinned, monkeypatch):
    calls: list = []
    _fake_cli(monkeypatch, calls=calls)

    probe = lark_cli.probe_lark_cli()

    assert probe.available and probe.version == "lark-cli version 1.0.96"
    (args, kwargs), *_ = calls
    assert args == ["/usr/local/bin/lark-cli", "--version"]
    assert not set(SECRETS) & set(kwargs["env"])


def test_pinned_install_verifies_the_bundled_cli_without_network(home, pinned, no_network, monkeypatch):
    _fake_cli(monkeypatch, skills=["lark-doc", "lark-shared"])

    result = lark_cli.install_lark_integration("alice", _config())

    assert result.success
    assert result.installed_skills == ("lark-doc", "lark-shared")
    assert PIN in result.message
    assert result.status.installed
    assert not lark_cli.lark_integration_root().exists()


def test_pinned_install_refuses_a_mismatched_binary(home, pinned, no_network, monkeypatch):
    _fake_cli(monkeypatch, version="lark-cli version 1.0.65")

    with pytest.raises(ValueError, match="v1.0.96"):
        lark_cli.install_lark_integration("alice", _config())


def test_pinned_install_refuses_a_missing_binary(home, pinned, no_network, monkeypatch):
    _fake_cli(monkeypatch, version=None)

    with pytest.raises(FileNotFoundError, match="v1.0.96"):
        lark_cli.install_lark_integration("alice", _config())


def test_pinned_status_reports_the_bundled_cli_and_its_embedded_skills(home, pinned, no_network, monkeypatch):
    _fake_cli(monkeypatch, skills=["lark-doc", "lark-im", "lark-shared"])

    status = lark_cli.get_lark_integration_status("alice", _config(), check_latest=True, check_runtime=True)

    assert status.installed
    assert (status.version, status.manifest_version) == (PIN, PIN)
    assert status.latest_available_version is None
    assert not status.runtime_version_mismatch
    assert (status.skills_installed, status.skills_expected) == (3, 3)
    assert status.installed_skills == ("lark-doc", "lark-im", "lark-shared")
    assert status.install_path == "/usr/local/bin/lark-cli"
    assert status.sandbox_runtime_mode == "none"


def test_pinned_status_marks_a_mismatched_binary_not_installed(home, pinned, no_network, monkeypatch):
    _fake_cli(monkeypatch, version="lark-cli version 1.0.65", skills=["lark-doc"])

    status = lark_cli.get_lark_integration_status("alice", _config(), check_latest=True)

    assert not status.installed
    assert status.runtime_version_mismatch
    assert status.skills_installed == 0


def test_embedded_skill_list_is_read_once_per_binary(home, pinned, monkeypatch):
    calls: list = []
    _fake_cli(monkeypatch, skills=["lark-doc"], calls=calls)

    for _ in range(3):
        lark_cli.get_lark_integration_status("alice", _config())

    assert sum(1 for args, _ in calls if args[1:] == ["skills", "list"]) == 1
    assert all(not set(SECRETS) & set(kwargs["env"]) for _, kwargs in calls)
