"""User-approved daily recovery campaign (2026-10-07): 10, 30, then 100; immutable nightly evidence.

A completion certificate is written under the session lease with its finished batch. It records checks against
actual request/raw rows while they still exist, so later raw retention cannot silently demote an established stage.
Historical batches and stop evidence are never deleted. Any extinguished date in this campaign stops future runs.
"""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import date

from sqlalchemy import select

from ggwork_pick.models import obs_batches, obs_budget, obs_raw, obs_requests
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.lease import ReadStep
from ggwork_pick.observe.trends import pacing
from ggwork_pick.observe.trends.budget import ModeLimits
from ggwork_pick.observe.trends.canary import SourceUnits
from ggwork_pick.observe.trends.top_dramas import SOURCE_NAME, TopDramasTaskSource
from ggwork_pick.observe.trends.units import SessionPlan, drama_units

KEY = "daily_recovery"
POLICY = "daily-v1"
BATCH_POLICY = "daily-five-v2"
STAGES = (10, 30, 100)
USABLE = frozenset({"ok", "ok_zero", "no_data"})


def document(value: object) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


async def qualified_nights(step: ReadStep, since: date, before: date, *, batch_size: int = 1) -> int:
    found = await step.execute(
        select(obs_batches)
        .where(
            obs_batches.c.channel == "trends",
            obs_batches.c.collect_mode == "stable",
            obs_batches.c.target_date >= str(since),
            obs_batches.c.target_date < str(before),
            obs_batches.c.finished_at.is_not(None),
            obs_batches.c.outcome == "withheld",
        )
        .order_by(obs_batches.c.target_date)
    )
    qualified = 0
    for row in found.mappings():
        plan = document(row["plan_json"])
        notes = document(plan.get("notes"))
        planned = document(notes.get(KEY))
        proof = document(document(row["summary_json"]).get(KEY))
        target = STAGES[min(qualified, 2)]
        groups = planned.get("groups") if batch_size == 5 else target
        if type(groups) is not int or not 1 <= groups <= target:
            continue
        if batch_size == 5:
            from ggwork_pick.observe.trends.grouped_recovery import valid_groups

            try:
                if not valid_groups(SessionPlan.from_dict(plan), target) or proof.get("groups") != groups:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
        if (
            plan.get("source") == SOURCE_NAME
            and plan.get("granularity") == "D"
            and plan.get("related") is False
            and notes.get("pace") == pacing.recovery_note(batch_size)
            and planned.get("policy") == (BATCH_POLICY if batch_size == 5 else POLICY)
            and planned.get("since") == str(since)
            and planned.get("target") == target
            and planned.get("qualified_nights") == qualified
            and notes.get("late_admission") is False
            and proof.get("qualified") is True
            and proof.get("policy") == (BATCH_POLICY if batch_size == 5 else POLICY)
            and proof.get("since") == str(since)
            and proof.get("target") == target
            and row["planned_units"] == groups
            and row["fetched_units"] == groups
            and row["requests"] >= 2 * groups
            and proof.get("request_rows", 0) >= 2 * groups
            and proof.get("raw_rows") == target
        ):
            qualified += 1
            if qualified == 3:
                break
    return qualified


async def halted(step: ReadStep, since: date) -> bool:
    # Budget stop facts are committed with the failing HTTP record, including a crash before batch finalization.
    found = await step.execute(
        select(obs_budget.c.budget_day)
        .where(
            obs_budget.c.channel == "trends",
            obs_budget.c.budget_day >= str(since),
            obs_budget.c.extinguished_at.is_not(None),
        )
        .limit(1)
    )
    return found.first() is not None


async def active_campaign(step: ReadStep) -> tuple[date, str | None, dict] | None:
    found = await step.execute(
        select(obs_batches.c.plan_json)
        .where(
            obs_batches.c.channel == "trends",
            obs_batches.c.planned_units.is_not(None),
        )
        .order_by(obs_batches.c.target_date.desc())
        .limit(1)
    )
    plan = document(found.scalar())
    notes = document(plan.get("notes"))
    marker = document(notes.get(KEY))
    return (date.fromisoformat(marker["since"]), marker.get("policy"), document(notes.get("pace"))) if marker else None


async def active_since(step: ReadStep) -> date | None:
    active = await active_campaign(step)
    return active[0] if active else None


class DailyRecoverySource:
    name = SOURCE_NAME  # existing table contract still reads the actual nightly plan

    def __init__(self, since: date, *, batch_size: int = 1, approval: Mapping | None = None):
        self.since = since
        self.batch_size = batch_size
        self.approval = approval

    async def units(self, step: ReadStep, *, target_date: date) -> SourceUnits:
        if target_date < self.since:
            raise Refused("尚未到日级恢复起点")
        qualified = await qualified_nights(step, self.since, target_date, batch_size=self.batch_size)
        target = STAGES[min(qualified, 2)]
        found = await TopDramasTaskSource(target=target, batch_size=self.batch_size).units(step, target_date=target_date)
        return replace(
            found,
            notes={
                **found.notes,
                KEY: {
                    "policy": BATCH_POLICY if self.batch_size == 5 else POLICY,
                    **({"groups": len(found.units)} if self.batch_size == 5 else {}),
                    "since": str(self.since),
                    "target": target,
                    "qualified_nights": qualified,
                    **({"approval": dict(self.approval)} if self.approval else {}),
                },
            },
        )


def limits_for(limits: ModeLimits, notes: Mapping) -> ModeLimits:
    target = document(notes.get(KEY)).get("target")
    if type(target) is not int or target not in STAGES:
        raise Refused("日级恢复任务缺少合法阶段，不发送请求")
    marker = document(notes.get(KEY))
    groups = marker.get("groups") if marker.get("policy") == BATCH_POLICY else target
    if type(groups) is not int or not (target + 4) // 5 <= groups <= target:
        raise Refused("日级恢复查询组数非法")
    cap = min(limits.cap, groups * 2 + 20)
    return replace(limits, plan=min(limits.plan, cap), cap=cap)


def require_same_campaign(plan: SessionPlan, since: date, *, batch_size: int = 1) -> None:
    notes = document(plan.notes.get(KEY))
    if (
        notes.get("policy") != (BATCH_POLICY if batch_size == 5 else POLICY)
        or notes.get("since") != str(since)
        or plan.granularity != "D"
        or plan.related
        or any(bool(u.members) != (batch_size == 5) for u in plan.tasks.planned)
        or plan.notes.get("pace") != pacing.recovery_note(batch_size)
    ):
        raise Refused("当晚已有其他采集计划，不能切换恢复合同后续跑")


def overview(plan: SessionPlan, figures: Mapping) -> dict:
    target = document(plan.notes.get(KEY)).get("target")
    units = tuple(member for u in plan.tasks.planned for member in drama_units(u))
    valid = (
        target in STAGES
        and len(units) == target
        and not plan.tasks.truncated
        and all(u.geo == "WW" and u.granularity == "D" and u.timeline and not u.related and u.identity for u in units)
        and len({u.identity for u in units}) == target
    )
    return {
        **figures,
        "cap": plan.notes["cap"],
        "min_requests": 2 * len(plan.tasks.planned),
        KEY: dict(plan.notes[KEY]),
        "reasons": [] if valid else ["日级恢复候选不足或任务形状不符，不计有效夜晚"],
    }


async def certificate(step: ReadStep, batch_id: str, plan: SessionPlan, summary: Mapping) -> dict:
    notes = document(plan.notes[KEY])
    if notes.get("policy") == BATCH_POLICY:
        from ggwork_pick.observe.trends.grouped_recovery import certificate as grouped_certificate

        return await grouped_certificate(step, batch_id, plan, summary)
    target = notes["target"]
    units = plan.tasks.planned
    requests = (
        await step.execute(
            select(obs_requests.c.endpoint, obs_requests.c.status_code, obs_requests.c.identity).where(
                obs_requests.c.batch_id == batch_id,
            )
        )
    ).all()
    raw = (
        await step.execute(
            select(obs_raw.c.params_json, obs_raw.c.fetch_status).where(
                obs_raw.c.batch_id == batch_id,
                obs_raw.c.line_role == "bare",
            )
        )
    ).all()
    raw_by_unit = {document(params).get("unit"): status for params, status in raw}
    successful = {(identity, endpoint) for endpoint, status, identity in requests if status == 200}
    completed = document(summary.get("units"))
    qualified = (
        len(units) == target
        and not plan.tasks.truncated
        and plan.notes.get("late_admission") is False
        and "extinguished" in summary
        and summary["extinguished"] is None
        and summary.get("fetched_units") == target
        and len(requests) == summary.get("requests_reserved")
        and len(raw) == target
        and len(completed) == target
        and all(
            raw_by_unit.get(u.key) in USABLE
            and completed.get(u.key, {}).get("series") in USABLE
            and (u.identity, "explore") in successful
            and (u.identity, "multiline") in successful
            for u in units
        )
    )
    return {**notes, "qualified": qualified, "request_rows": len(requests), "raw_rows": len(raw)}
