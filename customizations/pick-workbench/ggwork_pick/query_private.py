"""Imported/private catalogs retain their explicit provenance and candidate semantics."""

from collections import Counter

from ggwork_pick.completion_contracts import QueryPin, QueryResponse
from ggwork_pick.contracts import DramaInput, PickConditions
from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.selection import RULE_VERSION, matching_rows


def private_matches(rows, req, excluded):
    fields = {
        key: getattr(req, key) for key in ("theater", "language", "channel", "tags", "exclude_posted", "confirmed_eligible_only", "signal_kind", "hot_only")
    }
    # Search extends the candidate title/tag behavior to exact source identifiers.
    conditions = PickConditions(**fields, sort="rank" if req.order == "rank" else "evidence_date", posted_account=req.account)
    matches = matching_rows(rows, conditions, excluded, with_off=req.with_off)
    if req.language is not None:
        matches = [r for r in matches if r["language"].casefold() == req.language.casefold()]
    if req.signal_only:
        matches = [r for r in matches if r["signals"]]
    if req.dated_only:
        matches = [r for r in matches if any(s.get("observed_at") for s in r["signals"])]
    if req.youtube_ok:
        matches = [r for r in matches if r["channel_rules"].get("youtube") == "allowed"]
    if req.posted_filter:
        matches = [
            r
            for r in matches
            if (r.get("posted") or {}).get("matched") and (req.posted_filter == "pool" or ((r["posted"]["post_count"] > 0) == (req.posted_filter == "yes")))
        ]
    if req.source:
        matches = [r for r in matches if r["source"] == req.source]
    if req.source_id:
        matches = [r for r in matches if r["source_id"] == req.source_id]
    if req.query:
        query = req.query.casefold()
        matches = [r for r in matches if r["source_id"] == req.query or query in (r["title"] + " " + " ".join(r["tags"])).casefold()]
    if req.order == "title":
        matches.sort(key=lambda r: (r["title"], r["theater"], r["identity"]))
    if req.order == "listed":
        matches.sort(key=lambda r: r.get("listed_at") or "", reverse=True)
    return matches


async def query_private(repository, req, catalog_id, info, current):
    if req.domain not in {"candidates", "catalog"} or req.period.kind != "latest":
        raise QueryFailure("source_unavailable", "此版本没有完整镜像，无法查询历史榜单、规则或发布台账")
    if req.in_use_only or req.rank or req.grade or req.rs_locale or req.rs_bucket or req.rs_sort != "rr" or req.posted_state or req.legacy_week_label:
        raise QueryFailure("invalid_query", "此导入版本不支持该镜像筛选条件")
    if req.account or req.published_from or req.published_to:
        raise QueryFailure("source_unavailable", "此版本没有完整账号与时间范围的发布记录")
    if req.exclude_previous:
        raise QueryFailure("invalid_query", "换一批需要当前对话中已绑定的候选引用")
    pin = QueryPin(
        catalog_batch_id=catalog_id,
        knowledge_batch_id=req.pin.knowledge_batch_id if req.pin else current.knowledge_id,
        mirror_version=None,
        rule_version=RULE_VERSION,
    )
    if req.pin and req.pin.rule_version != RULE_VERSION:
        raise QueryFailure("version_conflict", "此导入版本的规则不一致")
    rows = await repository.catalog_rows(catalog_id)
    excluded = {r["identity"] for r in await repository.selections()} if req.exclude_selected else set()
    matches = private_matches(rows, req, excluded)
    page = matches[req.offset : req.offset + req.limit]
    facts = []
    for row in page:
        drama = DramaInput.model_validate({k: v for k, v in row.items() if k in DramaInput.model_fields})
        posted = row.get("posted")
        facts.append(
            {
                "identity": drama.identity,
                "drama": drama,
                "posted_status": "posted" if posted and posted["post_count"] else "unknown",
                "posted_scope_complete": False,
                "evidence_refs": [s.source_ref for s in drama.signals],
            }
        )
    next_offset = req.offset + len(page)
    return QueryResponse(
        request=req,
        pin=pin,
        actual_period=None,
        order_version="evidence-date-v1",
        counts={"total": len(rows), "matched": len(matches), "returned": len(page)},
        rows=facts,
        truncated=next_offset < len(matches),
        next_offset=next_offset if next_offset < len(matches) else None,
        facets={
            "platforms": dict(Counter(r["theater"] for r in private_matches(rows, req.model_copy(update={"theater": None}), excluded))),
            "languages": dict(Counter(r["language"] for r in private_matches(rows, req.model_copy(update={"language": None}), excluded))),
        },
        source_as_of=info["source_as_of"],
        mirror_synced_at=None,
    )
