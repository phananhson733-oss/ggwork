"""Five-title completion evidence: HTTP counted by group, coverage by original drama."""

from collections.abc import Mapping

from sqlalchemy import select

from ggwork_pick.models import obs_raw, obs_requests
from ggwork_pick.observe.trends.units import drama_units


def valid_groups(plan, target):
    units = plan.tasks.planned
    members = [m for u in units for m in drama_units(u)]
    return (
        type(target) is int
        and len(members) == target
        and not plan.tasks.truncated
        and all(u.members and u.geo == "WW" and u.granularity == "D" and u.timeline and not u.related for u in units)
        and len({u.key for u in units}) == len(units)
        and len({u.identity for u in units}) == len(units)
        and all(u.identity == u.key for u in units)
        and len({m.key for m in members}) == target
        and len({m.identity for m in members}) == target
    )


async def certificate(step, batch_id, plan, summary: Mapping):
    from ggwork_pick.observe.trends.recovery import KEY, USABLE, document

    notes = document(plan.notes[KEY])
    requests = (
        (
            await step.execute(
                select(obs_requests.c.id, obs_requests.c.identity, obs_requests.c.endpoint, obs_requests.c.status_code, obs_requests.c.budget_item).where(
                    obs_requests.c.batch_id == batch_id
                )
            )
        )
        .mappings()
        .all()
    )
    raw = (
        (
            await step.execute(
                select(obs_raw.c.params_json, obs_raw.c.identity, obs_raw.c.line_index, obs_raw.c.fetch_status, obs_raw.c.request_id).where(
                    obs_raw.c.batch_id == batch_id, obs_raw.c.line_role == "bare"
                )
            )
        )
        .mappings()
        .all()
    )
    successful = {(r["identity"], r["endpoint"]) for r in requests if r["status_code"] == 200}
    multiline = {r["id"]: r["identity"] for r in requests if r["status_code"] == 200 and r["endpoint"] == "multiline"}
    lines = {document(r["params_json"]).get("unit"): r for r in raw}
    completed = document(summary.get("units"))
    identities = {u.identity for u in plan.tasks.planned}
    known_requests = all(
        (r["endpoint"] == "warmup" and r["identity"] is None and r["budget_item"] == "warmup")
        or (r["endpoint"] in ("explore", "multiline") and r["identity"] in identities and r["budget_item"] in ("title", "retry", "probe"))
        for r in requests
    )
    valid = valid_groups(plan, notes["target"]) and known_requests and len(requests) <= plan.notes["cap"]
    for u in plan.tasks.planned:
        progress = completed.get(u.key, {})
        valid = valid and (u.identity, "explore") in successful and (u.identity, "multiline") in successful and progress.get("series") in USABLE
        for index, (key, identity, term) in enumerate(u.members):
            row = lines.get(key)
            params = document(row["params_json"]) if row else {}
            valid = (
                valid
                and row is not None
                and (
                    row["identity"] == identity
                    and row["line_index"] == index
                    and row["fetch_status"] in USABLE
                    and multiline.get(row["request_id"]) == u.identity
                    and params.get("query_unit") == u.key
                    and params.get("term") == term
                    and params.get("terms") == list(u.terms)
                    and progress.get("lines", {}).get(term) in USABLE
                )
            )
    qualified = bool(
        valid
        and notes.get("groups") == len(plan.tasks.planned)
        and plan.notes.get("late_admission") is False
        and "extinguished" in summary
        and summary["extinguished"] is None
        and summary.get("fetched_units") == len(plan.tasks.planned)
        and len(requests) == summary.get("requests_reserved")
        and len(raw) == notes["target"]
        and len(lines) == notes["target"]
        and len(completed) == len(plan.tasks.planned)
    )
    return {**notes, "qualified": qualified, "request_rows": len(requests), "raw_rows": len(raw)}
