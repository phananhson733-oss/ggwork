"""lark_credential_lock can give up at a deadline instead of waiting as long as the holder takes.

The GGWork pick lark_cli tool takes a user's credential lock before it copies their credentials, while that user's
own authorization flow may hold the same lock for up to 45 seconds (docs/pick-workbench/lark-personal-auth.md).
Without a deadline the lock waits exactly as before.
"""

from __future__ import annotations

import fcntl
import threading
import time
from pathlib import Path

import pytest

from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path) -> Path:
    base = tmp_path / "home"
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=base))
    return base


def _lock_file(user_id: str) -> Path:
    path = paths_module.get_paths().user_dir(user_id) / "integrations" / f".{lark_cli.INTEGRATION_ID}.credentials.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _in_thread(target) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def test_a_deadline_bounds_the_wait_for_another_thread():
    held, release = threading.Event(), threading.Event()

    def hold():
        with lark_cli.lark_credential_lock("alice"):
            held.set()
            release.wait(5)

    holder = _in_thread(hold)
    assert held.wait(5)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        with lark_cli.lark_credential_lock("alice", deadline=time.monotonic() + 0.2):
            pytest.fail("the lock is held elsewhere")
    assert time.monotonic() - started < 2
    release.set()
    holder.join(5)
    with lark_cli.lark_credential_lock("alice", deadline=time.monotonic() + 1):
        pass


def test_a_deadline_bounds_the_wait_for_another_process():
    """A Gateway worker process holds the file lock; the in-process lock is free."""
    with _lock_file("alice").open("a+b") as other_worker:
        fcntl.flock(other_worker, fcntl.LOCK_EX)
        with pytest.raises(TimeoutError):
            with lark_cli.lark_credential_lock("alice", deadline=time.monotonic() + 0.2):
                pytest.fail("another process holds the lock")
        fcntl.flock(other_worker, fcntl.LOCK_UN)
    # Giving up released the in-process lock and the file.
    with lark_cli.lark_credential_lock("alice", deadline=time.monotonic() + 1):
        pass


def test_other_users_are_not_held_up():
    with lark_cli.lark_credential_lock("alice"):
        with lark_cli.lark_credential_lock("bob", deadline=time.monotonic() + 0.2):
            pass


def test_the_lock_is_taken_once_it_frees_up_before_the_deadline():
    held, order = threading.Event(), []

    def hold():
        with lark_cli.lark_credential_lock("alice"):
            held.set()
            time.sleep(0.2)
            order.append("released")

    holder = _in_thread(hold)
    assert held.wait(5)
    with lark_cli.lark_credential_lock("alice", deadline=time.monotonic() + 5):
        order.append("taken")
    holder.join(5)
    assert order == ["released", "taken"]


def test_without_a_deadline_the_lock_waits_as_before():
    held, order = threading.Event(), []

    def hold():
        with lark_cli.lark_credential_lock("alice"):
            held.set()
            time.sleep(0.3)
            order.append("released")

    holder = _in_thread(hold)
    assert held.wait(5)
    with lark_cli.lark_credential_lock("alice"):
        order.append("taken")
    holder.join(5)
    assert order == ["released", "taken"]


def test_a_passed_deadline_still_takes_a_free_lock():
    with lark_cli.lark_credential_lock("alice", deadline=time.monotonic() - 1):
        pass
