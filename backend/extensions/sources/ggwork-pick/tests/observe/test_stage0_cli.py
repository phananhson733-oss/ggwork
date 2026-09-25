"""TR-05: stage 0's command line and its redacted fixtures (plan TR-05).

Every command runs against a temporary artifacts root. `run` is exercised with --dry-run, with refusals, and once on a
ManualClock against the fake Google of test_stage0_run: nothing leaves the process.
"""

import io
import json
import os
import stat
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from stage0_fakes import controls_document, hourly_values, related_ok, result_line
from test_stage0_run import START, FakeGoogle
from trends_fakes import body_of

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.trends import stage0_cli
from ggwork_pick.observe.trends.parse import classify, redirect_kind
from ggwork_pick.observe.trends.source import FetchStatus, Phase, RedirectKind
from ggwork_pick.observe.trends.stage0 import MAX_HTTP_PER_DAY, DayPlan, parse_controls
from ggwork_pick.observe.trends.stage0_fixtures import DOCUMENTATION_IP, REDACTED_TOKEN, write_fixtures
from ggwork_pick.observe.trends.stage0_run import Stage0Paths, append_private, load_meta, write_private, write_private_bytes


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def paths(tmp_path) -> Stage0Paths:
    root = tmp_path / "trends-stage0"
    root.mkdir(mode=0o700)
    found = Stage0Paths(root)
    write_private(found.controls_file, json.dumps(controls_document()))
    return found


def refusing_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent here")

    return httpx.MockTransport(handler)


def cli(paths: Stage0Paths, *argv: str, **options) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    options = {"environ": {}, "transport": refusing_transport(), "system_proxies": lambda: {}, **options}
    code = stage0_cli.main([*argv, "--root", str(paths.root)], out=out, err=err, **options)
    return code, out.getvalue(), err.getvalue()


def test_plan_writes_both_days(paths):
    code, out, _ = cli(paths, "plan")
    assert code == 0
    for day in (1, 2):
        path = paths.plan_file(day)
        assert mode(path) == 0o600 and mode(path.parent) == 0o700
        plan = DayPlan.from_document(json.loads(path.read_text()))
        assert plan.http <= MAX_HTTP_PER_DAY
    assert "172" in out  # 88 + 84 for the synthetic list


def test_plan_refuses_to_overwrite_a_started_day(paths):
    assert cli(paths, "plan")[0] == 0
    append_private(paths.run_dir(1) / "results.jsonl", {"unit": "pos-01-h", "status": "ok"})
    before = paths.plan_file(1).read_text()
    code, _, err = cli(paths, "plan")
    assert code == 2 and "第 1 天" in err
    assert paths.plan_file(1).read_text() == before


def test_run_refuses_without_a_plan(paths):
    code, _, err = cli(paths, "run", "--day", "1", "--dry-run")
    assert code == 2 and "plan" in err


def test_run_dry_run_sends_nothing(paths):
    cli(paths, "plan")
    code, out, _ = cli(paths, "run", "--day", "1", "--dry-run")
    assert code == 0 and "待跑" in out and "pos-01-h" in out
    assert not paths.state_file.exists()


def test_run_refuses_a_changed_control_list(paths):
    cli(paths, "plan")
    changed = controls_document()
    changed["controls"][0]["term"] = "a different title"
    write_private(paths.controls_file, json.dumps(changed))
    code, _, err = cli(paths, "run", "--day", "1", "--dry-run")
    assert code == 2 and "plan" in err


def test_run_refuses_without_a_key(paths):
    cli(paths, "plan")
    code, _, err = cli(paths, "run", "--day", "1", "--init-state")
    assert code == 2
    assert not paths.state_file.exists()


def test_run_day_one_end_to_end(paths):
    """The whole synthetic day 1 through the command, on a ManualClock against the fake Google: every unit covered,
    no more requests than the task list, the state file private."""
    cli(paths, "plan")
    fake, clock = FakeGoogle(), ManualClock(START)
    fake.clock = clock
    environ = {"PICK_OBS_STATE_KEY": Fernet.generate_key().decode(), "HTTPS_PROXY": "http://user:hunter2@proxy.invalid:3128"}
    code, out, _ = cli(paths, "run", "--day", "1", "--init-state", environ=environ, transport=fake.transport(), clock=clock)
    assert code == 0, out
    plan = DayPlan.from_document(json.loads(paths.plan_file(1).read_text()))
    assert len(fake.requests) == plan.http
    assert mode(paths.state_file) == 0o600
    session = load_meta(paths.run_dir(1))["sessions"][0]
    assert session["uncovered"] == [] and session["proxy"]["environment"] == ["HTTPS_PROXY"]
    assert "hunter2" not in out and "hunter2" not in paths.run_dir(1).joinpath("meta.json").read_text()


def _most_in_window(times, seconds: float) -> int:
    return max(sum(1 for later in times if 0 <= (later - earlier).total_seconds() < seconds) for earlier in times)


@pytest.mark.parametrize(("pace", "user_rhythm"), [("user", True), ("design", False)])
def test_run_pace_user_keeps_the_users_rhythm(paths, pace, user_rhythm):
    """--pace user is the user's own tested rhythm (a burst of at most 4, about 2 a minute sustained); the design's
    default envelope runs about twice as fast, and on day 1 met a 429 after 14 minutes at that speed. A minute can hold
    the burst plus what the bucket refills meanwhile; ten minutes tell the two rhythms apart."""
    cli(paths, "plan")
    fake, clock = FakeGoogle(), ManualClock(START)
    fake.clock = clock
    environ = {"PICK_OBS_STATE_KEY": Fernet.generate_key().decode()}
    code, out, _ = cli(paths, "run", "--day", "1", "--init-state", "--pace", pace, environ=environ, transport=fake.transport(), clock=clock)
    assert code == 0, out
    assert _most_in_window(fake.times, 60) <= (4 + 2 if user_rhythm else 8 + 4)
    assert (_most_in_window(fake.times, 600) <= 2 * 10 + 4) is user_rhythm


def test_report_writes_an_interim_report(paths):
    entry = next(c for c in controls_document()["controls"] if c["id"] == "pos-01")
    append_private(paths.run_dir(1) / "results.jsonl", result_line(entry, "H", hourly_values(14)))
    manual = paths.root / "export-1.csv"
    manual.write_text("Category: All categories\n\nTime,moonlit vow 1: (United States)\n2026-09-25T08,5\n2026-09-25T09,<1\n")
    code, out, _ = cli(paths, "report", "--manual", str(manual))
    assert code == 0
    written = next(paths.root.glob("trends-stage0-interim-report-*.md"))
    assert written.name in out and mode(written) == 0o600
    text = written.read_text()
    assert "中期报告" in text and "export-1.csv" in text


def test_usage_errors_never_echo(paths, capsys):
    code = stage0_cli.main(["run", "--day", "secret-value-7", "--root", str(paths.root)], environ={}, transport=refusing_transport())
    assert code == 2
    assert "secret-value-7" not in capsys.readouterr().err


# ---- the redacted fixtures -----------------------------------------------------------------------------------------


def test_fixtures_take_out_terms_tokens_and_addresses(paths):
    controls = parse_controls(controls_document())
    entry = next(c for c in controls_document()["controls"] if c["id"] == "pos-01")
    append_private(paths.run_dir(1) / "results.jsonl", {**result_line(entry, "D", None, status="ok"), "unit": "pos-01-d", "related": related_ok()})
    explore = {"widgets": [{"id": "RELATED_QUERIES", "token": "APP6_real-token", "request": {"keyword": "Moonlit Vow 1"}}]}
    related = {
        "default": {
            "rankedList": [
                {
                    "rankedKeyword": [
                        {"query": "moonlit vow 1 full", "value": 5000, "formattedValue": "Breakout", "link": "/trends/explore?q=moonlit+vow+1+full"}
                    ]
                }
            ]
        }
    }
    sorry = "<html>Our systems have detected unusual traffic. IP address: 198.51.100.23 q=moonlit+vow+1</html>"
    bodies = [("explore", 200, ")]}'\n" + json.dumps(explore)), ("related", 200, ")]}',\n" + json.dumps(related)), ("related", 429, sorry)]
    for seq, (phase, status, body) in enumerate(bodies, 1):
        name = f"{seq:04d}-{phase}.body"
        write_private_bytes(paths.raw_dir(1) / name, body.encode())
        append_private(paths.raw_dir(1) / "index.jsonl", {"seq": seq, "unit": "pos-01-d", "phase": phase, "http_status": status, "body_file": name})
    written = write_fixtures(paths, 1, controls)
    assert len(written) == 3
    texts = [path.read_text() for path in written]
    for text in texts:
        assert "moonlit vow 1" not in text.lower() and "moonlit+vow+1" not in text.lower()
        assert "APP6_real-token" not in text and "198.51.100.23" not in text
    first, second, third = (json.loads(text) for text in texts)
    assert first["constructed"] is False and first["pending_stage0"] == []
    assert first["response"]["json"]["widgets"][0]["token"] == REDACTED_TOKEN
    ranked = second["response"]["json"]["default"]["rankedList"][0]["rankedKeyword"][0]
    assert ranked["formattedValue"] == "Breakout" and ranked["query"].startswith("related query ")
    assert second["response"]["prefix"] == ")]}',\n"
    assert DOCUMENTATION_IP in third["response"]["text"]
    assert first["query"]["bare"] == first["query"]["terms"][0] and first["query"]["geo"] == "US"
    assert all(mode(path) == 0o600 for path in written)


def test_fixtures_rebuild_the_headers_a_replay_needs(paths):
    """A redirect keeps where it pointed, rebuilt from the recorded kind and host (never the raw Location, whose query
    can carry the term), and every answer a content type: replayed, each fixture earns the status the real answer did."""
    controls = parse_controls(controls_document())
    entry = next(c for c in controls_document()["controls"] if c["id"] == "pos-01")
    append_private(paths.run_dir(1) / "results.jsonl", result_line(entry, "H", None, status="blocked_redirect"))
    answers = [
        (302, b"", {"redirect_kind": "sorry", "redirect_host": "www.google.com"}),
        (302, b"", {"redirect_kind": "consent", "redirect_host": "consent.google.com"}),
        (200, b"<!DOCTYPE html><html>unusual traffic</html>", {}),
        (200, b')]}\'\n{"widgets": []}', {}),
    ]
    for seq, (status, body, redirect) in enumerate(answers, 1):
        name = f"{seq:04d}-explore.body"
        write_private_bytes(paths.raw_dir(1) / name, body)
        index = {"seq": seq, "unit": "pos-01-h", "phase": "explore", "http_status": status, "body_file": name, "redirect_kind": None, "redirect_host": None}
        append_private(paths.raw_dir(1) / "index.jsonl", {**index, **redirect})
    fixtures = [json.loads(path.read_text()) for path in write_fixtures(paths, 1, controls)]
    replayed = []
    for fixture in fixtures:
        response = fixture["response"]
        headers = httpx.Headers(response["headers"])
        replayed.append((classify(Phase.EXPLORE, response["status"], headers, body_of(response)), headers))
    assert [status for status, _ in replayed[:3]] == [FetchStatus.BLOCKED_REDIRECT, FetchStatus.BLOCKED_REDIRECT, FetchStatus.HTML_BODY]
    assert redirect_kind(replayed[0][1]["location"]) == (RedirectKind.SORRY, "www.google.com")
    assert redirect_kind(replayed[1][1]["location"])[0] is RedirectKind.CONSENT
    assert all("?" not in headers.get("location", "") for _, headers in replayed)
    assert "text/html" in replayed[2][1]["content-type"] and "json" in replayed[3][1]["content-type"]
