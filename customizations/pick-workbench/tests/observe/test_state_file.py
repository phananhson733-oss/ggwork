"""TR-04: the persisted Trends state, the file store and the state key (plan TR-04; design 4.3, 4.4).

Design 4.3: when the state cannot be read, the day does not run, or a restart would come back with a new cookie jar at
full speed. So a missing, unreadable, undecryptable or loosely permitted state file is StateUnavailable (exit 3), and
only an explicit --init-state creates one, never over an existing file. Keys come only from the environment and never
reach a message, a repr or a log line; nor do cookie values.
"""

import dataclasses
import json
import logging
import os
import signal
import stat
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
EXTENSION_API = Path(__file__).resolve().parents[4] / "backend/packages/extension-api"
START = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)
TARGET = date(2026, 9, 26)  # the 02:00 UTC deadline this session works towards (D23)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
OTHER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"
COOKIE_VALUE = "511=hunter2-cookie-value_Zx9"


def new_key() -> str:
    return Fernet.generate_key().decode()


def state_path(tmp_path: Path) -> Path:
    return tmp_path / "obs" / "trends-state.json"


def file_store(path: Path, *keys: str):
    from ggwork_pick.observe.crypto import StateCipher
    from ggwork_pick.observe.state import FileStateStore

    return FileStateStore(path, StateCipher(list(keys)))


def warm_jar():
    from ggwork_pick.observe.trends.cookies import Cookie, CookieJar

    return CookieJar.fresh(UA).warmed([Cookie("NID", COOKIE_VALUE, domain=".google.com")], day=TARGET, now=START)


def write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def lock_name(path: Path) -> str:
    return f".{path.name}.lock"


def names_in(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def key_env(key: str) -> dict:
    env = {**os.environ, "PICK_OBS_STATE_KEY": key}
    env.pop("PICK_OBS_STATE_KEY_FILE", None)
    return env


def run_isolated(code: str, env: dict | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    """`code` in a fresh interpreter that sees this worktree's source first, like the cron would."""
    prelude = f"import sys\nsys.path[:0] = {json.dumps([str(SOURCE), str(EXTENSION_API)])}\n"
    return subprocess.run([sys.executable, "-I", "-c", prelude + code], env=env, capture_output=True, text=True, timeout=timeout)


@pytest.mark.asyncio
async def test_missing_file_requires_init(tmp_path):
    from ggwork_pick.observe.errors import ExitCode, Refused, StateUnavailable, exit_code_for
    from ggwork_pick.observe.state import RuntimeState, open_state

    key, path = new_key(), state_path(tmp_path)
    with pytest.raises(StateUnavailable) as missing:
        await open_state(file_store(path, key), init_state=False)
    assert exit_code_for(missing.value) == ExitCode.STATE_UNAVAILABLE == 3
    assert "--init-state" in str(missing.value)
    assert not path.parent.exists()  # a refused read creates nothing

    assert await open_state(file_store(path, key), init_state=True) == RuntimeState()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert await open_state(file_store(path, key), init_state=False) == RuntimeState()

    before = path.read_bytes()
    with pytest.raises(Refused) as again:  # --init-state never starts afresh over a state that exists
        await open_state(file_store(path, key), init_state=True)
    assert exit_code_for(again.value) == ExitCode.REFUSED
    assert path.read_bytes() == before


def _sealed(key: str, document: object) -> bytes:
    return Fernet(key).encrypt(json.dumps(document).encode())


def _valid_document() -> dict:
    from ggwork_pick.observe.state import RuntimeState

    return RuntimeState(paused_until=START).to_document()


DAMAGES = {
    "wrong-key": lambda key, sealed: Fernet(new_key()).encrypt(b"{}"),
    "flipped-byte": lambda key, sealed: sealed[:40] + bytes([sealed[40] ^ 0x01]) + sealed[41:],
    "truncated": lambda key, sealed: sealed[: len(sealed) // 2],
    "empty": lambda key, sealed: b"",
    "not-json": lambda key, sealed: Fernet(key).encrypt(b"not json at all"),
    "not-an-object": lambda key, sealed: _sealed(key, ["format", 1]),
    "future-format": lambda key, sealed: _sealed(key, {**_valid_document(), "format": 99}),
    "unknown-field": lambda key, sealed: _sealed(key, {**_valid_document(), "extra": 1}),
    "missing-field": lambda key, sealed: _sealed(key, {k: v for k, v in _valid_document().items() if k != "breaker"}),
    "naive-pause": lambda key, sealed: _sealed(key, {**_valid_document(), "paused_until": "2026-09-25T22:30:00"}),
    "bad-jar": lambda key, sealed: _sealed(key, {**_valid_document(), "cookie_jar": {"user_agent": UA}}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", sorted(DAMAGES))
async def test_corrupt_or_wrong_key_unavailable(tmp_path, damage):
    from ggwork_pick.observe.errors import ExitCode, StateUnavailable, describe_error, exit_code_for
    from ggwork_pick.observe.state import RuntimeState

    key, path = new_key(), state_path(tmp_path)
    await file_store(path, key).initialize(RuntimeState(paused_until=START))
    write_private(path, DAMAGES[damage](key, path.read_bytes()))
    with pytest.raises(StateUnavailable) as caught:
        await file_store(path, key).load()
    assert exit_code_for(caught.value) == ExitCode.STATE_UNAVAILABLE
    assert key not in describe_error(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o610, 0o700, 0o666], ids=oct)
async def test_mode_600_enforced(tmp_path, mode):
    from ggwork_pick.observe.errors import StateUnavailable
    from ggwork_pick.observe.state import RuntimeState

    key, path = new_key(), state_path(tmp_path)
    await file_store(path, key).initialize(RuntimeState(paused_until=START))
    path.chmod(mode)
    with pytest.raises(StateUnavailable) as caught:
        await file_store(path, key).load()
    assert "600" in str(caught.value)
    path.chmod(0o400)  # narrower than 600 is fine to read
    assert (await file_store(path, key).load()).paused_until == START


@pytest.mark.asyncio
async def test_state_directory_and_links_must_be_private(tmp_path):
    from ggwork_pick.observe.errors import StateUnavailable
    from ggwork_pick.observe.state import RuntimeState

    key, path = new_key(), state_path(tmp_path)
    store = file_store(path, key)
    await store.initialize(RuntimeState())
    path.parent.chmod(0o755)  # others could swap the file under us
    with pytest.raises(StateUnavailable, match="700"):
        await file_store(path, key).load()
    with pytest.raises(StateUnavailable, match="700"):
        await store.save(RuntimeState())
    path.parent.chmod(0o700)

    link = path.parent / "linked-state.json"
    link.symlink_to(path)
    with pytest.raises(StateUnavailable, match="符号链接"):
        await file_store(link, key).load()

    wide = tmp_path / "wide"
    wide.mkdir(mode=0o755)
    wide.chmod(0o755)
    with pytest.raises(StateUnavailable, match="700"):  # init never adopts a directory others can reach
        await file_store(wide / "trends-state.json", key).initialize(RuntimeState())
    assert list(wide.iterdir()) == []


@pytest.mark.asyncio
async def test_atomic_write(tmp_path, monkeypatch):
    """A save replaces the file by renaming a finished temp file over it; interrupted half-way, the old file is still
    whole and still reads."""
    from ggwork_pick.observe import state as state_module
    from ggwork_pick.observe.errors import StateUnavailable
    from ggwork_pick.observe.state import RuntimeState

    key, path = new_key(), state_path(tmp_path)
    store = file_store(path, key)
    await store.initialize(RuntimeState(paused_until=START))
    created = path.stat().st_ino
    await store.save(RuntimeState(paused_until=START + timedelta(hours=1)))
    assert path.stat().st_ino != created  # a new file renamed into place, never the old one rewritten
    assert names_in(path.parent) == sorted([path.name, lock_name(path)])  # no temp file left beside it
    before = path.read_bytes()

    def half_then_fail(fd: int, data: bytes) -> None:
        os.write(fd, data[: len(data) // 2])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(state_module, "_write_all", half_then_fail)
    with pytest.raises(StateUnavailable):
        await store.save(RuntimeState(paused_until=START + timedelta(hours=4)))
    assert path.read_bytes() == before
    assert names_in(path.parent) == sorted([path.name, lock_name(path)])  # its own half-written temp file is gone
    store.close()  # the run stops on the failed save
    assert (await file_store(path, key).load()).paused_until == START + timedelta(hours=1)


@pytest.mark.asyncio
async def test_atomic_write_survives_a_kill_mid_write(tmp_path):
    """A process killed half-way through writing (no exception handler runs) leaves the old file whole; the next save
    clears the half-written temp file it left."""
    from ggwork_pick.observe.state import RuntimeState

    key, path = new_key(), state_path(tmp_path)
    await file_store(path, key).initialize(RuntimeState(paused_until=START))
    before = path.read_bytes()
    code = (
        "import asyncio, os, signal, sys\n"
        f"sys.path[:0] = {json.dumps([str(SOURCE), str(EXTENSION_API)])}\n"
        "from ggwork_pick.observe import state\n"
        "from ggwork_pick.observe.crypto import load_cipher\n"
        "def half_then_die(fd, data):\n"
        "    os.write(fd, data[: len(data) // 2])\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
        "state._write_all = half_then_die\n"
        "async def main():\n"
        f"    store = state.FileStateStore({str(path)!r}, load_cipher())\n"
        "    await store.load()\n"
        "    await store.save(state.RuntimeState())\n"
        "asyncio.run(main())\n"
    )
    env = {**os.environ, "PICK_OBS_STATE_KEY": key}
    env.pop("PICK_OBS_STATE_KEY_FILE", None)
    done = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == -signal.SIGKILL, done.stderr[-2000:]
    assert path.read_bytes() == before
    leftovers = [name for name in names_in(path.parent) if name not in (path.name, lock_name(path))]
    assert len(leftovers) == 1 and leftovers[0].endswith(".tmp")

    store = file_store(path, key)  # the kill released the lock with the process
    assert (await store.load()).paused_until == START
    await store.save(RuntimeState())
    assert names_in(path.parent) == sorted([path.name, lock_name(path)])


@pytest.mark.asyncio
async def test_restart_keeps_pause(tmp_path):
    from ggwork_pick.observe.state import open_state

    key, path = new_key(), state_path(tmp_path)
    first = file_store(path, key)
    state = await open_state(first, init_state=True)
    assert not state.is_paused(START)
    pause_until = START + timedelta(minutes=30)
    await first.save(dataclasses.replace(state, paused_until=pause_until, breaker={"level": 1, "trips_today": 1}))
    first.close()  # the process ends

    restarted = await open_state(file_store(path, key), init_state=False)  # a new process, same file
    assert restarted.paused_until == pause_until
    assert restarted.is_paused(START + timedelta(minutes=5))
    assert restarted.is_paused(START + timedelta(minutes=29, seconds=59))
    assert not restarted.is_paused(pause_until)
    assert restarted.breaker == {"level": 1, "trips_today": 1}
    with pytest.raises(ValueError):
        restarted.is_paused(START.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_key_rotation(tmp_path, caplog):
    """The old key still reads; the new key writes. Rotation: prepend the new key, save once, drop the old key."""
    from ggwork_pick.observe.crypto import KEY_VARIABLE, load_cipher
    from ggwork_pick.observe.errors import StateUnavailable
    from ggwork_pick.observe.state import FileStateStore, RuntimeState

    old, new, path = new_key(), new_key(), state_path(tmp_path)
    await file_store(path, old).initialize(RuntimeState(paused_until=START))

    caplog.set_level(logging.DEBUG)
    rotating = FileStateStore(path, load_cipher({KEY_VARIABLE: f"{new},{old}"}))
    state = await rotating.load()
    assert state.paused_until == START
    assert "key #2 of 2" in caplog.text  # the operator learns the file is still under the old key
    await rotating.save(state)
    rotating.close()

    assert Fernet(new).decrypt(path.read_bytes())  # written under the new key
    assert (await file_store(path, new).load()).paused_until == START
    with pytest.raises(StateUnavailable):
        await file_store(path, old).load()


def test_state_key_comes_only_from_the_environment(tmp_path):
    from ggwork_pick.observe.crypto import KEY_FILE_VARIABLE, KEY_VARIABLE, load_cipher
    from ggwork_pick.observe.errors import ExitCode, Refused, exit_code_for

    first, second = new_key(), new_key()
    assert load_cipher({KEY_VARIABLE: f" {first} , {second} "}).key_count == 2
    key_file = tmp_path / "state.key"
    write_private(key_file, f"{first}\n{second}\n".encode())
    assert load_cipher({KEY_FILE_VARIABLE: str(key_file)}).key_count == 2

    refusals = {
        "missing": {},
        "blank": {KEY_VARIABLE: "  "},
        "both": {KEY_VARIABLE: first, KEY_FILE_VARIABLE: str(key_file)},
        "not-a-key": {KEY_VARIABLE: f"{first},not-a-fernet-key-hunter2"},
        "no-file": {KEY_FILE_VARIABLE: str(tmp_path / "absent.key")},
    }
    for label, environ in refusals.items():
        with pytest.raises(Refused) as caught:
            load_cipher(environ)
        assert exit_code_for(caught.value) == ExitCode.REFUSED, label
        assert "hunter2" not in str(caught.value) and first not in str(caught.value), label
    with pytest.raises(Refused, match="第 2 个"):
        load_cipher({KEY_VARIABLE: f"{first},not-a-fernet-key-hunter2"})

    key_file.chmod(0o644)
    with pytest.raises(Refused, match="600"):
        load_cipher({KEY_FILE_VARIABLE: str(key_file)})
    key_file.chmod(0o600)
    link = tmp_path / "linked.key"
    link.symlink_to(key_file)
    with pytest.raises(Refused, match="符号链接"):
        load_cipher({KEY_FILE_VARIABLE: str(link)})


@pytest.mark.asyncio
async def test_missing_key_refuses_before_touching_the_state(tmp_path):
    """Without a key nothing runs: the refusal comes before the state is read or created (and so before any HTTP)."""
    from ggwork_pick.observe.errors import ExitCode, Refused, exit_code_for
    from ggwork_pick.observe.state import file_state_store

    path = state_path(tmp_path)
    with pytest.raises(Refused) as caught:
        file_state_store(path, environ={})
    assert exit_code_for(caught.value) == ExitCode.REFUSED
    assert not path.parent.exists()
    store = file_state_store(path, environ={"PICK_OBS_STATE_KEY": new_key()})
    assert store.path == path


LOAD_IN_CHILD = """
import asyncio
from ggwork_pick.observe.crypto import load_cipher
from ggwork_pick.observe.errors import exit_code_for
from ggwork_pick.observe.state import FileStateStore

async def main(path):
    try:
        await FileStateStore(path, load_cipher()).load()
    except Exception as exc:
        return str(int(exit_code_for(exc)))
    return "loaded"

print(asyncio.run(main(sys.argv[1])))
"""


def load_in_child(path: Path, key: str) -> str:
    """What a second local run on the same file gets from load(): "loaded", or its exit code."""
    code = LOAD_IN_CHILD.replace("sys.argv[1]", repr(str(path)))
    done = run_isolated(code, env=key_env(key))
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.strip().splitlines()[-1]


@pytest.mark.asyncio
async def test_a_second_process_is_refused_at_load(tmp_path):
    """Two local runs on one file: the second stops when it loads, before anything could be sent, so no interleaving of
    their saves can overwrite the first one's pause (the first holds the file's lock from load to exit)."""
    from ggwork_pick.observe.errors import ExitCode, StateUnavailable, exit_code_for
    from ggwork_pick.observe.state import RuntimeState

    key, path = new_key(), state_path(tmp_path)
    await file_store(path, key).initialize(RuntimeState())
    first, second = file_store(path, key), file_store(path, key)
    mine = await first.load()
    with pytest.raises(StateUnavailable, match="另一个进程") as caught:
        await second.load()
    assert exit_code_for(caught.value) == ExitCode.STATE_UNAVAILABLE
    with pytest.raises(RuntimeError):  # refused at load, it cannot save either
        await second.save(RuntimeState())
    assert load_in_child(path, key) == str(int(ExitCode.STATE_UNAVAILABLE))  # nor can another process

    await first.save(dataclasses.replace(mine, paused_until=START))
    await first.save(dataclasses.replace(mine, paused_until=START + timedelta(minutes=30)))  # its own saves chain
    first.close()  # the first run ends
    assert load_in_child(path, key) == "loaded"
    assert (await second.load()).paused_until == START + timedelta(minutes=30)
    with pytest.raises(RuntimeError):  # a store that never read cannot tell whether it would overwrite anything
        await file_store(path, key).save(RuntimeState())


@pytest.mark.asyncio
async def test_a_file_changed_behind_the_lock_is_not_overwritten(tmp_path):
    """The lock binds only the runs that take it. A file replaced some other way (a copy, a build without the lock) is
    still caught by the second guard: save compares the file with what this run last read or wrote."""
    from ggwork_pick.observe.errors import StateUnavailable
    from ggwork_pick.observe.state import RuntimeState

    key, path = new_key(), state_path(tmp_path)
    store = file_store(path, key)
    await store.initialize(RuntimeState())
    outside = _sealed(key, RuntimeState(paused_until=START).to_document())
    write_private(path, outside)
    with pytest.raises(StateUnavailable, match="改过"):
        await store.save(RuntimeState())
    assert path.read_bytes() == outside


@pytest.mark.asyncio
async def test_a_state_too_big_to_read_back_is_never_written(tmp_path):
    """load() refuses a file over MAX_STATE_BYTES, so save() must never write one: every later run would stop on it."""
    from ggwork_pick.observe.errors import StateUnavailable
    from ggwork_pick.observe.state import MAX_STATE_BYTES, RuntimeState

    key, path = new_key(), state_path(tmp_path)
    store = file_store(path, key)
    await store.initialize(RuntimeState(paused_until=START))
    before = path.read_bytes()
    huge = RuntimeState(pacing={"blob": "x" * MAX_STATE_BYTES})
    with pytest.raises(StateUnavailable, match="上限"):
        await store.save(huge)
    assert path.read_bytes() == before
    assert names_in(path.parent) == sorted([path.name, lock_name(path)])
    await store.save(RuntimeState(paused_until=START + timedelta(hours=1)))  # the refusal changed nothing else
    store.close()
    assert (await file_store(path, key).load()).paused_until == START + timedelta(hours=1)

    elsewhere = tmp_path / "other" / "trends-state.json"
    with pytest.raises(StateUnavailable, match="上限"):
        await file_store(elsewhere, key).initialize(huge)
    assert not elsewhere.exists()


def test_a_section_that_does_not_decode_is_state_unavailable():
    """The sections are TR-03's; their decoders raise ValueError on bad content. Read through RuntimeState.section, any
    such failure is StateUnavailable (exit 3), never a crash with exit 1."""
    from ggwork_pick.observe.errors import ExitCode, StateUnavailable, exit_code_for
    from ggwork_pick.observe.state import RuntimeState

    breaker = {"day": {"target_date": "2026-09-26", "paused_until": None}, "extinguished_days": ["2026-09-24"]}
    state = RuntimeState(breaker=breaker)
    seen = []

    def decode(data):
        seen.append(data)
        return ("decoded", data["day"]["target_date"])

    assert state.section("breaker", decode) == ("decoded", "2026-09-26")
    assert type(seen[0]) is dict and type(seen[0]["extinguished_days"]) is list  # plain JSON, as its owner wrote it
    assert state.section("pacing", decode) is None and len(seen) == 1  # never saved: the machine starts fresh
    for failure in (ValueError("bad"), TypeError("bad"), KeyError("day"), AttributeError("get"), IndexError("0")):

        def broken(data, failure=failure):
            raise failure

        with pytest.raises(StateUnavailable, match="breaker") as caught:
            state.section("breaker", broken)
        assert exit_code_for(caught.value) == ExitCode.STATE_UNAVAILABLE
        assert "2026-09-26" not in str(caught.value)
    with pytest.raises(ValueError):
        state.section("cookie_jar", decode)


FIFO_IN_CHILD = """
import asyncio, json
from ggwork_pick.observe.crypto import StateCipher, load_cipher
from ggwork_pick.observe.errors import ObserveFailure
from ggwork_pick.observe.state import FileStateStore

KEY_FILE, STATE, KEY = sys.argv[1:4]
messages = []
try:
    load_cipher({"PICK_OBS_STATE_KEY_FILE": KEY_FILE})
except ObserveFailure as exc:
    messages.append(str(exc))
try:
    asyncio.run(FileStateStore(STATE, StateCipher([KEY])).load())
except ObserveFailure as exc:
    messages.append(str(exc))
print(json.dumps(messages, ensure_ascii=False))
"""


def test_a_fifo_is_refused_without_waiting(tmp_path):
    """A FIFO where the key file or the state file should be is refused at once as not a regular file. A blocking open
    would hang the run (and the cron) until something wrote to the FIFO."""
    key_file, directory = tmp_path / "state.key", tmp_path / "obs"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    os.mkfifo(key_file, 0o600)
    os.mkfifo(directory / "trends-state.json", 0o600)
    arguments = [str(key_file), str(directory / "trends-state.json"), new_key()]
    code = FIFO_IN_CHILD.replace("sys.argv[1:4]", repr(arguments))
    done = run_isolated(code, timeout=30)
    assert done.returncode == 0, done.stderr[-2000:]
    messages = json.loads(done.stdout.strip().splitlines()[-1])
    assert len(messages) == 2 and all("普通文件" in message for message in messages), messages


def test_runtime_state_is_immutable():
    from ggwork_pick.observe.state import RuntimeState

    breaker = {"level": 1, "events": [{"kind": "rate_limited", "at": "2026-09-25T22:10:00.000000+00:00"}]}
    state = RuntimeState(paused_until=START, breaker=breaker, budget={"2026-09-26": {"reserved": 12}})
    breaker["level"] = 9
    breaker["events"].append({"kind": "probe_failed"})
    assert state.breaker["level"] == 1 and len(state.breaker["events"]) == 1
    with pytest.raises(TypeError):
        state.breaker["level"] = 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.paused_until = None
    later = dataclasses.replace(state, paused_until=None)
    assert state.paused_until == START and later.paused_until is None and later.breaker == state.breaker
    assert RuntimeState.from_document(json.loads(json.dumps(state.to_document()))) == state

    unstorable = [
        {"x": float("nan")},
        {"x": "a" + chr(0) + "b"},  # PostgreSQL text refuses NUL; the file store refuses what the database would
        {"x": chr(0xD800)},
        {1: "x"},
        {"x": object()},
    ]
    for section in unstorable:
        with pytest.raises(ValueError):
            RuntimeState(breaker=section)
    with pytest.raises(ValueError):
        RuntimeState(paused_until=START.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_ua_bound_to_jar(tmp_path):
    from ggwork_pick.observe.crypto import StateCipher
    from ggwork_pick.observe.errors import StateUnavailable
    from ggwork_pick.observe.state import RuntimeState
    from ggwork_pick.observe.trends.cookies import Cookie, CookieJar, open_jar, seal_jar

    fresh = CookieJar.fresh(UA)
    assert fresh.cookies == () and dict(fresh.request_headers(START)) == {"User-Agent": UA}
    jar = warm_jar()
    assert jar.user_agent == UA
    assert dict(jar.request_headers(START)) == {"User-Agent": UA, "Cookie": f"NID={COOKIE_VALUE}"}
    assert jar.updated([Cookie("NID", "next", domain=".google.com")], now=START).user_agent == UA  # updates keep the UA
    assert CookieJar.fresh(OTHER_UA).cookies == ()  # another UA is another, empty jar

    key, path = new_key(), state_path(tmp_path)
    store = file_store(path, key)
    await store.initialize(RuntimeState())
    await store.save(RuntimeState(cookie_jar=jar))
    store.close()
    assert (await file_store(path, key).load()).cookie_jar == jar  # UA and cookies come back together

    cipher = StateCipher([key])
    sealed = seal_jar(cipher, jar)  # the Text column form for the runtime row (TR-13, D18)
    assert isinstance(sealed, str) and COOKIE_VALUE not in sealed and UA not in sealed
    assert open_jar(cipher, sealed, user_agent=UA) == jar
    with pytest.raises(StateUnavailable, match="UA"):
        open_jar(cipher, sealed, user_agent=OTHER_UA)
    with pytest.raises(StateUnavailable):
        open_jar(StateCipher([new_key()]), sealed)


def test_jar_warms_at_most_once_per_target_date():
    from ggwork_pick.observe.trends.cookies import Cookie

    jar = warm_jar()
    assert jar.warmed_on == TARGET
    assert not jar.can_warm(TARGET)  # 20:30 and 00:10 of one session are one target date (D23)
    with pytest.raises(ValueError):
        jar.warmed([Cookie("NID", "again", domain=".google.com")], day=TARGET, now=START)
    tomorrow = jar.warmed([Cookie("NID", "tomorrow", domain=".google.com")], day=TARGET + timedelta(days=1), now=START)
    assert tomorrow.warmed_on == TARGET + timedelta(days=1)
    assert [c.name for c in tomorrow.cookies] == ["NID"]  # a Set-Cookie replaces the cookie of the same name

    expiring = tomorrow.updated([Cookie("AEC", "short-lived", domain=".google.com", expires=int((START + timedelta(hours=1)).timestamp()))], now=START)
    assert "AEC=short-lived" in expiring.request_headers(START)["Cookie"]
    assert "AEC" not in expiring.request_headers(START + timedelta(hours=2))["Cookie"]


def test_jar_refuses_header_injection():
    from ggwork_pick.observe.trends.cookies import Cookie, CookieJar

    for value in ("a;b", "a\r\nX-Evil: 1", "a" + chr(0), "caf" + chr(0xE9)):
        with pytest.raises(ValueError) as caught:
            Cookie("NID", value)
        assert value not in str(caught.value)
    for name in ("", "N ID", "N;ID", "N=ID"):
        with pytest.raises(ValueError):
            Cookie(name, "v")
    for agent in ("", "Mozilla\r\nX-Evil: 1", "x" * 1000):
        with pytest.raises(ValueError):
            CookieJar.fresh(agent)
    with pytest.raises(ValueError):  # one name, domain and path holds one value
        CookieJar(UA, (Cookie("NID", "a"), Cookie("NID", "b")))


@pytest.mark.asyncio
async def test_no_secret_in_logs(tmp_path, caplog):
    """Keys and cookie values never reach a log line, a message, a repr or a traceback, on success or failure."""
    from ggwork_pick.observe.crypto import KEY_VARIABLE, StateCipher, load_cipher
    from ggwork_pick.observe.errors import ObserveFailure, describe_error
    from ggwork_pick.observe.state import FileStateStore, RuntimeState

    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("test_no_secret_in_logs")
    old, new, path = new_key(), new_key(), state_path(tmp_path)
    jar = warm_jar()
    await file_store(path, old).initialize(RuntimeState(cookie_jar=jar))
    rotating = FileStateStore(path, load_cipher({KEY_VARIABLE: f"{new},{old}"}))
    state = await rotating.load()
    await rotating.save(state)
    rotating.close()

    shown = [repr(state), str(state), repr(jar), str(jar), repr(jar.cookies[0]), repr(StateCipher([old, new])), repr(rotating)]
    failures = []
    for attempt in (
        lambda: file_store(path, old).load(),  # the file is under the new key now
        lambda: _load_after(path, b"garbage", new),
        lambda: _load_after(path, Fernet(new).encrypt(json.dumps({"cookie_jar": COOKIE_VALUE}).encode()), new),
    ):
        try:
            await attempt()
        except ObserveFailure as exc:
            failures.append(exc)
            logger.exception("load failed")
            shown.append(describe_error(exc))
    for bad in (f"{old},{COOKIE_VALUE}", f"{COOKIE_VALUE}"):
        try:
            load_cipher({KEY_VARIABLE: bad})
        except ObserveFailure as exc:
            failures.append(exc)
            logger.exception("key refused")
            shown.append(describe_error(exc))
    assert len(failures) == 5

    text = caplog.text + "\n".join(shown)
    for secret in (old, new, COOKIE_VALUE):
        assert secret not in text


async def _load_after(path: Path, data: bytes, key: str):
    write_private(path, data)
    return await file_store(path, key).load()


LIGHT = ("ggwork_pick.observe.crypto", "ggwork_pick.observe.state", "ggwork_pick.observe.trends.cookies")
HEAVY = ("deerflow.runtime", "deerflow.config.app_config", "fastapi", "alembic", "langgraph", "dotenv", "sqlalchemy", "httpx")


@pytest.mark.parametrize("module", LIGHT)
def test_state_modules_import_light(module):
    """The local stage-0 run and the cron read the state before anything else; these modules bring no gateway, no
    database driver and no HTTP client with them (plan D7)."""
    code = (
        "import importlib, json, sys\n"
        f"sys.path[:0] = {json.dumps([str(SOURCE), str(EXTENSION_API)])}\n"
        f"importlib.import_module({module!r})\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    loaded = json.loads(done.stdout.strip().splitlines()[-1])
    assert [name for name in loaded if any(name == heavy or name.startswith(heavy + ".") for heavy in HEAVY)] == []


@pytest.mark.asyncio
async def test_edges_refuse_cleanly(tmp_path, caplog):
    """The less travelled refusals: each is a named Refused or StateUnavailable, never a stack trace or a value."""
    from ggwork_pick.observe.crypto import KEY_FILE_VARIABLE, MAX_KEY_FILE_BYTES, StateCipher, load_cipher
    from ggwork_pick.observe.errors import Refused, StateUnavailable
    from ggwork_pick.observe.state import RuntimeState, default_state_path
    from ggwork_pick.observe.trends.cookies import open_jar, seal_jar

    assert default_state_path() == Path.home() / ".ggwork-obs" / "trends-state.json"
    with pytest.raises(Refused):
        StateCipher([])

    key_file = tmp_path / "state.key"
    for content, reason in ((b" \n, \n", "没有密钥"), ("密钥".encode(), "ASCII"), (b"k" * (MAX_KEY_FILE_BYTES + 1), "上限")):
        write_private(key_file, content)
        with pytest.raises(Refused, match=reason):
            load_cipher({KEY_FILE_VARIABLE: str(key_file)})
    with pytest.raises(Refused, match="普通文件"):
        load_cipher({KEY_FILE_VARIABLE: str(tmp_path)})

    key, path = new_key(), state_path(tmp_path)
    await file_store(path, key).initialize(RuntimeState())
    linked_directory = tmp_path / "linked-obs"
    linked_directory.symlink_to(path.parent)  # the file is reached through a symlinked directory
    with pytest.raises(StateUnavailable, match="符号链接"):
        await file_store(linked_directory / path.name, key).load()
    with pytest.raises(StateUnavailable, match="建不了"):  # a file where the directory should go
        await file_store(path / "nested" / "trends-state.json", key).initialize(RuntimeState())

    old, new = new_key(), new_key()
    sealed = seal_jar(StateCipher([old]), warm_jar())
    caplog.set_level(logging.WARNING)
    assert open_jar(StateCipher([new, old]), sealed) == warm_jar()
    assert "key #2 of 2" in caplog.text
    with pytest.raises(StateUnavailable, match="ASCII"):
        open_jar(StateCipher([old]), "密文")
    with pytest.raises(StateUnavailable, match="格式"):
        open_jar(StateCipher([old]), Fernet(old).encrypt(b'{"user_agent": 1}').decode())
