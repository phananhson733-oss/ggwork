"""Current mandatory execution facts, bounded to the plan's exact identities."""

from ggwork_pick.completion_contracts import CommonQuery
from ggwork_pick.contracts import DramaInput, PickConditions
from ggwork_pick.freshness import data_notices


async def current_facts(query_service, identities, *, deadline):
    # Resolve owner, source pair and rules through the same query boundary as the board.
    result = await query_service.query(CommonQuery(domain="catalog", scope="full_catalog", with_off=True, exclude_selected=False, limit=1), deadline=deadline)
    imported = await query_service.repository.catalog_rows(result.pin.catalog_batch_id)
    if result.pin.mirror_version is not None:
        # Mirror validation is added with the canonical batch projection; flattened
        # imports alone never establish a mirror's current execution permission.
        from ggwork_pick.query_reader import QueryFailure

        raise QueryFailure("source_unavailable", "当前镜像执行依据暂不可读取")
    facts = {}
    for raw in imported:
        drama = DramaInput.model_validate({k: v for k, v in raw.items() if k in DramaInput.model_fields})
        if drama.identity in identities:
            facts[drama.identity] = drama
    ages = await query_service.repository.data_as_of(result.pin.catalog_batch_id)
    return result.pin, facts, data_notices(ages, [], PickConditions())
