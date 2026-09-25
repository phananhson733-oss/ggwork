"""The weekly live contract check (design section 10 "端点改版"; plan TR-14; contract STATUS_CODES parse_error).

Once a week, on the Monday target date, the session asks two units with fixed parameters first thing, counted in the
day's budget like any request. A parse_error on either means the endpoint changed: the batch gets the red code
parse_error, and like every failure it writes no values. The code is carried on every later batch row until a check
passes again (status_rules: a lasting condition is written again by every run row).

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
    """None when the batch ran no contract check (or none of its units ran); parse_error when any came back as one;
    ok otherwise."""
    ran = [statuses[unit.key] for unit in units if unit.item == "contract_check" and unit.key in statuses]
    if not ran:
        return None
    return PARSE_ERROR if FetchStatus.PARSE_ERROR.value in ran else "ok"
