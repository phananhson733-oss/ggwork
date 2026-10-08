"""Current mandatory execution facts, bounded to the plan's exact identities."""

import base64
import json

from pydantic import ValidationError

from ggwork_pick.completion_contracts import CommonQuery
from ggwork_pick.contracts import DramaInput, PickConditions
from ggwork_pick.freshness import data_notices
from ggwork_pick.mirror.versions import check_schema_name
from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.query_service import canonical_rows, fact_rows, wire


async def current_facts(query_service, identities, *, deadline):
    """One validated current pin, then one readonly batch for at most 100 identities.

    Canonical imports are an immutable two-entry repository cache. Mirror raw rows
    and rules remain authoritative for explicit denials; they never become writer
    connection reads or one fresh deadline per plan row.
    """
    if len(identities) > 100:
        raise QueryFailure("invalid_query", "执行计划最多核对100个身份")
    request = CommonQuery(domain="catalog", scope="full_catalog", with_off=True, exclude_selected=False, confirmed_eligible_only=False, limit=1)
    result = await query_service.query(request, deadline=deadline)
    imported = await query_service.repository.catalog_rows(result.pin.catalog_batch_id)
    if result.pin.mirror_version is None:
        facts = {}
        for raw in imported:
            if raw["identity"] in identities:
                drama = DramaInput.model_validate({k: v for k, v in raw.items() if k in DramaInput.model_fields})
                facts[drama.identity] = drama
    else:
        facts = await mirror_facts(query_service, result, imported, identities, deadline)
    ages = await query_service.repository.data_as_of(result.pin.catalog_batch_id)
    return result.pin, facts, data_notices(ages, [], PickConditions())


async def mirror_facts(query_service, result, imported, identities, deadline):
    canonical = canonical_rows(imported)
    keys = {key for key, row in canonical.items() if row["identity"] in identities}
    for identity in identities:
        try:
            source, source_id, _ = json.loads(identity)
            if source == "realshort-pick":
                key = base64.urlsafe_b64decode(source_id + "=" * (-len(source_id) % 4)).decode()
                if base64.urlsafe_b64encode(key.encode()).decode().rstrip("=") != source_id:
                    raise ValueError
                keys.add(key)
        except (ValueError, UnicodeError, TypeError):
            raise QueryFailure("source_unavailable", "计划来源身份无法与当前镜像核对") from None
    if result.board is None or result.board.rules is None or query_service.reader is None:
        raise QueryFailure("source_unavailable", "当前镜像规则无法完整读取")
    try:
        async with query_service.reader.connection(deadline=deadline) as conn:
            version = await conn.fetchrow("SELECT * FROM pick_mirror.versions WHERE id=$1", result.pin.mirror_version)
            if version is None or version["status"] != "published":
                raise QueryFailure("version_gone", "当前来源版本已不可读取")
            if (version["agent_catalog_batch_id"], version["agent_knowledge_batch_id"]) != (result.pin.catalog_batch_id, result.pin.knowledge_batch_id):
                raise QueryFailure("version_conflict", "当前来源配对已变化，请重新预览")
            schema = check_schema_name(version["schema_name"])
            await conn.execute(f"SET LOCAL search_path = {schema},pick_mirror")
            board = {"row_keys": sorted(keys), "rules": result.board.rules.model_dump(mode="json", by_alias=True), "posted": []}
            for table in ("catalog_rows", "rs_rows"):
                board[table] = [wire(dict(row)) for row in await conn.fetch(f"SELECT * FROM {table} WHERE row_key=ANY($1::text[])", sorted(keys))]
            if any(row["platform"] not in board["rules"]["platformRules"] for table in ("catalog_rows", "rs_rows") for row in board[table]):
                raise QueryFailure("source_unavailable", "当前镜像缺少必要剧场规则，无法确认执行资格")
            # Missing raw source rows cannot inherit an imported affirmative value.
            present = {row["row_key"] for table in ("catalog_rows", "rs_rows") for row in board[table]}
            board["row_keys"] = [key for key in board["row_keys"] if key in present]
            raw_by_key = {row["row_key"]: row for table in ("catalog_rows", "rs_rows") for row in board[table]}
            restrictions = {}
            for key, raw in raw_by_key.items():
                rule = board["rules"]["platformRules"][raw["platform"]]["yt"]
                if rule not in ("ok", "no", "only"):
                    restrictions[key] = "unknown"
                elif rule == "only" and raw["youtube"] is not True:
                    restrictions[key] = "denied"
            # These are negative execution constraints only: global ok/only never
            # create a positive per-drama grant. Unknown/warn and list exclusion
            # cannot inherit an older canonical affirmative permission.
            by_identity = {}
            for key, restriction in restrictions.items():
                identity = (
                    canonical[key]["identity"]
                    if key in canonical
                    else json.dumps(
                        ["realshort-pick", base64.urlsafe_b64encode(key.encode()).decode().rstrip("="), raw_by_key[key]["lang"]],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                by_identity[identity] = restriction
            output = {}
            for row in fact_rows(board, imported, result.request):
                if row["identity"] in identities:
                    drama = row["drama"]
                    if row["identity"] in by_identity:
                        drama = drama.model_copy(update={"channel_rules": {**drama.channel_rules, "youtube": by_identity[row["identity"]]}})
                    output[row["identity"]] = drama
            return output
    except QueryFailure:
        raise
    except (OSError, ValidationError, ValueError):
        raise QueryFailure("source_unavailable", "当前镜像执行依据无法完整读取") from None
    except Exception as exc:
        if getattr(exc, "sqlstate", None):
            raise QueryFailure("source_unavailable", "当前镜像执行依据无法完整读取") from None
        raise
