"""The Trends session's settings, read from the service's environment (plan TR-14, section 8, section 9, D11; design
4.9, 4.11, section 10).

Only these variables steer a session; each is checked before anything is read or sent, and a bad value is Refused
(exit 2) without echoing it:

| variable                      | values                    | default | meaning                                            |
|-------------------------------|---------------------------|---------|----------------------------------------------------|
| PICK_OBS_TRENDS_MODE          | canary1, canary2, stable  | (none)  | budget.MODES: start, plan and cap (section 9)       |
| PICK_OBS_TRENDS_PACE          | user, design              | user    | pacing.PRESETS, the pace every request keeps (G3)   |
| PICK_OBS_TRENDS_GRANULARITY   | H, D, HD                  | H       | stage 0's choice (design 4.9); HD halves the units  |
| PICK_OBS_TRENDS_ROUTE         | both, a_only, b_only      | both    | section 8's route; a_only mixes no relatedsearches  |
| PICK_OBS_CONTRACT_CHECK       | 1 to turn it on           | off     | the weekly live contract check, after U12 only      |
| PICK_OBS_PUBLISH              | 1 for live                | shadow  | D11; a canary session never publishes anyway       |
| PICK_OBS_CANARY_SINCE         | YYYY-MM-DD                | (none)  | canary termination counts days from here (TR-30)   |

Route `neither` (both gates failed) means the Trends collector does not go live at all (section 8): refused. Route
b_only keeps the canary's load as it is (its seeds and discovery queue come with TR-19); only a_only changes it here.

The mode must fit its window at the pace (capacity.mode_fit: a clear night covers every unit, one with a 429 at the
56th request and half speed after it at least 95%), or it is refused: every mode in budget.MODES does at either preset
(test_trends_capacity), so this only stops a mode or a pace changed without that check.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.trends import budget, capacity, pacing
from ggwork_pick.observe.trends import state_codec as codec

MODE_VARIABLE = "PICK_OBS_TRENDS_MODE"
PACE_VARIABLE = "PICK_OBS_TRENDS_PACE"
GRANULARITY_VARIABLE = "PICK_OBS_TRENDS_GRANULARITY"
ROUTE_VARIABLE = "PICK_OBS_TRENDS_ROUTE"
CONTRACT_CHECK_VARIABLE = "PICK_OBS_CONTRACT_CHECK"
PUBLISH_VARIABLE = "PICK_OBS_PUBLISH"
CANARY_SINCE_VARIABLE = "PICK_OBS_CANARY_SINCE"
VARIABLES = frozenset({MODE_VARIABLE, PACE_VARIABLE, GRANULARITY_VARIABLE, ROUTE_VARIABLE, CONTRACT_CHECK_VARIABLE, PUBLISH_VARIABLE, CANARY_SINCE_VARIABLE})

CANARY_MODES = ("canary1", "canary2")
GRANULARITIES = ("H", "D", "HD")  # contract GRANULARITIES
DEFAULT_GRANULARITY = "H"
ROUTES = ("both", "a_only", "b_only")  # TR-05's route keys; "neither" is refused
DEFAULT_ROUTE = "both"
SWITCH_ON = "1"


@dataclass(frozen=True)
class Settings:
    limits: budget.ModeLimits
    granularity: str
    route: str
    contract_check: bool
    publish_live: bool
    canary_since: date | None = None
    pace: str = pacing.PRODUCTION_PRESET

    @property
    def mode(self) -> str:
        return self.limits.name

    @property
    def pace_params(self) -> pacing.PacingParams:
        """The preset every request of the session is paced at."""
        return pacing.PRESETS[self.pace]

    @property
    def canary(self) -> bool:
        return self.limits.name in CANARY_MODES

    @property
    def related(self) -> bool:
        """Whether relatedsearches are mixed in (section 8: not on route a_only)."""
        return self.route != "a_only"

    @property
    def batch_mode(self) -> str:
        """What this run would publish as (the batch's mode column): a canary never publishes, so never live."""
        return "live" if self.publish_live and not self.canary else "shadow"

    @property
    def granularities(self) -> tuple[str, ...]:
        """The time ranges each drama is asked in: H, D, or both (design 4.9 scheme H+D)."""
        return ("H", "D") if self.granularity == "HD" else (self.granularity,)


def _choice(env: Mapping[str, str], name: str, allowed: tuple[str, ...], default: str | None) -> str:
    value = env.get(name, "").strip()
    if not value:
        if default is None:
            raise Refused(f"缺少 {name}（可选值：{', '.join(allowed)}）")
        return default
    if value not in allowed:
        raise Refused(f"{name} 的值不认识（可选值：{', '.join(allowed)}；为免泄露，不回显）")
    return value


def _route(env: Mapping[str, str]) -> str:
    if env.get(ROUTE_VARIABLE, "").strip() == "neither":
        raise Refused("阶段 0 两道闸门都没过（去向 neither）：Trends 采集不上线（计划第 8 节），不跑")
    return _choice(env, ROUTE_VARIABLE, ROUTES, DEFAULT_ROUTE)


def _since(env: Mapping[str, str]) -> date | None:
    value = env.get(CANARY_SINCE_VARIABLE, "").strip()
    if not value:
        return None
    try:
        return codec.decode_day(value, CANARY_SINCE_VARIABLE)
    except ValueError:
        raise Refused(f"{CANARY_SINCE_VARIABLE} 须是 YYYY-MM-DD 日期") from None


def _fitting(limits: budget.ModeLimits, pace: str) -> budget.ModeLimits:
    problem = capacity.mode_fit(limits, pacing.PRESETS[pace]).problem()
    if problem is not None:
        raise Refused(f"{problem}（{PACE_VARIABLE}={pace}，计划第 9 节）")
    return limits


def settings_from(environ: Mapping[str, str]) -> Settings:
    """The session's settings; Refused (exit 2) for a missing mode, any value it does not know, or a mode that does
    not fit its window at the pace."""
    mode = _choice(environ, MODE_VARIABLE, tuple(budget.MODES), None)
    pace = _choice(environ, PACE_VARIABLE, tuple(pacing.PRESETS), pacing.PRODUCTION_PRESET)
    return Settings(
        limits=_fitting(budget.mode_limits(mode), pace),
        granularity=_choice(environ, GRANULARITY_VARIABLE, GRANULARITIES, DEFAULT_GRANULARITY),
        route=_route(environ),
        contract_check=environ.get(CONTRACT_CHECK_VARIABLE, "").strip() == SWITCH_ON,
        publish_live=environ.get(PUBLISH_VARIABLE, "").strip() == SWITCH_ON,
        canary_since=_since(environ),
        pace=pace,
    )
