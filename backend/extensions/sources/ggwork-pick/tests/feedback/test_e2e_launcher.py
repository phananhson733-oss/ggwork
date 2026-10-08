"""Safety checks for the local-only browser fixture, never signal real processes."""

import json

import pytest

from . import e2e_launcher as launcher


def test_environment_pins_local_proxy_and_disables_static(monkeypatch):
    for name in ("NEXT_PUBLIC_BACKEND_BASE_URL", "NEXT_PUBLIC_LANGGRAPH_BASE_URL"):
        monkeypatch.setenv(name, "https://outside.invalid")
    monkeypatch.setenv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", "true")
    env = launcher.environment({"gateway": 54321, "frontend": 54322})
    assert env["NEXT_PUBLIC_BACKEND_BASE_URL"] == ""
    assert env["NEXT_PUBLIC_LANGGRAPH_BASE_URL"] == ""
    assert env["NEXT_PUBLIC_STATIC_WEBSITE_ONLY"] == "false"


@pytest.mark.parametrize("change", ["birth", "pgid", "command", "port", "missing"])
def test_stop_skips_pid_reuse_or_incomplete_metadata(tmp_path, monkeypatch, change):
    monkeypatch.setattr(launcher, "HOME", tmp_path)
    saved = {"pid": 99, "pgid": 99, "birth": "Wed Oct 7 09:00:00 2026", "command": "node pnpm exec next dev --port 54322 --hostname 127.0.0.1"}
    (tmp_path / "frontend.process.json").write_text(json.dumps(saved))
    current = dict(saved)
    if change in ("birth", "command"):
        current[change] += " changed"
    elif change == "pgid":
        current[change] = 100
    elif change == "missing":
        (tmp_path / "frontend.process.json").unlink()
    monkeypatch.setattr(launcher, "process_identity", lambda pid: current)
    calls = []
    monkeypatch.setattr(launcher.os, "killpg", lambda *args: calls.append(args))
    assert not launcher.stop_process("frontend", 55555 if change == "port" else 54322)
    assert calls == []


def test_stop_only_signals_matching_group_leader(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "HOME", tmp_path)
    identity = {"pid": 99, "pgid": 99, "birth": "Wed Oct 7 09:00:00 2026", "command": "node pnpm exec next dev --port 54322 --hostname 127.0.0.1"}
    (tmp_path / "frontend.process.json").write_text(json.dumps(identity))
    monkeypatch.setattr(launcher, "process_identity", lambda pid: identity)
    calls = []
    monkeypatch.setattr(launcher.os, "killpg", lambda *args: calls.append(args))
    assert launcher.stop_process("frontend", 54322)
    assert calls == [(99, launcher.signal.SIGTERM)]
