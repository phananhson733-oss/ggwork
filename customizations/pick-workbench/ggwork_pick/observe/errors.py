"""Exit statuses and error redaction for the observe commands (plan 5 errors.py, D5, D34).

A database or HTTP error can quote a value: a DSN, a cookie, a title. Only the error class and the SQLSTATE ever reach
a log or the terminal. ObserveFailure and its subclasses are raised by this package with a message written to name
things (a variable, a table, a step), never to carry a value, so they keep their message; the SQLSTATE of the database
error they wrap (`raise StateUnavailable(...) from db_error`) is added to it, so exit 3 still tells a missing grant
(42501, regrant) from a database that was never reached. An exception group (asyncio.TaskGroup) is described member by
member and exits with its most severe member's status.
"""

from collections.abc import Iterator
from enum import IntEnum
from typing import ClassVar


class ExitCode(IntEnum):
    OK = 0  # done, or nothing to do (triggered before the start time, the target date already published)
    FAILED = 1  # the run failed part-way; the next trigger resumes from the persisted state
    REFUSED = 2  # usage error, missing configuration or a failed self-check: refused before any HTTP (D5)
    STATE_UNAVAILABLE = 3  # the persisted state or the runtime row is unreadable or missing: the day does not run (D34)
    INTERRUPTED = 130  # SIGINT; rerunning is safe


# Which member's status an exception group exits with: the one an operator must act on outranks the one a rerun clears.
SEVERITY = (ExitCode.STATE_UNAVAILABLE, ExitCode.REFUSED, ExitCode.INTERRUPTED, ExitCode.FAILED, ExitCode.OK)


class ObserveFailure(Exception):
    """A failure that chooses its exit status. The message names things and never carries a value."""

    exit_code: ClassVar[ExitCode] = ExitCode.FAILED


class Refused(ObserveFailure):
    """Refused before doing anything: a bad command line, a missing variable, a failed self-check."""

    exit_code = ExitCode.REFUSED


class StateUnavailable(ObserveFailure):
    """The persisted state cannot be read, or the runtime row is missing: never start afresh in its place (design 4.3)."""

    exit_code = ExitCode.STATE_UNAVAILABLE


def _linked(exc: BaseException) -> tuple[BaseException | None, ...]:
    members = tuple(exc.exceptions) if isinstance(exc, BaseExceptionGroup) else ()
    return (getattr(exc, "orig", None), exc.__cause__, exc.__context__, *members)


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """exc, what it wraps (SQLAlchemy's .orig), its causes and contexts, and a group's members, each once."""
    seen, pending = set(), [exc]
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(_linked(current))


def sqlstate(exc: BaseException) -> str | None:
    """A database error's SQLSTATE: asyncpg's sqlstate, psycopg's pgcode, or either one wrapped (DBAPIError.orig, cause)."""
    for candidate in _chain(exc):
        for attribute in ("sqlstate", "pgcode"):
            state = getattr(candidate, attribute, None)
            if isinstance(state, str) and state:
                return state
    return None


def describe_error(exc: BaseException) -> str:
    """Safe text for a log line: the class and SQLSTATE, after an ObserveFailure's own value-free message."""
    name = type(exc).__name__
    if isinstance(exc, BaseExceptionGroup):
        return f"{name}[{'；'.join(describe_error(member) for member in exc.exceptions)}]"
    message = str(exc) if isinstance(exc, ObserveFailure) else ""
    text = f"{name}：{message}" if message else name
    state = sqlstate(exc)
    return f"{text}（SQLSTATE {state}）" if state is not None else text


def exit_code_for(exc: BaseException) -> ExitCode:
    if isinstance(exc, BaseExceptionGroup):
        return min((exit_code_for(member) for member in exc.exceptions), key=SEVERITY.index, default=ExitCode.FAILED)
    if isinstance(exc, KeyboardInterrupt):
        return ExitCode.INTERRUPTED
    if isinstance(exc, ObserveFailure):
        return exc.exit_code
    return ExitCode.FAILED
