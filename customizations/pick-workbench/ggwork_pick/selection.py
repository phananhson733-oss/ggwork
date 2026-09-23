"""Deterministic filtering and immutable candidate snapshots."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from ggwork_pick.contracts import PickConditions
from ggwork_pick.repository import PickRepository

RULE_VERSION = "pick-rules-v1"
RANKING_VERSION = "evidence-date-v1"
RANK_RANKING_VERSION = "signal-rank-v1"
RANKING_VERSIONS = frozenset({RANKING_VERSION, RANK_RANKING_VERSION})


class PostedDataUnavailable(ValueError):
    """The pinned catalog batch carries no publication records, so "not posted" cannot be checked."""


def _matched_total(record: dict) -> int | None:
    items = record["ordered_items_json"]
    if not items:
        # limit >= 1, so an empty snapshot means nothing matched.
        return 0
    return items[0].get("matched_total")


def result_view(record: dict) -> dict:
    return {
        **{
            key: record[key] for key in ("id", "thread_id", "run_id", "catalog_batch_id", "knowledge_batch_id", "rule_version", "ranking_version", "created_at")
        },
        "conditions": record["conditions_json"],
        "items": record["ordered_items_json"],
        # Each item carries the pre-limit match count; non-empty snapshots from before it existed report None.
        "matched_total": _matched_total(record),
    }


def _latest_date(row) -> str:
    return max((s["observed_at"][:10] for s in row["signals"] if s["observed_at"]), default="")


def _kind_signal(row, kind):
    """The newest signal of one kind; rank comparisons never mix kinds or sources."""
    signals = [s for s in row["signals"] if s["kind"] == kind]
    return max(signals, key=lambda s: (s["observed_at"] or "", -(s.get("rank") or 0)), default=None)


def _posted_excluded(row, conditions: PickConditions) -> bool:
    posted = row.get("posted")
    if posted is None:
        raise PostedDataUnavailable("当前剧库批次没有发布记录，无法核对是否发过")
    if conditions.exclude_posted and posted["post_count"] > 0:
        return True
    if conditions.posted_account:
        wanted = conditions.posted_account.strip().casefold()
        return any(account.casefold() == wanted for account in posted["accounts"])
    return False


def _row_matches(row, conditions: PickConditions, excluded: set[str]) -> bool:
    if row["availability"] == "delisted" or row["identity"] in excluded:
        return False
    if conditions.theater and row["theater"].casefold() != conditions.theater.casefold():
        return False
    if conditions.language and row["language"].casefold() != conditions.language.casefold():
        return False
    if conditions.tags and not set(conditions.tags).issubset(set(row["tags"])):
        return False
    if conditions.query and conditions.query.casefold() not in (row["title"] + " " + " ".join(row["tags"])).casefold():
        return False
    if conditions.signal_kind and _kind_signal(row, conditions.signal_kind) is None:
        return False
    if conditions.channel:
        permission = row["channel_rules"].get(conditions.channel, "unknown")
        if permission == "denied" or (conditions.confirmed_eligible_only and (permission != "allowed" or row["availability"] != "active")):
            return False
    if (conditions.exclude_posted or conditions.posted_account) and _posted_excluded(row, conditions):
        return False
    return True


def matching_rows(rows, conditions: PickConditions, excluded: set[str]):
    if conditions.sort == "rank" and not conditions.signal_kind:
        raise ValueError("按名次排序必须指定 signal_kind（同一类榜单内才能比较名次）")
    matches = [row for row in rows if _row_matches(row, conditions, excluded)]
    if conditions.sort == "rank":
        # One board at a time, like RealShort's rank tab: ranks from different days are not comparable.
        kind_signals = [s for row in rows for s in row["signals"] if s["kind"] == conditions.signal_kind]
        if kind_signals and all(s.get("rank") is None for s in kind_signals):
            raise ValueError(f"{conditions.signal_kind} 这类信号没有名次，不能按名次排序；去掉 sort 只按这类依据筛选")
        board = max((s["observed_at"] for s in kind_signals if s["observed_at"]), default=None)
        if board is not None:
            matches = [row for row in matches if _kind_signal(row, conditions.signal_kind)["observed_at"] == board]
    matches.sort(key=lambda row: row["identity"])
    if conditions.sort == "rank":

        def rank_key(row):
            rank = _kind_signal(row, conditions.signal_kind).get("rank")
            return (rank is None, rank if rank is not None else 0)

        matches.sort(key=rank_key)
    else:
        # Date-only ordering is intentionally not a cross-source performance score.
        matches.sort(key=_latest_date, reverse=True)
    return matches


def filter_rows(rows, conditions: PickConditions, excluded: set[str]):
    return matching_rows(rows, conditions, excluded)[: conditions.limit]


def _posted_warnings(row, conditions) -> list[str]:
    posted = row.get("posted")
    if posted is None:
        return []
    warnings = []
    if posted["post_count"] == 0 and posted["sched_count"] > 0:
        warnings.append(f"发布记录显示已排期未发（{posted['sched_count']}条待公开）")
    if (conditions.exclude_posted or conditions.posted_account) and not posted["matched"]:
        warnings.append("发布记录未对上这部剧；对不上不代表从未发布")
    return warnings


def candidate_item(row, conditions, matched_total: int | None = None):
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
    warnings.extend(_posted_warnings(row, conditions))
    reason = f"符合本次筛选条件；有{len(row['signals'])}条来源信号。"
    if conditions.sort == "rank" and conditions.signal_kind:
        signal = _kind_signal(row, conditions.signal_kind)
        if signal and signal.get("rank") is not None:
            reason = f"{signal.get('label') or signal['kind']}第{signal['rank']}名（{signal['observed_at'] or '日期未知'}）；" + reason
    item = dict(
        item_id=item_id,
        identity=row["identity"],
        title=row["title"],
        theater=row["theater"],
        language=row["language"],
        availability=row["availability"],
        reason=reason,
        warnings=warnings,
        evidence=[dict(citation_id=f"{item_id}:{i + 1}", **signal) for i, signal in enumerate(row["signals"])],
    )
    if row.get("detail_url"):
        item["detail_url"] = row["detail_url"]
    if row.get("posted") is not None:
        item["posted"] = row["posted"]
    if matched_total is not None:
        item["matched_total"] = matched_total
    return item


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
            if parent["rule_version"] != RULE_VERSION or parent["ranking_version"] not in RANKING_VERSIONS:
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
        matches = matching_rows(rows, effective, excluded)
        items = [candidate_item(row, effective, len(matches)) for row in matches[: effective.limit]]
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
                ranking_version=RANK_RANKING_VERSION if effective.sort == "rank" else RANKING_VERSION,
                conditions_json=effective.model_dump(),
                ordered_items_json=items,
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        return result_view(record)

    async def count(self, filters: dict, *, catalog_id: str | None = None) -> dict:
        """Aggregate the same filter without persisting a candidate snapshot."""
        conditions = PickConditions.model_validate(filters)
        if catalog_id is None:
            catalog = await self.repository.current_batch("catalog")
            if catalog is None:
                raise ValueError("尚未导入剧库")
            catalog_id = catalog["id"]
        excluded = {r["identity"] for r in await self.repository.selections()} if conditions.exclude_selected else set()
        matches = matching_rows(await self.repository.catalog_rows(catalog_id), conditions, excluded)
        by_theater, by_language = {}, {}
        for row in matches:
            by_theater[row["theater"]] = by_theater.get(row["theater"], 0) + 1
            by_language[row["language"]] = by_language.get(row["language"], 0) + 1
        return {
            "catalog_batch_id": catalog_id,
            "conditions": conditions.model_dump(),
            "total": len(matches),
            "by_theater": dict(sorted(by_theater.items(), key=lambda kv: (-kv[1], kv[0]))),
            "by_language": dict(sorted(by_language.items(), key=lambda kv: (-kv[1], kv[0]))),
        }

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
