import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_unknown_titles_and_save_claims_are_flagged():
    from ggwork_pick.answer_check import check_answer

    notes = check_answer(
        "推荐《Known One》和《Invented Drama》，已为你保存到清单。",
        known_titles={"Known One"},
        posted_checked=False,
    )
    joined = "\n".join(notes)
    assert "《Invented Drama》" in joined and "Known One" not in joined
    assert "确认保存" in joined


def test_not_posted_claim_requires_a_posted_filter():
    from ggwork_pick.answer_check import check_answer

    assert any("发布记录" in n for n in check_answer("这5部都没发过。", known_titles=set(), posted_checked=False))
    assert check_answer("这5部都没发过。", known_titles=set(), posted_checked=True) == []


def test_negated_or_quoted_phrases_do_not_trigger():
    from ggwork_pick.answer_check import check_answer

    text = "点击「确认保存」后才会写入。没有接入账号发布记录时不能声称没发过。尚未保存。"
    assert check_answer(text, known_titles=set(), posted_checked=False) == []


def test_title_match_ignores_case_and_spacing():
    from ggwork_pick.answer_check import check_answer

    assert check_answer("《the  ceo's wife》", known_titles={"The CEO's Wife"}, posted_checked=False) == []


def test_ordinary_wording_and_relayed_tool_text_are_not_claims():
    from ggwork_pick.answer_check import check_answer

    # Zero-result wording, the sanctioned "no record" phrasing, and text the tools themselves return.
    for text in (
        "这次没有发现符合条件的剧。",
        "未发现符合条件的剧目，建议放宽条件。",
        "数据没发生变化。",
        "这部发布记录里没有，也就是没有发布记录。",
        "当前剧库批次没有发布记录，无法核对是否发过。",
        "第2部发布记录显示已排期未发（1条待公开）。",
        "我已加入“排除已选”的条件重新查询。",
        "已添加英语筛选。",
        "请点击卡片上的确认保存，保存完成后会显示回执。",
        "默认排除你已保存的剧。",
    ):
        assert check_answer(text, known_titles=set(), posted_checked=False) == [], text


def test_real_claims_are_still_caught_and_a_comma_ends_a_disclaimer():
    from ggwork_pick.answer_check import check_answer

    for text in ("这3部都没发过。", "《A》从来没有发布过。", "尚未发布。", "关于是否发过，它从没发过。"):
        assert any("发布记录" in n for n in check_answer(text, known_titles={"A"}, posted_checked=False)), text
    for text in ("已存入个人清单。", "已为你保存第1部。", "保存成功。", "已添加到你的选剧清单。"):
        assert any("确认保存" in n for n in check_answer(text, known_titles=set(), posted_checked=True)), text


def test_long_catalog_titles_are_checked():
    from ggwork_pick.answer_check import check_answer

    long_title = "长" * 300
    assert check_answer(f"推荐《{long_title}》", known_titles={long_title}, posted_checked=False) == []
    assert check_answer(f"推荐《{long_title}》", known_titles=set(), posted_checked=False) != []


def _item(title, *, matched, posts=0):
    """A returned candidate item with the posted summary selection.candidate_item copies from its row."""
    posted = {"matched": matched, "records": ["SD-1"] if matched else [], "post_count": posts, "sched_count": 0, "last_post_on": None, "accounts": []}
    return {"title": title, "posted": posted}


def test_a_matched_record_without_posts_backs_the_not_posted_claim():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True)])
    for text in (
        "《Lost Heir》发布记录已对上，帖子数0，团队还没发过。",
        "《Lost Heir》：目前未发布过。",
        "发布记录已对上，帖子数0，团队还没发过。",
        "目前未发布过。",
        "《Lost Heir》已对上，发帖0次。",
    ):
        assert check_answer(text, known_titles={"Lost Heir"}, posted_checked=False, posted_seen=seen) == [], text


def test_unmatched_and_posted_records_do_not_back_it():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=False), _item("Big Boss", matched=True, posts=3)])
    known = {"Lost Heir", "Big Boss"}
    for text in ("《Lost Heir》从未发布。", "《Lost Heir》：发布记录已查，团队还没发过。"):
        for checked in (False, True):
            notes = check_answer(text, known_titles=known, posted_checked=checked, posted_seen=seen)
            assert notes == ["《Lost Heir》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"], (text, checked)
    notes = check_answer("《Big Boss》没发过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["发布记录显示《Big Boss》发过，不能说没发过。"]
    notes = check_answer("目前未发布过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    assert check_answer("《Lost Heir》发布记录里没有。", known_titles=known, posted_checked=False, posted_seen=seen) == []


def test_each_claim_is_judged_by_the_titles_it_is_about():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True), _item("Big Boss", matched=True, posts=3), _item("No Match", matched=False)])
    known = {"Lost Heir", "Big Boss", "No Match"}
    for text in (
        "《Big Boss》发过3次，《Lost Heir》还没发过。",
        "《Lost Heir》没发过；《No Match》发布记录里没有。",
        "1. 《Lost Heir》\n   发布记录已对上，帖子数0，团队还没发过\n2. 《No Match》发布记录里没有",
    ):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == [], text
    notes = check_answer("《Lost Heir》和《No Match》都没发过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]
    notes = check_answer("这三部都没发过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]


def test_a_title_is_neither_split_by_its_punctuation_nor_read_as_a_claim():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Oops! Wed, Again", matched=True)])
    assert check_answer("《Oops! Wed, Again》发布记录已对上，团队还没发过。", known_titles={"Oops! Wed, Again"}, posted_checked=False, posted_seen=seen) == []
    assert check_answer("推荐《从未发布的秘密》。", known_titles={"从未发布的秘密"}, posted_checked=False) == []


def test_titles_without_a_single_clear_record_back_nothing():
    from ggwork_pick.answer_check import check_answer, with_posted

    # Two items with one title (two languages) that disagree, and an item from a batch without publication records.
    seen = with_posted(with_posted({}, [_item("Twin", matched=True)]), [_item("twin", matched=False), {"title": "Plain"}])
    for text in ("《Twin》没发过。", "《Plain》没发过。", "都没发过。"):
        assert check_answer(text, known_titles={"Twin", "Plain"}, posted_checked=False, posted_seen=seen) == [
            "本轮查询没有按发布记录过滤，不能据此断言没发过。"
        ], text
    assert check_answer("《Twin》没发过。", known_titles={"Twin"}, posted_checked=True, posted_seen=seen) == []


def test_a_long_list_answer_is_judged_item_by_item():
    from ggwork_pick.answer_check import check_answer, with_posted

    items = [_item(f"Drama {index}", matched=index != 1_500) for index in range(2_000)]
    seen = with_posted({}, items)
    lines = "\n".join(f"{index + 1}. 《Drama {index}》\n   发布记录已对上，帖子数0，团队还没发过" for index in range(2_000))
    notes = check_answer(lines, known_titles={item["title"] for item in items}, posted_checked=False, posted_seen=seen)
    assert notes == ["《Drama 1500》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]


@pytest_asyncio.fixture
async def workbench(tmp_path):
    """A catalog with one matched record without posts and one unmatched record, and a way to start a run on it."""
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(service.session_factory, "alice")
    rows = [
        {"source": "s", "source_id": "1", "language": "en", "title": "Lost Heir", "posted": _item("Lost Heir", matched=True)["posted"]},
        {"source": "s", "source_id": "2", "language": "en", "title": "No Match", "posted": _item("No Match", matched=False)["posted"]},
    ]
    await Importer(repo, service.data_dir).catalog(json.dumps(rows).encode(), "json")

    async def start_run(run_id, **context):
        store = ExtensionData(run_id)
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo(run_id, run_id, "thread", "lead"))
        return store, SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store, **context}, tool_call_id="call")

    yield repo, start_run
    await engine.dispose()


async def _answer_notes(repo, runtime, text, message_id):
    """The notes PickModelGate stores beside a final answer, or None when it stores none."""
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage

    from ggwork_pick.middleware import PickModelGate

    request = ModelRequest(model=SimpleNamespace(), messages=[], runtime=runtime, tools=[])
    await PickModelGate().awrap_model_call(request, AsyncMock(return_value=ModelResponse(result=[AIMessage(content=text, id=message_id)])))
    return {check["message_id"]: check["notes"] for check in await repo.answer_checks("thread")}.get(message_id)


@pytest.mark.asyncio
async def test_a_title_lookup_backs_the_answer_through_the_tools_and_the_model_gate(workbench):
    from ggwork_pick.context import PickTask
    from ggwork_pick.tools import query_candidates_tool

    repo, start_run = workbench
    store, runtime = await start_run("run")
    for call_id, title in (("q1", "Lost Heir"), ("q2", "No Match")):
        runtime.tool_call_id = call_id
        found = json.loads(await query_candidates_tool.coroutine(filters={"query": title, "exclude_selected": False}, runtime=runtime))
        assert [item["title"] for item in found["items"]] == [title]
    assert store.get(PickTask).posted_checked is False
    assert await _answer_notes(repo, runtime, "《Lost Heir》发布记录已对上，帖子数0，团队还没发过。", "m1") is None
    notes = await _answer_notes(repo, runtime, "《No Match》从未发布。", "m2")
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]


@pytest.mark.asyncio
async def test_a_detail_read_backs_the_answer_about_that_item(workbench):
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.tools import get_drama_detail_tool

    repo, start_run = workbench
    parent = await SelectionService(repo).query({"query": "Lost Heir", "exclude_selected": False}, thread_id="thread", run_id="r0", call_id="c0")
    _, runtime = await start_run("r1", pick_reference={"result_id": parent["id"]})
    assert await _answer_notes(repo, runtime, "目前未发布过。", "m1") == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    await get_drama_detail_tool.coroutine(result_id=parent["id"], item_id=parent["items"][0]["item_id"], runtime=runtime)
    assert await _answer_notes(repo, runtime, "目前未发布过。", "m2") is None
