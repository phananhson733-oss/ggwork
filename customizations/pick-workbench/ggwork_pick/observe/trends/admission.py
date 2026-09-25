"""Is tonight's task list a canary at all? The payload gate between S6 and S7 and before every canary night (plan
section 9; G3 review seam 1).

A published shared catalog batch is not enough. Its dramas can all be older than 14 days, or the stage 0 controls can
be gone from it; then the night is a dozen market series, comes back at 100% coverage and proves nothing about the
load or the controls. So a canary's task list, as it is cut to the plan, must hold:

- planned requests (the units' own, before warm-up, probes and retries) at least MIN_PLAN_SHARE of the day's plan;
- matched positive controls (control entries of group "positive" whose identity the batch holds) at least
  MIN_POSITIVE_SHARE of the list's positive controls.

Short of either, the run refuses (exit 2) before any request, on the target date's refusal row: status code
not_published_low_coverage (a canary never publishes, so on a canary row that code comes from here and nowhere else)
and this overview, with the reasons, in summary_json's "admission". Such a day never counts toward the canary's three-
or seven-day pass. An admitted batch keeps the overview in plan_json's notes. `python -m ggwork_pick.observe.trends
preflight` prints the same overview for tonight without sending anything (S6 -> S7). Only the canary modes are gated.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from ggwork_pick.observe.trends.budget import ModeLimits
from ggwork_pick.observe.trends.units import SessionPlan

MIN_PLAN_SHARE = 0.8
MIN_POSITIVE_SHARE = 0.5
POSITIVE = "positive"
SHOWN_MISSING = 5  # status and preflight name this many missing controls; the count is always whole
REFUSAL_CODE = "not_published_low_coverage"


@dataclass(frozen=True)
class Admission:
    """The gate's two shares; tests of other things pass a looser one (the production default is this one)."""

    min_plan_share: float = MIN_PLAN_SHARE
    min_positive_share: float = MIN_POSITIVE_SHARE

    def __post_init__(self) -> None:
        if not all(0 <= share <= 1 for share in (self.min_plan_share, self.min_positive_share)):
            raise ValueError("admission shares are between 0 and 1")


STRICT = Admission()


def _controls(notes: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    groups = notes.get("controls")
    return {group: dict(counts) for group, counts in groups.items()} if isinstance(groups, Mapping) else {}


def overview(plan: SessionPlan, limits: ModeLimits, admission: Admission = STRICT) -> dict[str, Any]:
    """The night's task list in figures, and why it is not a canary (empty when it is)."""
    notes = plan.notes
    controls = _controls(notes)
    missing = [str(identity) for identity in notes.get("missing_controls", ())]
    positive = controls.get(POSITIVE, {"listed": 0, "matched": 0})
    figures = {
        "plan": limits.plan,
        "min_requests": math.ceil(limits.plan * admission.min_plan_share),
        "planned_requests": plan.tasks.planned_http,
        "planned_units": len(plan.tasks.planned),
        "truncated_units": len(plan.tasks.truncated),
        "catalog_batch_id": plan.catalog_batch_id,
        "catalog_dramas": notes.get("catalog_dramas"),
        "recent_dramas": notes.get("recent_dramas"),
        "controls": controls,
        "min_positive": math.ceil(positive["listed"] * admission.min_positive_share),
        "missing_controls": len(missing),
        "missing_first": missing[:SHOWN_MISSING],
    }
    return {**figures, "reasons": list(_reasons(figures, positive))}


def _reasons(figures: Mapping[str, Any], positive: Mapping[str, int]):
    if figures["planned_requests"] < figures["min_requests"]:
        yield f"计划请求 {figures['planned_requests']} 次，不到门槛 {figures['min_requests']} 次（计划量 {figures['plan']} 次的 {MIN_PLAN_SHARE:.0%}）"
    if positive["matched"] < figures["min_positive"]:
        yield f"正对照只匹配到 {positive['matched']}/{positive['listed']} 条，不到门槛 {figures['min_positive']} 条（一半）"


def log_line(figures: Mapping[str, Any]) -> str:
    """One line for the log: the figures that matter, never a title (identities only in status and preflight)."""
    groups = ", ".join(f"{group} {counts['matched']}/{counts['listed']}" for group, counts in sorted(figures["controls"].items()))
    return (
        f"{figures['planned_requests']} requests planned of {figures['plan']} (at least {figures['min_requests']}), "
        f"{figures['planned_units']} units, {figures['truncated_units']} truncated, {figures['recent_dramas']} recent dramas, "
        f"controls {groups or '-'}, {figures['missing_controls']} missing"
    )


def admitted(figures: Mapping[str, Any]) -> bool:
    return not figures["reasons"]


def with_overview(plan: SessionPlan, figures: Mapping[str, Any]) -> SessionPlan:
    """The plan with its overview kept in the notes (plan_json), for status to show."""
    return replace(plan, notes=MappingProxyType({**plan.notes, "admission": dict(figures)}))


def refusal_text(figures: Mapping[str, Any]) -> str:
    return "金丝雀负载不够，不算有效金丝雀日，拒跑：" + "；".join(figures["reasons"]) + "（先跑 preflight 看缺什么，手册 trends-session.md）"
