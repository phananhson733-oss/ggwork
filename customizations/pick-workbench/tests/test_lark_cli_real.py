"""The lark_cli tool against a real lark-cli binary, when this machine has one (CI does not; the image does).

Needs no Feishu account: help text, embedded skills and the "not configured" answer are all local.
"""

import json
import shutil

import pytest
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli

from ggwork_pick import lark_runner, lark_tool
from ggwork_pick.lark_policy import LarkRefused

pytestmark = pytest.mark.skipif(shutil.which("lark-cli") is None, reason="no lark-cli on this machine")


@pytest.fixture(autouse=True)
def real_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=tmp_path / "home"))
    monkeypatch.delenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raising=False)
    monkeypatch.delenv(lark_runner.RUN_AS_ENV, raising=False)
    lark_runner._RISK_CACHE.clear()
    lark_runner._BINARY_CACHE.clear()
    yield
    lark_runner._RISK_CACHE.clear()
    lark_runner._BINARY_CACHE.clear()


def test_help_labels_read_and_write_commands():
    assert lark_runner.command_risk(("docs", "+fetch")) == "read"
    assert lark_runner.command_risk(("im", "+messages-send")) == "write"
    assert lark_runner.command_risk(("docs", "+update")) == "write"


def test_an_unknown_subcommand_is_not_taken_for_a_read():
    with pytest.raises(LarkRefused, match="只读"):
        lark_runner.command_risk(("docs", "+no-such-command"))


def test_embedded_skills_read_without_credentials():
    completed = lark_runner.run_guide(("skills", "read", "lark-doc"))

    assert completed.exit_code == 0
    assert "lark-cli docs" in completed.stdout


def test_an_unconnected_user_gets_the_capability_center_link():
    completed = lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbCdEf123"))

    assert completed.exit_code != 0
    answer = json.loads(lark_tool.render(completed))
    assert answer["status"] == "not_connected" and lark_tool.CONNECT_LINK in answer["notice"]
    assert lark_cli.lark_cli_config_dir("alice").is_dir()
