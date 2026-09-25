"""The weekly live contract check (design section 10 "端点改版"; plan TR-14; contract STATUS_CODES parse_error).

Once a week, on the Monday target date, the session asks two units with fixed parameters first thing, counted in the
day's budget like any request. A parse_error on either means the endpoint changed: the batch gets the red code
parse_error, and like every failure it writes no values. The code is carried on every later batch row until a check
passes again (status_rules: a lasting condition is written again by every run row). A check passes only when both
units got a usable answer (ok, ok_zero or no_data: the parser read it); a check that got none (a 429, a 5xx, a wall,
a timeout, a unit that never ran) has no verdict and clears nothing.

The check is off unless PICK_OBS_CONTRACT_CHECK=1 (settings.py), and turning it on waits for U12's approval.
"""

from collections.abc import Iterable, Mapping
from datetime import date

from ggwork_pick.observe.trends.source import FetchStatus
from ggwork_pick.observe.trends.units import QueryUnit, unit_key

CONTRACT_TERM = "short drama"  # the canary's own US market phrase: a series Trends always has
CONTRACT_GEO = "US"
CONTRACT_GRANULARITIES = ("H", "D")  # both time ranges the parser reads
CHECK_WEEKDAY = 0  # Monday
PRIORITY = 0  # before everything: a changed endpoint shows before the day spends its budget
PARSE_ERROR = "parse_error"
PASSED = "ok"
READ = frozenset({FetchStatus.OK.value, FetchStatus.OK_ZERO.value, FetchStatus.NO_DATA.value})  # answers the parser read


def contract_check_due(target_date: date, *, enabled: bool) -> bool:
    return enabled and target_date.weekday() == CHECK_WEEKDAY


def contract_check_units() -> tuple[QueryUnit, ...]:
    return tuple(
        QueryUnit(
            key=unit_key("contract_check", CONTRACT_TERM, CONTRACT_GEO, granularity),
            item="contract_check",
            geo=CONTRACT_GEO,
            terms=(CONTRACT_TERM,),
            bare=CONTRACT_TERM,
            granularity=granularity,
            priority=PRIORITY,
        )
        for granularity in CONTRACT_GRANULARITIES
    )


def contract_check_verdict(units: Iterable[QueryUnit], statuses: Mapping[str, str]) -> str | None:
    """parse_error when any check unit came back as one; ok when every check unit of the plan was answered and read;
    None otherwise: no check that day, or one that got no usable answer, which confirms nothing."""
    checks = [unit.key for unit in units if unit.item == "contract_check"]
    answered = [statuses.get(key) for key in checks]
    if FetchStatus.PARSE_ERROR.value in answered:
        return PARSE_ERROR
    return PASSED if checks and all(status in READ for status in answered) else None
