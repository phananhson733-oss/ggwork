"""Deterministic filtering and immutable candidate snapshots."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from ggwork_pick.contracts import PickConditions
from ggwork_pick.repository import PickRepository

RULE_VERSION = "pick-rules-v1"
RANKING_VERSION = "evidence-date-v1"


def result_view(record: dict) -> dict:
    return {
        **{
            key: record[key] for key in ("id", "thread_id", "run_id", "catalog_batch_id", "knowledge_batch_id", "rule_version", "ranking_version", "created_at")
        },
        "conditions": record["conditions_json"],
        "items": record["ordered_items_json"],
    }


def filter_rows(rows, conditions: PickConditions, excluded: set[str]):
    matches = []
    for row in rows:
        if row["availability"] == "delisted" or row["identity"] in excluded:
            continue
        if conditions.theater and row["theater"].casefold() != conditions.theater.casefold():
            continue
        if conditions.language and row["language"].casefold() != conditions.language.casefold():
            continue
        if conditions.tags and not set(conditions.tags).issubset(set(row["tags"])):
            continue
        if conditions.query and conditions.query.casefold() not in (row["title"] + " " + " ".join(row["tags"])).casefold():
            continue
        if conditions.channel:
            permission = row["channel_rules"].get(conditions.channel, "unknown")
            if permission == "denied" or (conditions.confirmed_eligible_only and (permission != "allowed" or row["availability"] != "active")):
                continue
        matches.append(row)
    matches.sort(key=lambda row: row["identity"])
    # Date-only ordering is intentionally not a cross-source performance score.
    matches.sort(key=lambda row: max((s["observed_at"][:10] for s in row["signals"] if s["observed_at"]), default=""), reverse=True)
    return matches[: conditions.limit]


def candidate_item(row, conditions):
    item_id = uuid4().hex
    warnings = []
    if row["availability"] == "unknown":
        warnings.append("上下架状态待核实")
    if conditions.channel and row["channel_rules"].get(conditions.channel, "unknown") != "allowed":
        warnings.append("目标渠道规则待核实")
    if not row["signals"]:
        warnings.append("仅剧库收录，暂无榜单或指标依据")
    elif any(s["observed_at"] is None for s in row["signals"]):
        warnings.append("部分依据日期未知")
    return dict(
        item_id=item_id,
        identity=row["identity"],
        title=row["title"],
        theater=row["theater"],
        language=row["language"],
        availability=row["availability"],
        reason=f"符合本次筛选条件；有{len(row['signals'])}条来源信号。",
        warnings=warnings,
        evidence=[dict(citation_id=f"{item_id}:{i + 1}", **signal) for i, signal in enumerate(row["signals"])],
    )


class SelectionService:
    def __init__(self, repository: PickRepository):
        self.repository = repository

    async def query(
        self,
        filters: dict,
        *,
        thread_id: str,
        run_id: str,
        call_id: str,
        parent_result_id: str | None = None,
        use_latest: bool = False,
        pinned_versions: tuple[str | None, str | None] | None = None,
    ):
        parent = await self.repository.result(parent_result_id) if parent_result_id else None
        if parent is not None and parent["thread_id"] != thread_id:
            raise LookupError("引用结果不属于当前对话")
        effective = PickConditions.model_validate({**(parent["conditions_json"] if parent else {}), **filters})
        request_hash = hashlib.sha256(
            json.dumps(
                {"conditions": effective.model_dump(), "parent_result_id": parent_result_id, "use_latest": use_latest}, sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        old = await self.repository.result_for_call(run_id, call_id)
        if old is not None:
            if old["thread_id"] != thread_id or old["request_hash"] != request_hash:
                raise ValueError("重复工具调用的参数不同")
            return result_view(old)
        if parent is not None and not use_latest:
            if parent["rule_version"] != RULE_VERSION or parent["ranking_version"] != RANKING_VERSION:
                raise ValueError("历史规则版本仅供查看，重新选剧需明确使用最新规则")
            catalog_id, knowledge_id = parent["catalog_batch_id"], parent["knowledge_batch_id"]
        elif pinned_versions is not None:
            catalog_id, knowledge_id = pinned_versions
            if catalog_id is None:
                raise ValueError("本轮开始时尚未导入剧库，请导入后重新提问")
        else:
            catalog = await self.repository.current_batch("catalog")
            if catalog is None:
                raise ValueError("尚未导入剧库")
            knowledge = await self.repository.current_batch("knowledge")
            catalog_id, knowledge_id = catalog["id"], knowledge["id"] if knowledge else None
        excluded = {r["identity"] for r in await self.repository.selections()} if effective.exclude_selected else set()
        if effective.exclude_previous:
            if parent is None:
                raise ValueError("换一批需要明确引用上一份候选")
            excluded.update(item["identity"] for item in parent["ordered_items_json"])
        rows = await self.repository.catalog_rows(catalog_id)
        items = [candidate_item(row, effective) for row in filter_rows(rows, effective, excluded)]
        record = await self.repository.add_result(
            dict(
                id=uuid4().hex,
                thread_id=thread_id,
                run_id=run_id,
                tool_call_id=call_id,
                request_hash=request_hash,
                parent_result_id=parent_result_id,
                catalog_batch_id=catalog_id,
                knowledge_batch_id=knowledge_id,
                rule_version=parent["rule_version"] if parent and not use_latest else RULE_VERSION,
                ranking_version=parent["ranking_version"] if parent and not use_latest else RANKING_VERSION,
                conditions_json=effective.model_dump(),
                ordered_items_json=items,
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        return result_view(record)

    async def detail(self, result_id: str, item_id: str):
        record = await self.repository.result(result_id)
        for item in record["ordered_items_json"]:
            if item["item_id"] == item_id:
                return {"result_id": result_id, "catalog_batch_id": record["catalog_batch_id"], "item": item}
        raise LookupError("候选条目不存在")

    async def prepare(self, result_id: str, item_ids: list[str], note: str = ""):
        if len(note) > 2000:
            raise ValueError("备注最多2000字")
        record = await self.repository.result(result_id)
        items = [item for item in record["ordered_items_json"] if item["item_id"] in set(item_ids)]
        if not items or len(items) != len(set(item_ids)):
            raise ValueError("请指定这份候选中真实存在的条目")
        return {
            "result_id": result_id,
            "item_ids": [r["item_id"] for r in items],
            "titles": [r["title"] for r in items],
            "requires_confirmation": True,
            "note": note,
        }
