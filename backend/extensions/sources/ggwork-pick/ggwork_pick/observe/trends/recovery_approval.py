"""An explicit operator receipt authorizes one old campaign -> one later campaign.

It never removes budget rows or clears the breaker. Once the new plan exists, its
own stop evidence applies and this receipt cannot rearm it.
"""

import json
from collections.abc import Mapping
from datetime import date
from types import MappingProxyType

from sqlalchemy import select

from ggwork_pick.models import obs_batches
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.instants import instant
from ggwork_pick.observe.trends import state_codec as codec

VARIABLE = "PICK_OBS_TRENDS_RECOVERY_APPROVAL"
FIELDS = frozenset({"id", "from_since", "to_since", "previous_batch_id", "approved_at"})


def parse(raw: str, since: date | None, batch_size: int) -> Mapping | None:
    if not raw.strip():
        return None
    try:
        if len(raw) > 2048:
            raise ValueError("receipt too large")
        value = json.loads(raw)
        codec.exact_keys(value, FIELDS, "recovery approval")
        if not all(isinstance(v, str) and v and v.isprintable() and len(v) <= 200 for v in value.values()):
            raise ValueError("receipt fields")
        before = codec.decode_day(value["from_since"], "from_since")
        after = codec.decode_day(value["to_since"], "to_since")
        codec.decode_instant(value["approved_at"], "approved_at")
        if batch_size != 5 or after != since or before >= after:
            raise ValueError("receipt scope")
    except (ValueError, TypeError, KeyError):
        raise Refused("恢复授权记录格式或范围不符，不发送请求") from None
    return MappingProxyType(value)


async def transition_allowed(step, active: date, since: date | None, receipt: Mapping | None, *, now) -> bool:
    from ggwork_pick.observe.trends.recovery import KEY, document

    if receipt is None or str(active) != receipt["from_since"] or str(since) != receipt["to_since"]:
        return False
    row = (
        (
            await step.execute(
                select(obs_batches)
                .where(obs_batches.c.channel == "trends", obs_batches.c.planned_units.is_not(None))
                .order_by(obs_batches.c.target_date.desc())
                .limit(1)
            )
        )
        .mappings()
        .first()
    )
    if instant(receipt["approved_at"]) > now:
        return False
    if row is None or row["id"] != receipt["previous_batch_id"] or row["finished_at"] is None:
        return False
    marker = document(document(document(row["plan_json"]).get("notes")).get(KEY))
    return (
        marker.get("since") == str(active) and date.fromisoformat(row["target_date"]) < since and instant(receipt["approved_at"]) >= instant(row["finished_at"])
    )
