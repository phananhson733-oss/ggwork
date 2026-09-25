"""TR-01: error redaction and exit statuses (plan 5 errors.py).

Database and HTTP errors can quote a value: a DSN, a cookie, a title. What reaches a log or the terminal is the error
class and the SQLSTATE only; failures raised by the observe code itself carry a message written to name things, not
values, and keep it.
"""

import pytest
from sqlalchemy.exc import DBAPIError

SECRET = "postgresql://observer:hunter2@db.example/pick"


class _AsyncpgLike(Exception):
    sqlstate = "40P01"


class _PsycopgLike(Exception):
    pgcode = "23505"


def test_describe_error_keeps_class_and_sqlstate_only():
    from ggwork_pick.observe.errors import describe_error, sqlstate

    raw = _AsyncpgLike(f"deadlock detected on {SECRET}")
    assert sqlstate(raw) == "40P01"
    assert describe_error(raw) == "_AsyncpgLike（SQLSTATE 40P01）"

    wrapped = DBAPIError(f"select * from t where dsn = '{SECRET}'", None, raw)
    assert sqlstate(wrapped) == "40P01"
    assert describe_error(wrapped) == "DBAPIError（SQLSTATE 40P01）"

    assert describe_error(_PsycopgLike(SECRET)) == "_PsycopgLike（SQLSTATE 23505）"
    assert describe_error(ValueError(SECRET)) == "ValueError"


def test_sqlstate_found_through_the_cause_chain():
    from ggwork_pick.observe.errors import describe_error, sqlstate

    try:
        try:
            raise _AsyncpgLike(SECRET)
        except _AsyncpgLike as inner:
            raise RuntimeError(f"step failed: {SECRET}") from inner
    except RuntimeError as outer:
        assert sqlstate(outer) == "40P01"
        assert describe_error(outer) == "RuntimeError（SQLSTATE 40P01）"


def test_sqlstate_ignores_non_text_and_cycles():
    from ggwork_pick.observe.errors import sqlstate

    odd = Exception("x")
    odd.sqlstate = 42501  # not text: not trusted as a code
    assert sqlstate(odd) is None
    first, second = Exception("a"), Exception("b")
    first.__cause__, second.__cause__ = second, first
    assert sqlstate(first) is None


def test_own_failures_keep_their_message():
    from ggwork_pick.observe.errors import ObserveFailure, Refused, StateUnavailable, describe_error

    assert describe_error(StateUnavailable("运行时行 trends 不存在")) == "StateUnavailable：运行时行 trends 不存在"
    assert describe_error(Refused("")) == "Refused"
    assert issubclass(Refused, ObserveFailure) and issubclass(StateUnavailable, ObserveFailure)


class _InsufficientPrivilege(Exception):
    sqlstate = "42501"


def _raised(exc: BaseException, *, cause: BaseException | None = None) -> BaseException:
    """exc as `raise exc from cause` leaves it: __cause__ and __context__ set, traceback attached."""
    try:
        raise exc from cause
    except BaseException as caught:  # noqa: BLE001 - the test inspects whatever was raised
        return caught


def test_own_failure_keeps_the_sqlstate_it_wraps():
    """`raise StateUnavailable(...) from db_error` must still tell 42501 (regrant) from a lost connection (D34)."""
    from ggwork_pick.observe.errors import Refused, StateUnavailable, describe_error, exit_code_for

    denied = _InsufficientPrivilege(f"permission denied for table ggwp_obs_runtime via {SECRET}")
    direct = _raised(StateUnavailable("运行时行读不到"), cause=denied)
    assert describe_error(direct) == "StateUnavailable：运行时行读不到（SQLSTATE 42501）"
    assert exit_code_for(direct) == 3

    wrapped = _raised(StateUnavailable("运行时行读不到"), cause=DBAPIError(f"select * from t -- {SECRET}", None, denied))
    assert describe_error(wrapped) == "StateUnavailable：运行时行读不到（SQLSTATE 42501）"

    assert describe_error(_raised(Refused(""), cause=denied)) == "Refused（SQLSTATE 42501）"
    for text in (describe_error(direct), describe_error(wrapped)):
        assert "hunter2" not in text and "permission denied" not in text


def test_exception_groups_are_described_member_by_member():
    """A TaskGroup's failures arrive as an ExceptionGroup: each member is described, none of their values."""
    from ggwork_pick.observe.errors import StateUnavailable, describe_error, sqlstate

    group = ExceptionGroup(f"unhandled errors near {SECRET}", [StateUnavailable("运行时行读不到"), _AsyncpgLike(SECRET)])
    assert describe_error(group) == "ExceptionGroup[StateUnavailable：运行时行读不到；_AsyncpgLike（SQLSTATE 40P01）]"
    assert sqlstate(ExceptionGroup("x", [ValueError(SECRET), _PsycopgLike(SECRET)])) == "23505"
    outer = _raised(StateUnavailable("运行时行读不到"), cause=ExceptionGroup("x", [_InsufficientPrivilege(SECRET)]))
    assert describe_error(outer) == "StateUnavailable：运行时行读不到（SQLSTATE 42501）"
    nested = ExceptionGroup("x", [ExceptionGroup("y", [ValueError(SECRET)])])
    assert describe_error(nested) == "ExceptionGroup[ExceptionGroup[ValueError]]"
    assert "hunter2" not in describe_error(group)


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        (["StateUnavailable"], 3),
        (["failure", "Refused"], 2),
        (["Refused", "StateUnavailable", "failure"], 3),
        (["failure", "group:StateUnavailable"], 3),
        (["interrupt", "failure"], 130),
        (["interrupt", "StateUnavailable"], 3),
        (["failure", "failure"], 1),
    ],
)
def test_exit_code_for_a_group_is_its_most_severe_member(members, expected):
    """3 (the state is unreadable) > 2 (refused) > 130 (interrupted) > 1: the one needing an operator wins."""
    from ggwork_pick.observe import errors

    def build(member: str) -> BaseException:
        if member == "failure":
            return RuntimeError("boom")
        if member == "interrupt":
            return KeyboardInterrupt()
        if member.startswith("group:"):
            return ExceptionGroup("inner", [build(member.removeprefix("group:"))])
        return getattr(errors, member)("名字，不是值")

    assert errors.exit_code_for(BaseExceptionGroup("outer", [build(member) for member in members])) == expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (KeyboardInterrupt(), 130),
        ("Refused", 2),
        ("StateUnavailable", 3),
        ("ObserveFailure", 1),
        (RuntimeError("boom"), 1),
        (_AsyncpgLike("x"), 1),
    ],
)
def test_exit_code_for(exc, expected):
    from ggwork_pick.observe import errors

    if isinstance(exc, str):
        exc = getattr(errors, exc)("名字，不是值")
    assert errors.exit_code_for(exc) == expected
