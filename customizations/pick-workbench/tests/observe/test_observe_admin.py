"""TR-01: the operator command framework (plan D35).

`python -m ggwork_pick.observe.admin <name> [args...]` finds the `cmd_*.py` modules of its own package; each task adds
one module exporting NAME and main(argv), and nobody edits a shared dispatch table. The tests build throwaway command
packages under tmp_path, so the real package's (future) commands are never run here.
"""

import importlib
import json
import pkgutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2]
EXTENSION_API = Path(__file__).resolve().parents[4] / "backend/packages/extension-api"
SECRET = "postgresql://observer:hunter2@db.example/pick"


@pytest.fixture
def make_package(tmp_path, monkeypatch):
    """make_package({"cmd_alpha": "source", ...}) -> an importable package name holding those modules."""
    monkeypatch.syspath_prepend(str(tmp_path))
    made = []

    def make(modules: dict[str, str]) -> str:
        name = f"fake_admin_{uuid.uuid4().hex[:8]}"
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        for module, source in modules.items():
            (package / f"{module}.py").write_text(textwrap.dedent(source))
        importlib.invalidate_caches()
        made.append(name)
        return name

    yield make
    for name in made:
        for loaded in [key for key in sys.modules if key == name or key.startswith(name + ".")]:
            del sys.modules[loaded]


RECORDING = """
    NAME = "{name}"
    CALLS = []

    def main(argv):
        CALLS.append(list(argv))
        return {code}
"""


def test_admin_discovers_commands(make_package, capsys):
    from ggwork_pick.observe.admin.__main__ import discover, main

    package = make_package(
        {
            "cmd_alpha": RECORDING.format(name="alpha", code=0),
            "cmd_import_legacy": RECORDING.format(name="import-legacy", code=7),
            "helpers": "NAME = 'helpers'\ndef main(argv):\n    raise AssertionError('not a command')\n",
        }
    )
    assert list(discover(package)) == ["alpha", "import-legacy"]
    assert main(["alpha"], package=package) == 0
    assert main(["import-legacy", "--file", "x.jsonl"], package=package) == 7
    assert sys.modules[f"{package}.cmd_alpha"].CALLS == [[]]
    assert sys.modules[f"{package}.cmd_import_legacy"].CALLS == [["--file", "x.jsonl"]]
    assert f"{package}.helpers" not in sys.modules


def test_admin_unknown_command_exit_2(make_package, capsys):
    from ggwork_pick.observe.admin.__main__ import main
    from ggwork_pick.observe.errors import ExitCode

    package = make_package({"cmd_alpha": RECORDING.format(name="alpha", code=0)})
    assert main(["bravo", SECRET], package=package) == ExitCode.REFUSED == 2
    assert main([], package=package) == 2
    err = capsys.readouterr().err
    assert "alpha" in err
    assert "bravo" not in err and "hunter2" not in err  # what was typed is never echoed
    assert f"{package}.cmd_alpha" not in sys.modules  # listed by file name, never imported to refuse


def test_admin_only_loads_the_command_it_runs(make_package, capsys):
    """A broken or heavy command module must not take the others down, nor slow them."""
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package({"cmd_alpha": RECORDING.format(name="alpha", code=0), "cmd_broken": f"raise ImportError('cannot reach {SECRET}')\n"})
    assert main(["alpha"], package=package) == 0
    assert f"{package}.cmd_broken" not in sys.modules
    assert main(["broken"], package=package) == 1
    err = capsys.readouterr().err
    assert "ImportError" in err and "hunter2" not in err


def test_admin_help_lists_commands(make_package, capsys):
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package({"cmd_alpha": RECORDING.format(name="alpha", code=0), "cmd_reset_disable": RECORDING.format(name="reset-disable", code=0)})
    for flag in ("-h", "--help"):
        assert main([flag], package=package) == 0
        out = capsys.readouterr().out
        assert "alpha" in out and "reset-disable" in out


@pytest.mark.parametrize(
    "source",
    [
        RECORDING.format(name="other", code=0),  # NAME disagrees with the file name
        "NAME = 'alpha'\n",  # no main
        "def main(argv):\n    return 0\n",  # no NAME
        "NAME = 'alpha'\nmain = 3\n",  # main not callable
    ],
    ids=["name-mismatch", "no-main", "no-name", "main-not-callable"],
)
def test_admin_refuses_a_malformed_command(make_package, capsys, source):
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package({"cmd_alpha": source})
    assert main(["alpha"], package=package) == 2
    assert "cmd_alpha" in capsys.readouterr().err


def test_admin_failure_names_class_and_sqlstate_only(make_package, capsys):
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package(
        {
            "cmd_alpha": f"""
                NAME = "alpha"

                class InsufficientPrivilegeError(Exception):
                    sqlstate = "42501"

                def main(argv):
                    raise InsufficientPrivilegeError("permission denied for {SECRET}")
            """
        }
    )
    assert main(["alpha"], package=package) == 1
    err = capsys.readouterr().err
    assert "InsufficientPrivilegeError" in err and "42501" in err
    assert "hunter2" not in err and "permission denied" not in err


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("raise KeyboardInterrupt", 130),
        ("from ggwork_pick.observe.errors import StateUnavailable\n    raise StateUnavailable('运行时行不存在')", 3),
        ("from ggwork_pick.observe.errors import Refused\n    raise Refused('缺少环境变量 PICK_OBS_DSN_FILE')", 2),
        ("return None", 0),
        ("return 5", 5),
        ("raise SystemExit(0)", 0),
        ("raise SystemExit(2)", 2),
        ("raise SystemExit('usage text')", 1),
        ("return 255", 255),
        ("return 256", 1),  # the OS keeps the low byte: 256 would exit 0 and read as success
        ("return -1", 1),
        ("raise SystemExit(512)", 1),
        ("from ggwork_pick.observe.errors import StateUnavailable\n    raise ExceptionGroup('x', [RuntimeError('a'), StateUnavailable('b')])", 3),
        ("raise BaseExceptionGroup('x', [KeyboardInterrupt()])", 130),
    ],
    ids=[
        "interrupted",
        "state-unavailable",
        "refused",
        "none-is-ok",
        "own-status",
        "argparse-help",
        "argparse-usage",
        "exit-with-text",
        "top-status",
        "status-over-255",
        "negative-status",
        "exit-over-255",
        "group-with-state-unavailable",
        "group-with-interrupt",
    ],
)
def test_admin_exit_status_follows_the_outcome(make_package, capsys, body, expected):
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package({"cmd_alpha": f"NAME = 'alpha'\n\ndef main(argv):\n    {body}\n"})
    assert main(["alpha"], package=package) == expected


def test_admin_own_failure_keeps_the_wrapped_sqlstate(make_package, capsys):
    """Exit 3 with 42501 means regrant; exit 3 without it means the database was not reached (D34)."""
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package(
        {
            "cmd_alpha": f"""
                from ggwork_pick.observe.errors import StateUnavailable

                NAME = "alpha"

                class InsufficientPrivilegeError(Exception):
                    sqlstate = "42501"

                def main(argv):
                    try:
                        raise InsufficientPrivilegeError("permission denied for {SECRET}")
                    except InsufficientPrivilegeError as db_error:
                        raise StateUnavailable("运行时行读不到") from db_error
            """
        }
    )
    assert main(["alpha"], package=package) == 3
    err = capsys.readouterr().err
    assert "StateUnavailable：运行时行读不到（SQLSTATE 42501）" in err
    assert "hunter2" not in err and "permission denied" not in err


PARSED = """
    from ggwork_pick.observe.admin.args import command_parser

    NAME = "alpha"

    def main(argv):
        parser = command_parser(NAME, "测试命令")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--mode", choices=("stable", "canary1"))
        parser.add_argument("--file", required=True)
        parser.parse_args(argv)
        return 0
"""


@pytest.mark.parametrize(
    "argv",
    [
        ["--file", "x", "--limit", SECRET],  # a conversion error quotes the value
        ["--file", "x", "--mode", SECRET],  # so does an invalid choice
        ["--file", "x", "--dsn", SECRET],  # and an unrecognized argument
        ["--file", "x", SECRET],  # and a stray positional
        ["--limit", "3"],  # a missing required argument
    ],
    ids=["bad-type", "bad-choice", "unknown-option", "stray-positional", "missing-required"],
)
def test_command_parser_never_echoes_what_was_typed(make_package, capsys, argv):
    """argparse's own error() repeats the offending value; the admin parser prints the usage and exits 2."""
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package({"cmd_alpha": PARSED})
    assert main(["alpha", *argv], package=package) == 2
    err = capsys.readouterr().err
    assert "hunter2" not in err and "postgresql" not in err
    assert "python -m ggwork_pick.observe.admin alpha" in err and "--limit" in err


def test_command_parser_help_and_success(make_package, capsys):
    from ggwork_pick.observe.admin.__main__ import main

    package = make_package({"cmd_alpha": PARSED})
    assert main(["alpha", "--file", "x", "--limit", "3"], package=package) == 0
    assert main(["alpha", "--help"], package=package) == 0
    out = capsys.readouterr().out
    assert "python -m ggwork_pick.observe.admin alpha" in out and "测试命令" in out and "--mode" in out


def test_real_admin_package_commands_follow_the_convention():
    """Every command a later task adds to the real package declares the NAME its file name implies."""
    from ggwork_pick.observe import admin
    from ggwork_pick.observe.admin.__main__ import command_name, discover

    names = [info.name for info in pkgutil.iter_modules(admin.__path__) if info.name.startswith("cmd_")]
    assert list(discover(admin.__name__)) == sorted(command_name(name) for name in names)
    for name in names:
        module = importlib.import_module(f"{admin.__name__}.{name}")
        assert module.NAME == command_name(name) and callable(module.main)


def test_admin_runs_as_a_module(tmp_path):
    code = (
        "import runpy, sys\n"
        f"sys.path[:0] = {json.dumps([str(SOURCE), str(EXTENSION_API)])}\n"
        "sys.argv = ['admin', '--help']\n"
        "runpy.run_module('ggwork_pick.observe.admin', run_name='__main__', alter_sys=True)\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "python -m ggwork_pick.observe.admin" in done.stdout
