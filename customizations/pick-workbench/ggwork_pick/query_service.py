"""Owner-scoped common query service for the board and Agent adapters."""

import asyncio
import base64
import json
from datetime import date, datetime

from pydantic import ValidationError

from ggwork_pick.completion_contracts import CommonQuery, QueryPin, QueryResponse
from ggwork_pick.contracts import DramaInput
from ggwork_pick.mirror.contracts import Rules
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


def canonical_rows(imported):
    """One pinned full identity per raw key; overlapping namespaces cannot overwrite facts."""
    by_key = {}
    for row in imported:
        key = row["source_id"]
        if row["source"] == "realshort-pick":
            try:
                key = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode()
                if base64.urlsafe_b64encode(key.encode()).decode().rstrip("=") != row["source_id"]:
                    raise ValueError
            except (ValueError, UnicodeError):
                raise QueryFailure("source_unavailable", "剧库来源标识无法与镜像核对") from None
        if key in by_key:
            raise QueryFailure("source_unavailable", "剧库来源标识存在歧义，无法核对")
        by_key[key] = row
    return by_key


def fact_rows(board, imported, request, *, actual_period=None):
    """Canonical facts never override a raw explicit delisting or platform denial."""
    by_key = canonical_rows(imported)
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
        values = {k: v for k, v in row.items() if k in DramaInput.model_fields}
        source = raw.get(key)
        if source is not None:
            if source["off_on"]:
                values["availability"] = "delisted"
            if board["rules"]["platformRules"].get(source["platform"], {}).get("yt") == "no":
                values["channel_rules"] = {**values.get("channel_rules", {}), "youtube": "denied"}
        drama = DramaInput.model_validate(values)
        for ranked in board.get("rank_rows", []):
            if ranked["row_key"] != key:
                continue
            signal = ranked["signal"]
            prior = next((entry for entry in drama.signals if entry.kind == signal["kind"]), None)
            facts = {
                "kind": signal["kind"],
                "source_ref": prior.source_ref if prior else f"mirror:{key}:{signal['kind']}:{signal['ord']}",
                "observed_at": actual_period.value if actual_period and actual_period.value else signal["evidence_on"],
                "rank": ranked["day_rank"] if ranked["day_rank"] is not None else signal["rank"],
                "grade": signal["grade"],
                "note": ranked["day_note"] or signal["note"],
                "label": prior.label if prior else signal["kind"],
            }
            from ggwork_pick.contracts import SignalInput

            drama = drama.model_copy(update={"signals": [entry for entry in drama.signals if entry.kind != signal["kind"]] + [SignalInput(**facts)]})
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

    async def query(self, request: CommonQuery, *, deadline=None, excluded_identities=frozenset()):
        loop = asyncio.get_running_loop()
        deadline = min(deadline if deadline is not None else float("inf"), loop.time() + min(10000, request.budget_ms) / 1000)
        try:
            if loop.time() >= deadline:
                raise TimeoutError
            async with asyncio.timeout_at(deadline):
                result = await self._query(request, deadline=deadline, excluded_identities=excluded_identities)
                if loop.time() >= deadline:
                    raise TimeoutError
                return result
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

    async def _query(self, req, *, deadline, excluded_identities):
        self._validate_domain(req)
        if req.published_from and req.published_to and req.published_from > req.published_to:
            raise QueryFailure("invalid_query", "发布起始日期不能晚于结束日期")
        if req.exclude_previous:
            raise QueryFailure("invalid_query", "换一批需要当前对话中已绑定的候选引用")
        if req.result_id:
            # Resolving a replay reference always crosses the owner boundary first.
            await self.repository.result(req.result_id)
            raise QueryFailure("invalid_query", "候选回放请使用原候选的回放入口")
        current = await self.repository.current_pin()
        catalog_id = req.pin.catalog_batch_id if req.pin else current.catalog_id
        if catalog_id is None:
            raise QueryFailure("source_unavailable", "尚未导入剧库")
        info = await self.repository.batch_info(catalog_id)
        if info is None:
            raise QueryFailure("not_found", "查询版本不存在")
        if info["status"] != "published":
            raise QueryFailure("version_gone", "查询版本已过保留期")
        if req.pin and req.pin.feedback_version_id:
            raise QueryFailure("invalid_query", "公共镜像查询不接受运营反馈版本")
        knowledge_id = req.pin.knowledge_batch_id if req.pin else current.knowledge_id
        if knowledge_id is not None:
            await self.repository.knowledge_documents(knowledge_id)
        mirror_version = req.pin.mirror_version if req.pin else current.mirror_version
        if mirror_version is None:
            if req.pin and info["shared"]:
                if current.catalog_id == catalog_id and current.mirror_version is not None:
                    raise QueryFailure("version_conflict", "此剧库批次必须保留原镜像版本")
                if self.reader is not None:
                    async with self.reader.connection(deadline=deadline) as conn:
                        paired = await conn.fetchval(
                            "SELECT id FROM pick_mirror.versions WHERE agent_catalog_batch_id=$1 ORDER BY id DESC LIMIT 1",
                            catalog_id,
                        )
                    if paired is not None:
                        raise QueryFailure("version_conflict", "此剧库批次必须保留原镜像版本")
                elif current.catalog_id != catalog_id:
                    raise QueryFailure("source_unavailable", "无法核对历史镜像版本", retryable=True)
            return await self._private(req, catalog_id, info, current)
        if self.reader is None:
            raise QueryFailure("source_unavailable", "公共查询只读连接尚未配置", retryable=True)
        if req.channel and (req.channel != "youtube" or req.exclude_posted or req.account or req.published_from or req.published_to):
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
            try:
                rules = Rules.model_validate(meta.get("rules")).model_dump(mode="json", by_alias=True, exclude_unset=True)
            except ValidationError:
                raise QueryFailure("source_unavailable", "镜像规则暂时不可读取", retryable=True) from None
            pin = QueryPin(catalog_batch_id=catalog_id, knowledge_batch_id=knowledge_id, mirror_version=mirror_version, rule_version=rule_id(mirror_version))
            if req.pin and req.pin.rule_version != pin.rule_version:
                raise QueryFailure("version_conflict", "规则与镜像版本不一致")
            effective_request = req
            if req.domain in {"catalog", "candidates"}:
                updates = {}
                if req.theater:
                    updates["theater"] = next(
                        (key for key, value in rules["platformRules"].items() if req.theater.casefold() in {key.casefold(), str(value["name"]).casefold()}),
                        req.theater,
                    )
                if req.language and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM catalog_rows WHERE lang=$1 UNION ALL SELECT 1 FROM rs_rows WHERE lang=$1)", req.language
                ):
                    updates["language"] = next((key for key, value in rules["langLoc"].items() if value == req.language), req.language)
                effective_request = req.model_copy(update=updates)
            imported = await self.repository.catalog_rows(catalog_id)
            by_key = canonical_rows(imported)
            raw_identity_rows = await conn.fetch("SELECT row_key,lang FROM catalog_rows UNION ALL SELECT row_key,lang FROM rs_rows")
            raw_keys = [r["row_key"] for r in raw_identity_rows]
            if len(raw_keys) != len(set(raw_keys)):
                raise QueryFailure("source_unavailable", "镜像来源标识存在歧义，无法核对")
            selected = await self.repository.selections() if req.exclude_selected else []
            excluded = set(excluded_identities) | {s["identity"] for s in selected}
            excluded_keys = []
            canonical = {"sources": {}, "eligible": [], "denied": [], "delisted": []}
            for raw_row in raw_identity_rows:
                key = raw_row["row_key"]
                row = by_key.get(key)
                source = row["source"] if row else "realshort-pick"
                canonical["sources"].setdefault(source, []).append(key)
                if row is None:
                    # fact_rows exposes precisely this fallback identity for known-language
                    # raw records. Missing canonical facts never grant eligibility.
                    if raw_row["lang"]:
                        identity = json.dumps(
                            ["realshort-pick", base64.urlsafe_b64encode(key.encode()).decode().rstrip("="), raw_row["lang"].strip()],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        if identity in excluded:
                            excluded_keys.append(key)
                    continue
                if row["identity"] in excluded:
                    excluded_keys.append(key)
                permission = row["channel_rules"].get("youtube", "unknown")
                if row["availability"] == "active" and permission == "allowed":
                    canonical["eligible"].append(key)
                if permission == "denied":
                    canonical["denied"].append(key)
                if row["availability"] == "delisted":
                    canonical["delisted"].append(key)
            if req.published_from or req.published_to:
                from ggwork_pick.query_posted import validate_publication_window

                await validate_publication_window(conn, req)
            board = None
            rank_result = None
            if req.domain == "rankings":
                from ggwork_pick.query_rank import query_rank

                self._validate_rank(req)
                try:
                    rank_result = await query_rank(conn, req, rules=query_rules(rules), meta={**meta, "as_of": wire(version["as_of"])}, deadline=deadline)
                except ValueError:
                    raise QueryFailure("invalid_query", "榜单条件不符合来源约定") from None
                if rank_result.actual_period is None:
                    raise QueryFailure("period_missing", "这张榜单没有可读取的期次")
                keys, total, matched, facets = rank_result.row_keys, rank_result.total, rank_result.matched, rank_result.facets
            elif req.domain == "posted":
                from ggwork_pick.query_posted import posted_page

                board, total, matched, facets = await posted_page(conn, req, rules)
                keys = board["row_keys"]
            elif req.domain == "rules":
                keys, total, matched, facets = [], 0, 0, {}
            elif req.domain in {"catalog", "candidates"}:
                keys, total, matched, facets = await catalog_page(conn, effective_request, query_rules(rules), excluded_keys=excluded_keys, canonical=canonical)
            else:
                raise QueryFailure("invalid_query", "该查询域尚未就绪")
            board = board if board is not None else await board_data(conn, keys, rules)
            if rank_result is not None:
                for field in ("rank_rows", "bill_rows", "bill_totals", "effective_sort", "legacy_total", "rank_limit"):
                    board[field] = wire(getattr(rank_result, field))
            board.update(
                rs_counts=meta.get("rsCounts"),
                growth_baseline={k: v for k, v in meta.get("growthBaseline", {}).items() if v is not None},
                sources=meta.get("sources", {}),
                posted_stats=meta.get("control", {}).get("postedStats"),
            )
            rows = fact_rows(board, imported, req, actual_period=rank_result.actual_period if rank_result else None)
            returned = len(rank_result.bill_rows) if rank_result is not None and req.rank == "rs_ledger" else len(keys)
            next_offset = req.offset + returned if req.offset + returned < matched else None
            if rank_result is not None and not rank_result.has_more:
                next_offset = None
            return QueryResponse(
                request=req,
                pin=pin,
                actual_period=rank_result.actual_period if rank_result else None,
                period_options=rank_result.period_options if rank_result else None,
                order_version=ORDER_VERSION,
                counts={"total": total, "matched": matched, "returned": returned},
                truncated=req.offset + returned < matched,
                next_offset=next_offset,
                rows=rows,
                board=board,
                facets=facets,
                source_as_of=wire(version["as_of"]),
                mirror_synced_at=wire(version["published_at"]),
            )

    @staticmethod
    def _validate_domain(req):
        rank_fields = req.rank or req.grade or req.rs_locale or req.rs_bucket or req.rs_sort != "rr" or req.legacy_week_label
        if req.domain in {"catalog", "candidates"} and (
            rank_fields or req.period.kind != "latest" or req.posted_state or req.order in {"rank", "published_at"}
        ):
            raise QueryFailure("invalid_query", "请使用榜单查询域指定期次和名次，或使用剧库支持的排序")
        if req.domain == "posted":
            if req.channel:
                raise QueryFailure("source_unavailable", "发布台账缺少完整的渠道范围")
            if (
                req.scope != "full_catalog"
                or rank_fields
                or req.period.kind != "latest"
                or req.source
                or req.exclude_posted
                or req.exclude_selected
                or req.tags
                or req.signal_kind
                or req.hot_only
                or req.posted_filter
                or req.signal_only
                or req.youtube_ok
                or req.dated_only
                or req.in_use_only
                or req.order not in {"evidence_date", "published_at"}
            ):
                raise QueryFailure("invalid_query", "发布台账仅支持搜索、账号、日期、剧场、语种和发布状态条件")
        if req.domain == "rules":
            defaults = CommonQuery(domain="rules", scope=req.scope)
            supported = {"domain", "scope", "pin", "budget_ms", "limit", "offset"}
            if any(getattr(req, key) != getattr(defaults, key) for key in CommonQuery.model_fields if key not in supported):
                raise QueryFailure("invalid_query", "规则查询不支持剧集筛选条件")

    @staticmethod
    def _validate_rank(req):
        unsupported = (
            req.source
            or req.source_id
            or req.language is not None
            or req.theater
            or req.channel
            or req.account
            or req.published_from
            or req.published_to
            or req.exclude_posted
            or req.exclude_selected
            or req.tags
            or req.hot_only
            or req.posted_filter
            or req.posted_state
            or req.signal_only
            or req.youtube_ok
            or req.dated_only
            or req.in_use_only
        )
        if unsupported or req.scope != "full_catalog":
            raise QueryFailure("invalid_query", "榜单仅支持来源定义的期次、等级、搜索、语种和上架时长条件；请用完整剧库范围")
        kind = req.rank or req.signal_kind or "kd"
        if req.period.kind == "daily" and kind not in {"kd", "qc", "qr"} or req.period.kind == "weekly" and kind != "kw":
            raise QueryFailure("invalid_query", "期次类型与榜单不一致")
        if not kind.startswith("rs_") and (req.query or req.rs_locale or req.rs_bucket or req.rs_sort != "rr"):
            raise QueryFailure("invalid_query", "剧场榜单不支持 ReelShort 的搜索和指标条件")
        if kind == "rs_ledger" and (req.query or req.rs_locale or req.rs_bucket or req.grade or req.rs_sort != "rr"):
            raise QueryFailure("invalid_query", "订单台账不支持该筛选条件")

    async def _private(self, req, catalog_id, info, current):
        from ggwork_pick.query_private import query_private

        try:
            return await query_private(self.repository, req, catalog_id, info, current)
        except QueryFailure:
            raise
        except ValueError as exc:
            raise QueryFailure("invalid_query", str(exc)) from None

    async def candidate_matches(self, rows, conditions, excluded, pin, *, deadline=None):
        """Legacy cards keep storage/notes; their read goes through the common domain engine."""
        loop = asyncio.get_running_loop()
        deadline = min(deadline if deadline is not None else float("inf"), loop.time() + 10)
        try:
            if loop.time() >= deadline:
                raise TimeoutError
            async with asyncio.timeout_at(deadline):
                result = await self._candidate_matches(rows, conditions, excluded, pin, deadline=deadline)
                if loop.time() >= deadline:
                    raise TimeoutError
                return result
        except TimeoutError:
            raise QueryFailure("query_timeout", "查询超过时限，请缩小范围后重试", retryable=True) from None

    async def _candidate_matches(self, rows, conditions, excluded, pin, *, deadline):
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
        if conditions.sort == "rank":
            signals = [s for row in rows for s in row["signals"] if s["kind"] == conditions.signal_kind]
            if conditions.signal_kind not in {"kd", "qc", "qr"} and all(s.get("rank") is None for s in signals):
                raise ValueError("这类信号没有名次，不能按名次排序；请使用榜单查询查看来源等级或周次")
            req = CommonQuery(domain="rankings", scope="full_catalog", rank=conditions.signal_kind, order="rank", pin=req.pin, limit=200)
        found = []
        while True:
            response = await self.query(req, deadline=deadline, excluded_identities=excluded)
            for fact in response.rows:
                row = fact.drama.model_dump(mode="json")
                row["identity"] = fact.identity
                from ggwork_pick.selection import _row_matches

                checked = (
                    conditions.model_copy(update={"query": None})
                    if conditions.sort == "rank"
                    else conditions.model_copy(update={"query": None, "posted_account": None, "exclude_posted": False})
                )
                if conditions.filters_posted and response.board is not None:
                    from ggwork_pick.query_posted import publication_truth

                    key = row["source_id"]
                    if row["source"] == "realshort-pick":
                        key = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode()
                    records = [
                        p.model_dump(mode="json", exclude_unset=True)
                        for p in response.board.posted
                        if key in p.row_keys or key.removeprefix("reelshort-") in p.drama_ids
                    ]
                    scope = CommonQuery(domain="catalog", scope="full_catalog", account=conditions.posted_account)
                    status, complete = publication_truth(records, scope)
                    if status == "posted":
                        continue
                    row["_common_posted_unknown"] = status == "unknown" or not complete
                    checked = checked.model_copy(update={"posted_account": None, "exclude_posted": False})
                if conditions.sort == "rank" and conditions.query:
                    query = conditions.query.casefold()
                    if row["source_id"] != conditions.query and query not in (row["title"] + " " + " ".join(row["tags"])).casefold():
                        continue
                if _row_matches(row, checked, excluded):
                    found.append(row)
            if response.next_offset is None:
                break
            req = req.model_copy(update={"offset": response.next_offset})
        if conditions.sort == "rank" and conditions.signal_kind not in {"kd", "qc", "qr"}:
            newest = max((s["observed_at"] for row in rows for s in row["signals"] if s["kind"] == conditions.signal_kind and s["observed_at"]), default=None)

            def ranked_signal(row):
                return next(s for s in row["signals"] if s["kind"] == conditions.signal_kind)

            if newest:
                found = [row for row in found if ranked_signal(row)["observed_at"] == newest]
            found.sort(key=lambda row: (ranked_signal(row)["rank"] is None, ranked_signal(row)["rank"] or 0, row["identity"]))
        return found
