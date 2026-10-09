"""Two bound results retain distinct frozen clocks even when mirror reuses batches."""

import json

import pytest
import test_mirror_frozen as frozen
from test_plural_references import runtime_for

world = frozen.world


@pytest.mark.asyncio
async def test_plural_read_uses_each_snapshot_clock_after_same_batch_mirror_repair(world):
    from ggwork_pick.context import PickTask
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.tools import get_drama_detail_tool

    version1, version2, _, old = await frozen._card_then_repaired(world)
    newer = await SelectionService(frozen._alice(world)).query({"limit": 2}, thread_id="t", run_id="second", call_id="second")
    new = await frozen._alice(world).result(newer["id"])
    refs = {"version": "pick-references-v1", "references": [{"result_id": r["id"], "item_ids": [r["ordered_items_json"][0]["item_id"]]} for r in [old, new]]}
    runtime, store = await runtime_for(world.service, refs, thread="t")
    clocks = []
    for i, group in enumerate(refs["references"]):
        runtime.tool_call_id = f"detail-{i}"
        reply = json.loads(await get_drama_detail_tool.coroutine(result_id=group["result_id"], item_id=group["item_ids"][0], runtime=runtime))
        clocks.append(reply["data_as_of"])
    assert clocks[0]["source_as_of"] == frozen.AS_OF_TEXT
    assert clocks[1]["source_as_of"] == frozen.LATER_TEXT
    task = store.get(PickTask)
    assert task.mirror_version == version2
    assert [ref["mirror_version"] for ref in task.reference_context] == [version1, version2]
    assert old["catalog_batch_id"] == new["catalog_batch_id"]  # Different versions even though one batch was reused.
