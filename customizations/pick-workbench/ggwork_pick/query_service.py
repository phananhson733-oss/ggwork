"""Owner-scoped common query service for the board and Agent adapters."""

import asyncio
import base64
import json
from datetime import date, datetime

from ggwork_pick.completion_contracts import CommonQuery, QueryPin, QueryResponse
from ggwork_pick.contracts import DramaInput
from ggwork_pick.mirror.versions import check_schema_name
from ggwork_pick.query_catalog import catalog_page
from ggwork_pick.query_reader import QueryFailure

ORDER_VERSION = "mirror-board-v1"


def wire(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [wire(item) for item in value]
    return value


def rule_id(version):
    return f"mirror-rules-v{version}"


def query_rules(raw):
    return {
        **raw,
        "inUse": [v for v in raw["inUse"] if isinstance(v, str)],
        "ytBlocked": [k for k, v in raw["platformRules"].items() if v["yt"] == "no"],
        "ytListOnly": [k for k, v in raw["platformRules"].items() if v["yt"] == "only"],
    }


async def board_data(conn, keys, rules):
    data = {"row_keys": keys, "rules": rules}
    for key, table, order in [
        ("catalog_rows", "catalog_rows", "row_key"),
        ("rs_rows", "rs_rows", "row_key"),
        ("signals", "catalog_signals", "row_key,ord"),
    ]:
        data[key] = [wire(dict(r)) for r in await conn.fetch(f"SELECT * FROM {table} WHERE row_key=ANY($1::text[]) ORDER BY {order}", keys)]
    ids = [r["drama_id"] for r in data["rs_rows"]]
    data["posted"] = [
        wire(dict(r))
        for r in await conn.fetch(
            "SELECT * FROM catalog_posted WHERE row_keys && $1::text[] OR drama_ids && $2::text[] ORDER BY last_post_on DESC NULLS LAST,sd", keys, ids
        )
    ]
    data["accounts"] = [wire(dict(r)) for r in await conn.fetch("SELECT * FROM catalog_accounts ORDER BY grp,name,id")]
    return data


def fact_rows(board, imported, request):
    """Keep imported identity and evidence when present. Board-only records retain raw unknown language."""
    by_key = {}
    for row in imported:
        if row["source"] != "realshort-pick":
            by_key[row["source_id"]] = row
        if row["source"] == "realshort-pick":
            try:
                by_key[base64.urlsafe_b64decode(row["source_id"] + "=" * (-len(row["source_id"]) % 4)).decode()] = row
            except (ValueError, UnicodeError):
                continue
    raw = {r["row_key"]: r for r in [*board.get("catalog_rows", []), *board.get("rs_rows", [])]}
    output = []
    for key in board["row_keys"]:
        row = by_key.get(key)
        if row is None:
            source = raw.get(key)
            if source is None or not source["lang"]:
                continue
            row = {
                "source": "realshort-pick",
                "source_id": base64.urlsafe_b64encode(key.encode()).decode().rstrip("="),
                "title": source["title"],
                "language": source["lang"],
                "theater": source["platform"],
                "availability": "delisted" if source["off_on"] else "unknown",
            }
        drama = DramaInput.model_validate({k: v for k, v in row.items() if k in DramaInput.model_fields})
        posted = [p for p in board["posted"] if key in p["row_keys"] or key.removeprefix("reelshort-") in p["drama_ids"]]
        from ggwork_pick.query_posted import publication_truth

        status, complete = publication_truth(posted, request)
        output.append(
            {
                "identity": drama.identity,
                "drama": drama,
                "posted_status": status,
                "posted_scope_complete": complete,
                "evidence_refs": [s.source_ref for s in drama.signals],
            }
        )
    return output


class CommonQueryService:
    def __init__(self, repository, reader=None):
        self.repository, self.reader = repository, reader

    async def query(self, request: CommonQuery, *, deadline=None):
        loop = asyncio.get_running_loop()
        deadline = min(deadline if deadline is not None else float("inf"), loop.time() + request.budget_ms / 1000)
        try:
            async with asyncio.timeout_at(deadline):
                return await self._query(request, deadline=deadline)
        except QueryFailure:
            raise
        except TimeoutError:
            raise QueryFailure("query_timeout", "查询超过时限，请缩小范围后重试", retryable=True) from None
        except Exception as exc:
            code = getattr(exc, "sqlstate", None)
            if code == "57014":
                raise QueryFailure("query_timeout", "查询超过时限，请缩小范围后重试", retryable=True) from None
            if code in {"3F000", "42P01"}:
                raise QueryFailure("version_gone", "镜像版本已不可读取") from None
            if code or isinstance(exc, OSError):
                raise QueryFailure("source_unavailable", "查询数据暂时不可读取", retryable=True) from None
            raise

    async def _query(self, req, *, deadline):
        if req.published_from and req.published_to and req.published_from > req.published_to:
            raise QueryFailure("invalid_query", "发布起始日期不能晚于结束日期")
        if req.exclude_previous:
            raise QueryFailure("invalid_query", "换一批需要当前对话中已绑定的候选引用")
        if req.result_id:
            # Resolving a replay reference always crosses the owner boundary first.
            await self.repository.result(req.result_id)
            raise QueryFailure("invalid_query", "候选回放请使用原候选的回放入口")
        current = await self.repository.current_pin() if req.pin is None else None
        catalog_id = req.pin.catalog_batch_id if req.pin else current.catalog_id
        if catalog_id is None:
            raise QueryFailure("source_unavailable", "尚未导入剧库")
        info = await self.repository.batch_info(catalog_id)
        if info is None:
            raise QueryFailure("not_found", "查询版本不存在")
        if info["status"] != "published":
            raise QueryFailure("version_gone", "查询版本已过保留期")
        mirror_version = req.pin.mirror_version if req.pin else current.mirror_version
        if mirror_version is None:
            return await self._private(req, catalog_id, info, current)
        if self.reader is None:
            raise QueryFailure("source_unavailable", "公共查询只读连接尚未配置", retryable=True)
        if req.channel and req.channel != "youtube":
            raise QueryFailure("source_unavailable", "镜像尚未提供该渠道的完整规则与发布范围")
        async with self.reader.connection(deadline=deadline) as conn:
            version = await conn.fetchrow("SELECT * FROM pick_mirror.versions WHERE id=$1", mirror_version)
            if version is None or version["status"] != "published":
                raise QueryFailure("version_gone", "镜像版本已过保留期")
            knowledge_id = req.pin.knowledge_batch_id if req.pin else current.knowledge_id
            if (version["agent_catalog_batch_id"], version["agent_knowledge_batch_id"]) != (catalog_id, knowledge_id):
                raise QueryFailure("version_conflict", "剧库与镜像版本不一致")
            schema = check_schema_name(version["schema_name"])
            await conn.execute(f"SET LOCAL search_path = {schema},pick_mirror")
            meta = {r["key"]: r["value"] for r in await conn.fetch("SELECT key,value FROM meta")}
            rules = meta["rules"]
            pin = QueryPin(catalog_batch_id=catalog_id, knowledge_batch_id=knowledge_id, mirror_version=mirror_version, rule_version=rule_id(mirror_version))
            if req.pin and req.pin.rule_version != pin.rule_version:
                raise QueryFailure("version_conflict", "规则与镜像版本不一致")
            selected = await self.repository.selections() if req.exclude_selected else []
            excluded_keys = []
            for selection in selected:
                parts = json.loads(selection["identity"])
                if parts[0] == "realshort-pick":
                    try:
                        excluded_keys.append(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode())
                    except (ValueError, UnicodeError):
                        pass
            board = None
            if req.domain == "posted":
                from ggwork_pick.query_posted import posted_page

                board, total, matched, facets = await posted_page(conn, req, rules)
                keys = board["row_keys"]
            elif req.domain == "rules":
                keys, total, matched, facets = [], 0, 0, {}
            elif req.domain in {"catalog", "candidates"}:
                keys, total, matched, facets = await catalog_page(conn, req, query_rules(rules), excluded_keys=excluded_keys)
            else:
                raise QueryFailure("invalid_query", "该查询域尚未就绪")
            board = board if board is not None else await board_data(conn, keys, rules)
            board.update(
                rs_counts=meta.get("rsCounts"),
                growth_baseline={k: v for k, v in meta.get("growthBaseline", {}).items() if v is not None},
                sources=meta.get("sources", {}),
                posted_stats=meta.get("control", {}).get("postedStats"),
            )
            imported = await self.repository.catalog_rows(catalog_id)
            rows = fact_rows(board, imported, req)
            returned = len(keys)
            return QueryResponse(
                request=req,
                pin=pin,
                actual_period=None,
                order_version=ORDER_VERSION,
                counts={"total": total, "matched": matched, "returned": returned},
                truncated=req.offset + returned < matched,
                next_offset=req.offset + returned if req.offset + returned < matched else None,
                rows=rows,
                board=board,
                facets=facets,
                source_as_of=wire(version["as_of"]),
                mirror_synced_at=wire(version["published_at"]),
            )

    async def _private(self, req, catalog_id, info, current):
        from ggwork_pick.query_private import query_private

        return await query_private(self.repository, req, catalog_id, info, current)

    async def candidate_matches(self, rows, conditions, excluded, pin, *, deadline=None):
        """Legacy cards keep storage/notes; their read goes through the common domain engine."""
        from ggwork_pick.query_private import private_matches
        from ggwork_pick.selection import _check_references

        _check_references(rows, conditions)
        values = {k: getattr(conditions, k) for k in ("query", "theater", "language", "channel", "tags", "signal_kind", "confirmed_eligible_only", "hot_only")}
        values.update(
            domain="candidates",
            scope="candidate_pool",
            order=conditions.sort,
            exclude_posted=conditions.exclude_posted or bool(conditions.posted_account),
            account=conditions.posted_account,
            exclude_selected=False,
            exclude_previous=False,
            limit=200,
        )
        req = CommonQuery(**values)
        if pin.mirror_version is None or (conditions.signal_kind or "").startswith("obs_"):
            # No invented mirror for private imports; observation-specific evidence retains its explicit path.
            if conditions.posted_account:
                from ggwork_pick.selection import matching_rows

                return matching_rows(rows, conditions, excluded)
            return private_matches(rows, req, excluded)
        req = req.model_copy(
            update={
                "pin": QueryPin(
                    catalog_batch_id=pin.catalog_id,
                    knowledge_batch_id=pin.knowledge_id,
                    mirror_version=pin.mirror_version,
                    rule_version=rule_id(pin.mirror_version),
                )
            }
        )
        deadline = min(deadline if deadline is not None else float("inf"), asyncio.get_running_loop().time() + 10)
        found = []
        while True:
            response = await self.query(req, deadline=deadline)
            for fact in response.rows:
                row = fact.drama.model_dump(mode="json")
                row["identity"] = fact.identity
                from ggwork_pick.selection import _row_matches

                checked = conditions.model_copy(update={"query": None, "posted_account": None, "exclude_posted": False})
                if _row_matches(row, checked, excluded):
                    found.append(row)
            if response.next_offset is None:
                break
            req = req.model_copy(update={"offset": response.next_offset})
        return found
