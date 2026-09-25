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
