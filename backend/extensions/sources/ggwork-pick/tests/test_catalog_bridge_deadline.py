"""The credential-owning bridge must receive graceful cancellation, never subprocess.run's SIGKILL timeout."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_bridge_delegates_its_deadline_to_the_isolated_runner(monkeypatch):
    path = Path(__file__).resolve().parents[3] / "customizations/pick-source/scripts/juyuantai/fetch_source.py"
    spec = importlib.util.spec_from_file_location("native_fetch_source", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="complete", stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setenv("PICK_SOURCE_OWNER_ID", "owner")
    assert module.run_cli(["sheets", "+csv-get"]) == "complete"
    args, options = calls.pop()
    assert args[1:3] == ["-m", "ggwork_pick.source_lark"]
    assert options["timeout"] is None
    monkeypatch.delenv("PICK_SOURCE_OWNER_ID")
    module.run_cli(["sheets", "+csv-get"])
    assert calls[0][1]["timeout"] == 180
