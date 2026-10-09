"""The host permits a caller's bounded runner without changing its default probe."""

from types import SimpleNamespace

import pytest

from deerflow.integrations import lark_cli


@pytest.mark.parametrize("bounded_runner", [False, True])
def test_probe_preserves_args_and_five_second_cap_with_optional_process_runner(monkeypatch, bounded_runner):
    calls = []
    monkeypatch.setattr(lark_cli, "_resolve_lark_cli_path", lambda: "/synthetic/lark")

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="0.0.0", stderr="")

    def unexpected(*args, **kwargs):
        pytest.fail("the caller's bounded runner must be used for this probe")

    monkeypatch.setattr(lark_cli, "subprocess", SimpleNamespace(run=unexpected if bounded_runner else run))
    probe = lark_cli.probe_lark_cli(**({"process_runner": run} if bounded_runner else {}))
    assert probe.available and probe.version == "0.0.0"
    assert len(calls) == 1 and calls[0][0] == ["/synthetic/lark", "--version"]
    assert calls[0][1]["timeout"] == 5
    assert calls[0][1]["check"] is False
