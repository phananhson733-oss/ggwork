"""peek_lark_app_config answers "did this user connect Feishu?" without touching the credential tree.

The GGWork pick gate asks on every model call (docs/pick-workbench/lark-personal-auth.md section 4), so the answer
must not create, re-permission or walk a user's credential directories the way read_lark_app_config does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli

NOT_CONFIGURED = {"configured": False, "app_id": None, "brand": None}


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    base = tmp_path / "home"
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=base))
    return base


def _write_config(user_id: str, data: object) -> Path:
    lark_cli.ensure_lark_cli_credential_tree(user_id)
    config = lark_cli.lark_cli_config_dir(user_id) / "config.json"
    config.write_text(json.dumps(data), encoding="utf-8")
    return config


def test_a_user_who_never_connected_gets_no_credential_tree(home):
    assert lark_cli.peek_lark_app_config("alice") == NOT_CONFIGURED
    assert not home.exists() or not any(home.rglob("*"))


def test_peek_answers_what_read_answers(home):
    _write_config("alice", {"currentApp": "b", "apps": [{"appId": "cli_a", "appSecret": "s"}, {"name": "b", "appId": "cli_b", "appSecret": "s", "brand": "lark"}]})
    _write_config("bob", {"apps": [{"appId": "cli_x"}]})

    assert lark_cli.peek_lark_app_config("alice") == {"configured": True, "app_id": "cli_b", "brand": "lark"} == lark_cli.read_lark_app_config("alice")
    assert lark_cli.peek_lark_app_config("bob") == {"configured": False, "app_id": "cli_x", "brand": "feishu"} == lark_cli.read_lark_app_config("bob")


def test_peek_leaves_the_tree_as_it_found_it(home):
    config = _write_config("alice", {"apps": [{"appId": "cli_x", "appSecret": "s"}]})
    tree = config.parent.parent
    tree.chmod(0o755)
    (tree / "data").rmdir()

    assert lark_cli.peek_lark_app_config("alice")["configured"] is True
    assert tree.stat().st_mode & 0o777 == 0o755 and not (tree / "data").exists()


@pytest.mark.parametrize("content", ["not json", "[]", '{"apps": []}', '{"apps": ["x"]}'])
def test_a_broken_config_is_not_a_connection(home, content):
    config = _write_config("alice", {})
    config.write_text(content, encoding="utf-8")
    assert lark_cli.peek_lark_app_config("alice") == NOT_CONFIGURED


def test_a_linked_config_is_not_a_connection(home, tmp_path):
    config = _write_config("alice", {})
    planted = tmp_path / "planted.json"
    planted.write_text(json.dumps({"apps": [{"appId": "cli_x", "appSecret": "s"}]}), encoding="utf-8")
    config.unlink()
    config.symlink_to(planted)
    assert lark_cli.peek_lark_app_config("alice") == NOT_CONFIGURED
