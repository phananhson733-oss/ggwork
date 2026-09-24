"""Deterministic filtering and immutable candidate snapshots."""

import asyncio
import copy
import hashlib
import json
from uuid import uuid4

from ggwork_pick.contracts import PickConditions
from ggwork_pick.pin import Pin, as_pin
from ggwork_pick.repository import PickRepository, stamp

RULE_VERSION = "pick-rules-v1"
RANKING_VERSION = "evidence-date-v1"
RANK_RANKING_VERSION = "signal-rank-v1"
RANKING_VERSIONS = frozenset({RANKING_VERSION, RANK_RANKING_VERSION})
# The replay's ordered identity list stops here; total and truncated say how many there were (plan:1620).
REPLAY_LIMIT = 2000


class PostedDataUnavailable(ValueError):
    """The pinned catalog batch carries no publication records, so "not posted" cannot be checked."""


class ReplayGone(Exception):
    """The result's catalog batch no longer holds rows (pruned past retention): the replay answers 410."""


class ReplayUnrunnable(Exception):
    """This code cannot re-run the result's stored conditions any more: the replay answers 409."""


def ranking_version_for(conditions: PickConditions) -> str:
    return RANK_RANKING_VERSION if conditions.sort == "rank" else RANKING_VERSION


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
    """The newest signal of one kind, a ranked one first on the same day; ranks never mix kinds or sources."""
    signals = [s for s in row["signals"] if s["kind"] == kind]
    return max(signals, key=lambda s: (s["observed_at"] or "", s.get("rank") is not None, -(s.get("rank") or 0)), default=None)


def _check_references(rows, conditions: PickConditions) -> None:
    """A kind or account the batch does not contain is a typo, not a filter that silently matches nothing or everything."""
    if conditions.signal_kind and not any(s["kind"] == conditions.signal_kind for row in rows for s in row["signals"]):
        raise ValueError(f"剧库里没有 {conditions.signal_kind} 这类信号；信号种类代码见知识资料")
    if conditions.posted_account:
        if any(row.get("posted") is None for row in rows):
            raise PostedDataUnavailable("当前剧库批次没有发布记录，无法核对是否发过")
        wanted = conditions.posted_account.strip().casefold()
        if not any(account.casefold() == wanted for row in rows for account in row["posted"]["accounts"]):
            raise ValueError(f"发布记录里没有账号「{conditions.posted_account.strip()}」，请确认账号名")


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
    if conditions.filters_posted and _posted_excluded(row, conditions):
        return False
    return True


def matching_rows(rows, conditions: PickConditions, excluded: set[str]):
    if conditions.sort == "rank" and not conditions.signal_kind:
        raise ValueError("按名次排序必须指定 signal_kind（同一类榜单内才能比较名次）")
    _check_references(rows, conditions)
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


def unmappable_conditions(conditions: PickConditions) -> list[str]:
    """The result's conditions the data page has no filter for (plan 2.5 item 4), in a fixed order.

    query means title plus tags here and title or an exact key there; the exclusions come back through the replay
    list itself. confirmed_eligible_only filters only with a channel.
    """
    present = (
        ("tags", bool(conditions.tags)),
        ("posted_account", bool(conditions.posted_account)),
        ("channel", conditions.channel is not None),
        ("confirmed_eligible_only", conditions.channel is not None and conditions.confirmed_eligible_only),
        ("query", bool(conditions.query)),
        ("exclude_selected", conditions.exclude_selected),
        ("exclude_previous", conditions.exclude_previous),
    )
    return [name for name, active in present if active]


def replay_view(record: dict, rows) -> dict:
    """The stored result re-run on its own batch rows with the identities it excluded (plan 2.5 item 4).

    Rule or ranking versions other than this code's are flagged, not refused (U36). Raises ValidationError or
    ValueError when this code can no longer run the stored conditions. Pure and CPU-bound: run it off the loop.
    """
    conditions = PickConditions.model_validate(record["conditions_json"])
    excluded = record.get("excluded_json")
    identities = [row["identity"] for row in matching_rows(rows, conditions, frozenset(excluded or ()))]
    return {
        "result_id": record["id"],
        "catalog_batch_id": record["catalog_batch_id"],
        "knowledge_batch_id": record["knowledge_batch_id"],
        # Null where the result had no paired version, and always on SQLite, which has no pick_mirror (U35).
        "mirror_version": record.get("mirror_version"),
        "limit": conditions.limit,
        "total": len(identities),
        "identities": identities[:REPLAY_LIMIT],
        "truncated": len(identities) > REPLAY_LIMIT,
        "shown": identities[: conditions.limit],
        # Results from before P2 recorded no exclusions: exclude_selected and 换一批 cannot be redone for them.
        "excluded_reproducible": excluded is not None,
        "ranking_reproducible": record["rule_version"] == RULE_VERSION and record["ranking_version"] == ranking_version_for(conditions),
        "unmappable": unmappable_conditions(conditions),
    }


def _posted_warnings(row, conditions) -> list[str]:
    posted = row.get("posted")
    if posted is None:
        return []
    warnings = []
    if posted["post_count"] == 0 and posted["sched_count"] > 0:
        warnings.append(f"发布记录显示已排期未发（{posted['sched_count']}条待公开）")
    if conditions.filters_posted and not posted["matched"]:
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
        # Rows are shared with the batch cache; an item must not alias any of their lists.
        item["posted"] = copy.deepcopy(row["posted"])
    if matched_total is not None:
        item["matched_total"] = matched_total
    return item


def _request_hash(conditions: PickConditions, parent_result_id: str | None, use_latest: bool) -> str:
    """What a repeated tool call must repeat to get the first call's result back."""
    request = {"conditions": conditions.model_dump(), "parent_result_id": parent_result_id, "use_latest": use_latest}
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SelectionService:
    def __init__(self, repository: PickRepository):
        self.repository = repository

    async def _parent(self, parent_result_id: str | None, thread_id: str) -> dict | None:
        if not parent_result_id:
            return None
        parent = await self.repository.result(parent_result_id)
        if parent["thread_id"] != thread_id:
            raise LookupError("引用结果不属于当前对话")
        return parent

    async def _parent_versions(self, parent: dict) -> Pin:
        """换一批 stays on the parent's data: its batches, its mirror version and the data_as_of it froze (None before P2)."""
        if parent["rule_version"] != RULE_VERSION or parent["ranking_version"] not in RANKING_VERSIONS:
            raise ValueError("历史规则版本仅供查看，重新选剧需明确使用最新规则")
        info = await self.repository.batch_info(parent["catalog_batch_id"])
        if info is None or info["status"] != "published":
            raise ValueError("这份候选用的数据版本已过保留期被清理，不能在它上面换一批；请直接重新查询")
        return Pin(parent["catalog_batch_id"], parent["knowledge_batch_id"], parent.get("mirror_version"), parent.get("data_as_of_json"))

    async def _current_versions(self, pinned_versions) -> Pin:
        """The run's pin when the tools pass one; otherwise one read of the current data (repository.current_pin)."""
        if pinned_versions is not None:
            pin = as_pin(pinned_versions)
            if pin.catalog_id is None:
                raise ValueError("本轮开始时尚未导入剧库，请导入后重新提问")
            return pin
        pin = await self.repository.current_pin()
        if pin.catalog_id is None:
            raise ValueError("尚未导入剧库")
        return pin

    async def _scope(self, filters: dict, parent: dict | None, *, use_latest: bool, pinned_versions):
        """Conditions, data versions and exclusions for one call.

        Only 换一批 (exclude_previous) derives from the bound parent: its conditions, its data version
        and its items. Any other question stands on its own conditions and this run's data.
        """
        derived = parent is not None and filters.get("exclude_previous") is True
        conditions = PickConditions.model_validate({**parent["conditions_json"], **filters} if derived else filters)
        if conditions.exclude_previous and not derived:
            raise ValueError("换一批需要明确引用上一份候选")
        pin = await self._parent_versions(parent) if derived and not use_latest else await self._current_versions(pinned_versions)
        selected = {r["identity"] for r in await self.repository.selections()} if conditions.exclude_selected else set()
        previous = {item["identity"] for item in parent["ordered_items_json"]} if derived else set()
        return conditions, pin, frozenset(selected | previous)

    async def query(
        self,
        filters: dict,
        *,
        thread_id: str,
        run_id: str,
        call_id: str,
        parent_result_id: str | None = None,
        use_latest: bool = False,
        pinned_versions: Pin | tuple[str | None, str | None] | None = None,
    ) -> dict:
        """The result view alone; query_with_record also hands back the stored row."""
        view, _ = await self.query_with_record(
            filters,
            thread_id=thread_id,
            run_id=run_id,
            call_id=call_id,
            parent_result_id=parent_result_id,
            use_latest=use_latest,
            pinned_versions=pinned_versions,
        )
        return view

    async def query_with_record(
        self,
        filters: dict,
        *,
        thread_id: str,
        run_id: str,
        call_id: str,
        parent_result_id: str | None = None,
        use_latest: bool = False,
        pinned_versions: Pin | tuple[str | None, str | None] | None = None,
    ) -> tuple[dict, dict]:
        """(result view, stored row). The view cannot carry the frozen data_as_of: the frontend's result schema is
        strict (U51). A repeated call returns the row the first call wrote, so the tool answers with what it froze.
        """
        parent = await self._parent(parent_result_id, thread_id)
        effective, pin, excluded = await self._scope(filters, parent, use_latest=use_latest, pinned_versions=pinned_versions)
        request_hash = _request_hash(effective, parent_result_id, use_latest)
        old = await self.repository.result_for_call(run_id, call_id)
        if old is not None:
            if old["thread_id"] != thread_id or old["request_hash"] != request_hash:
                raise ValueError("重复工具调用的参数不同")
            return result_view(old), old
        rows = await self.repository.catalog_rows(pin.catalog_id)
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
                catalog_batch_id=pin.catalog_id,
                knowledge_batch_id=pin.knowledge_id,
                rule_version=RULE_VERSION,
                ranking_version=ranking_version_for(effective),
                conditions_json=effective.model_dump(),
                ordered_items_json=items,
                created_at=stamp(),
                # Frozen with the result (P2-5b): what it left out, for replay, and the data it stood on.
                excluded_json=sorted(excluded),
                mirror_version=pin.mirror_version,
                data_as_of_json=pin.data_as_of,
            )
        )
        return result_view(record), record

    async def count(self, filters: dict, *, parent: dict | None = None, pinned_versions=None) -> dict:
        """Aggregate the same filter as a query, without persisting a candidate snapshot.

        data_as_of is the pin's: a derived count's pin is the parent's frozen value, and a pin without one (a parent
        from before P2, or pinned_versions given as a bare pair) falls back to the catalog batch.
        """
        conditions, pin, excluded = await self._scope(filters, parent, use_latest=False, pinned_versions=pinned_versions)
        matches = matching_rows(await self.repository.catalog_rows(pin.catalog_id), conditions, excluded)
        by_theater, by_language = {}, {}
        for row in matches:
            by_theater[row["theater"]] = by_theater.get(row["theater"], 0) + 1
            by_language[row["language"]] = by_language.get(row["language"], 0) + 1
        return {
            "catalog_batch_id": pin.catalog_id,
            "conditions": conditions.model_dump(),
            "total": len(matches),
            "by_theater": dict(sorted(by_theater.items(), key=lambda kv: (-kv[1], kv[0]))),
            "by_language": dict(sorted(by_language.items(), key=lambda kv: (-kv[1], kv[0]))),
            "data_as_of": pin.data_as_of if pin.data_as_of is not None else await self.repository.data_as_of(pin.catalog_id),
        }

    async def replay(self, result_id: str) -> dict:
        """GET /api/pick/replay: the owner's result re-run on its own batch, with the data_as_of it froze.

        LookupError when the owner has no such result; ReplayGone when its batch is no longer published (pruned);
        ReplayUnrunnable when this code cannot run the stored conditions any more. Any other error propagates.
        """
        record = await self.repository.result(result_id)
        try:
            # catalog_rows reads only a readable, published batch (its _require_batch): a pruned one raises here.
            rows = await self.repository.catalog_rows(record["catalog_batch_id"])
        except LookupError:
            raise ReplayGone("这份候选用的剧库批次已过保留期被清理，无法回放") from None
        try:
            view = await asyncio.to_thread(replay_view, record, rows)
        except ValueError:
            # Pydantic's ValidationError included; only the re-run's refusals are a 409, not a ValueError anywhere.
            raise ReplayUnrunnable("这份候选的条件已不能按当前规则重跑，无法回放") from None
        return {**view, "data_as_of": await self.repository.frozen_data_as_of(record)}

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
