"""The lark_cli tool the model sees (docs/pick-workbench/lark-personal-auth.md section 3)."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from deerflow.integrations import lark_cli
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

from ggwork_pick import lark_runner, lark_tool
from ggwork_pick.context import PickTask
from ggwork_pick.lark_runner import Completed

CONNECTED = {"configured": True, "app_id": "cli_x", "brand": "feishu"}


def _runtime(user_id="alice", kind="lead"):
    store = ExtensionData("task1")
    store.set(PickTask(SimpleNamespace(), TaskInfo("task1", "run1", "thread1", kind)))
    return SimpleNamespace(context={"user_id": user_id, EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call1")


class Runner:
    def __init__(self, *, risk="read", result=Completed(0, '{"ok": true, "data": {"title": "周报"}}', "")):
        self.risk = risk
        self.result = result
        self.calls = []
        self.timeouts = []

    def install(self, monkeypatch):
        def risk(path, *, timeout):
            self.calls.append(("risk", path))
            self.timeouts.append(timeout)
            return self.risk

        def guide(args, *, timeout):
            self.calls.append(("guide", args))
            self.timeouts.append(timeout)
            return self.result

        def user(user_id, args, *, timeout):
            self.calls.append(("user", user_id, args))
            self.timeouts.append(timeout)
            return self.result

        monkeypatch.setattr(lark_runner, "command_risk", risk)
        monkeypatch.setattr(lark_runner, "run_guide", guide)
        monkeypatch.setattr(lark_runner, "run_for_user", user)
        return self


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(
        lark_cli, "peek_lark_app_config", lambda user_id: CONNECTED if user_id == "alice" else {"configured": False, "app_id": None, "brand": None}
    )


async def _call(args, runtime=None):
    return await lark_tool.lark_cli_tool.coroutine(argv=args, runtime=runtime or _runtime())


@pytest.mark.asyncio
async def test_a_read_command_runs_with_the_users_credentials(monkeypatch, connected):
    runner = Runner().install(monkeypatch)

    answer = await _call(["docs", "+fetch", "--doc", "https://example.feishu.cn/docx/AbC"])

    assert json.loads(answer) == {"ok": True, "data": {"title": "周报"}}
    assert runner.calls == [("risk", ("docs", "+fetch")), ("user", "alice", ("docs", "+fetch", "--doc", "https://example.feishu.cn/docx/AbC"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("risk", ["write", "high-risk-write"])
async def test_writes_are_refused_before_anything_runs_with_credentials(monkeypatch, connected, risk):
    runner = Runner(risk=risk).install(monkeypatch)

    answer = json.loads(await _call(["im", "+messages-send", "--chat-id", "oc_x", "--text", "hi"]))

    assert answer["status"] == "rejected" and risk in answer["notice"]
    assert [call[0] for call in runner.calls] == ["risk"]


@pytest.mark.asyncio
async def test_policy_refusals_run_nothing(monkeypatch, connected):
    runner = Runner().install(monkeypatch)

    answer = json.loads(await _call(["config", "show"]))

    assert answer["status"] == "rejected" and "不开放" in answer["notice"]
    assert runner.calls == []


@pytest.mark.asyncio
async def test_commands_run_on_the_lark_threads_not_the_shared_pool(monkeypatch, connected):
    runner = Runner().install(monkeypatch)
    threads = []
    original = lark_runner.run_for_user

    def recording(*args, **kwargs):
        threads.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr(lark_runner, "run_for_user", recording)
    await _call(["docs", "+fetch", "--doc", "AbC"])

    assert runner.calls[-1][0] == "user"
    assert [name.startswith(lark_runner.THREAD_NAME_PREFIX) for name in threads] == [True]


@pytest.mark.asyncio
async def test_guides_need_no_connection(monkeypatch):
    runner = Runner(result=Completed(0, "# lark-doc", "")).install(monkeypatch)
    monkeypatch.setattr(lark_cli, "peek_lark_app_config", lambda user_id: pytest.fail("a guide needs no credentials"))

    assert await _call(["skills", "read", "lark-doc"]) == "# lark-doc"
    assert runner.calls == [("guide", ("skills", "read", "lark-doc"))]


@pytest.mark.asyncio
async def test_a_user_without_a_connection_is_sent_to_the_capability_center(monkeypatch, connected):
    runner = Runner().install(monkeypatch)

    answer = json.loads(await _call(["docs", "+fetch", "--doc", "AbC"], runtime=_runtime("bob")))

    assert answer["status"] == "not_connected" and lark_tool.CONNECT_LINK in answer["notice"]
    assert all(call[0] != "user" for call in runner.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", ["config", "auth"])
async def test_an_authorization_error_from_lark_cli_points_to_the_capability_center(monkeypatch, connected, error_type):
    error = {"ok": False, "error": {"type": error_type, "message": "token expired", "hint": "run `lark-cli auth login`"}}
    Runner(result=Completed(3, json.dumps(error), "")).install(monkeypatch)

    answer = json.loads(await _call(["docs", "+fetch", "--doc", "AbC"]))

    assert answer["status"] == "not_connected" and lark_tool.CONNECT_LINK in answer["notice"]
    assert "auth login" not in json.dumps(answer)


@pytest.mark.asyncio
async def test_other_failures_keep_lark_clis_message_but_not_its_terminal_hint(monkeypatch, connected):
    error = {
        "ok": False,
        "error": {
            "type": "permission",
            "subtype": "missing_scope",
            "message": "需要 docx:document:readonly",
            "hint": "run `lark-cli auth login --scope docx:document:readonly`",
        },
    }
    Runner(result=Completed(1, "", json.dumps(error))).install(monkeypatch)

    answer = json.loads(await _call(["docs", "+fetch", "--doc", "AbC"]))

    assert answer["status"] == "failed" and answer["exit_code"] == 1
    assert answer["error"] == {"type": "permission", "subtype": "missing_scope", "message": "需要 docx:document:readonly"}
    assert lark_tool.CONNECT_LINK in answer["notice"]
    assert "lark-cli auth login" not in json.dumps(answer, ensure_ascii=False)


@pytest.mark.asyncio
async def test_a_plain_text_failure_is_reported(monkeypatch, connected):
    Runner(result=Completed(124, "", "lark-cli 运行超时（60 秒），已终止")).install(monkeypatch)

    answer = json.loads(await _call(["docs", "+fetch", "--doc", "AbC"]))

    assert answer["status"] == "failed" and answer["error"] == {"message": "lark-cli 运行超时（60 秒），已终止"}


@pytest.mark.asyncio
async def test_truncated_output_says_so(monkeypatch, connected):
    Runner(result=Completed(0, "x" * 10, "", truncated=True)).install(monkeypatch)

    answer = await _call(["docs", "+fetch", "--doc", "AbC"])

    assert answer.startswith("x" * 10) and "截断" in answer


@pytest.mark.asyncio
async def test_an_unavailable_deployment_says_so(monkeypatch, connected):
    def unavailable(_path, *, timeout):
        raise lark_runner.LarkUnavailable("网关不是 root")

    monkeypatch.setattr(lark_runner, "command_risk", unavailable)

    answer = json.loads(await _call(["docs", "+fetch", "--doc", "AbC"]))

    assert answer["status"] == "unavailable" and "网关不是 root" in answer["notice"]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["", "default"])
async def test_a_run_without_an_authenticated_owner_is_refused(monkeypatch, connected, user_id):
    runner = Runner().install(monkeypatch)
    monkeypatch.setattr(lark_tool, "resolve_runtime_user_id", lambda runtime: user_id)

    answer = json.loads(await _call(["skills", "list"]))

    assert answer["status"] == "rejected"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_only_a_controlled_lead_run_may_call_it(monkeypatch, connected):
    runner = Runner().install(monkeypatch)

    with pytest.raises(ValueError, match="受控"):
        await _call(["skills", "list"], runtime=_runtime(kind="subagent"))
    assert runner.calls == []


def test_the_schema_offers_only_the_argument_list():
    schema = lark_tool.lark_cli_tool.tool_call_schema.model_json_schema()

    assert set(schema["properties"]) == {"argv"}
    assert schema["properties"]["argv"]["type"] == "array"


def test_connected_reads_the_capability_center_setup(monkeypatch, connected):
    assert lark_tool.lark_connected("alice")
    assert not lark_tool.lark_connected("bob")


def test_connected_is_false_when_the_setup_cannot_be_read(monkeypatch):
    def broken(_user_id):
        raise ValueError("symlink")

    monkeypatch.setattr(lark_cli, "peek_lark_app_config", broken)

    assert not lark_tool.lark_connected("alice")


@pytest.mark.asyncio
async def test_each_step_gets_at_most_what_is_left_of_the_turn(monkeypatch, connected):
    runner = Runner().install(monkeypatch)
    runtime = _runtime()
    task = runtime.context[EXTENSION_TASK_STORE_KEY].get(PickTask)
    task.deadline = time.monotonic() + 5

    await _call(["docs", "+fetch", "--doc", "AbC"], runtime=runtime)
    await _call(["skills", "list"], runtime=runtime)

    assert len(runner.timeouts) == 3 and all(0 < timeout <= 5 for timeout in runner.timeouts)


@pytest.mark.asyncio
async def test_without_a_tight_turn_the_runner_limits_apply(monkeypatch, connected):
    runner = Runner().install(monkeypatch)

    await _call(["docs", "+fetch", "--doc", "AbC"])

    risk_timeout, run_timeout = runner.timeouts
    assert risk_timeout == pytest.approx(lark_runner.HELP_TIMEOUT_SECONDS, abs=1)
    assert run_timeout == pytest.approx(lark_runner.TIMEOUT_SECONDS, abs=1)
