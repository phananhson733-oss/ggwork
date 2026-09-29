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


def _item(title, *, matched, posts=0, accounts=()):
    """A returned candidate item with the posted summary selection.candidate_item copies from its row."""
    records = ["SD-1"] if matched else []
    posted = {"matched": matched, "records": records, "post_count": posts, "sched_count": 0, "last_post_on": None, "accounts": list(accounts)}
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
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。", "发布记录显示《Big Boss》发过，不能说没发过。"]


def test_a_claim_borrows_titles_only_within_its_sentence_or_list_item():
    from ggwork_pick.answer_check import check_answer, with_posted

    both = with_posted({}, [_item("No Match", matched=False), _item("Lost Heir", matched=True)])
    only_heir = with_posted({}, [_item("Lost Heir", matched=True)])
    known = {"No Match", "Lost Heir", "Big Boss"}
    unmatched = ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]
    generic = ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    for seen, text, expected in (
        # A summary counts every returned record, not the title named just before it.
        (both, "《No Match》：发布记录里没有。《Lost Heir》：已对上。两部都从未发布。", unmatched),
        (both, "1. 《No Match》\n2. 《Lost Heir》\n以上两部都没发过。", unmatched),
        # A sentence end, or a line not indented deeper than the one its subject starts on, stops the borrowing: a bare
        # title this run returned is judged by its own record.
        (both, "《Lost Heir》已对上。No Match 没发过。", unmatched),
        (both, "1. 《Lost Heir》已对上\n2. 也没发过", generic),
        (both, "  1. 《Lost Heir》已对上\n  2. 也没发过", generic),
        (both, "《Lost Heir》已对上\n\n   也没发过", generic),
        # A list item's details under its title, as lines or nested bullets, are about that title.
        (both, "1. 《Lost Heir》\n   - 发布记录已对上\n   - 团队还没发过\n2. 《No Match》：发布记录里没有", []),
        (both, "- 《Lost Heir》\n  已对上，\n  还没发过", []),
        # A name no tool returned is neither borrowed over nor waved through.
        (only_heir, "Big Boss 没发过。", generic),
        (only_heir, "《Lost Heir》帖子数0；Big Boss 也没发过。", generic),
        (only_heir, "《Lost Heir》已对上，Big Boss 也没发过。", generic),
        (only_heir, "《Lost Heir》和 Big Boss 都没发过。", generic),
    ):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == expected, text
    # Platform and theater names, language codes: one Latin word is not taken for a drama name.
    for text in (
        "《Lost Heir》已对上，在 YouTube 上还没发过。",
        "《Lost Heir》是 DramaBox 的剧，已对上，团队还没发过。",
        "《Lost Heir》（ReelShort，en）已对上，还没发过。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=only_heir) == [], text


def test_an_account_query_backs_a_claim_about_that_account_only():
    from ggwork_pick.answer_check import check_answer, with_posted

    # posted_account=A returned Big Boss: the team posted it twice, from account B.
    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"])], account="A")
    known = {"Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    assert check_answer("《Big Boss》在 A 账号没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    assert check_answer("《Big Boss》：A 账号，还没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    for text in ("《Big Boss》没发过。", "《Big Boss》团队还没发过。", "《Big Boss》在 B 账号没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # "这个账号" is the account queried; naming the team, even in a clause that goes on from A's, is about every account.
    for text in ("《Big Boss》这个账号还没发过。", "这几部在该账号都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text
    for text in ("《Big Boss》在 A 账号没发过，团队也没发过。", "《Big Boss》在 A 账号没发过。团队也没发过。", "《Big Boss》在 A 账号和团队都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # An account the records name as posting it is never cleared by another query.
    both = with_posted(seen, [_item("Big Boss", matched=True, posts=3, accounts=["A", "B"])])
    assert check_answer("《Big Boss》在 A 账号没发过。", known_titles=known, posted_checked=True, posted_seen=both) == posted


def test_after_a_posted_filter_a_claim_naming_nothing_stands_on_every_record_returned():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True), _item("Big Boss", matched=True, posts=3)])
    known = {"Lost Heir", "Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    for text in ("团队还没发过。", "还没发过。", "以下是团队还没发过的剧："):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
        # Without the filter nothing backs it at all, whichever record it is about.
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"], text
    fine = with_posted({}, [_item("Lost Heir", matched=True)])
    assert check_answer("团队还没发过。", known_titles=known, posted_checked=True, posted_seen=fine) == []
    assert check_answer("团队还没发过。", known_titles=known, posted_checked=True, posted_seen={}) == []


def test_a_title_is_neither_split_by_its_punctuation_nor_read_as_a_claim():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Oops! Wed, Again", matched=True)])
    assert check_answer("《Oops! Wed, Again》发布记录已对上，团队还没发过。", known_titles={"Oops! Wed, Again"}, posted_checked=False, posted_seen=seen) == []
    assert check_answer("推荐《从未发布的秘密》。", known_titles={"从未发布的秘密"}, posted_checked=False) == []


def test_a_title_returned_with_conflicting_records_keeps_the_one_that_refutes():
    from ggwork_pick.answer_check import check_answer, with_posted

    # Two items with one title (two languages) that disagree, and an item from a batch without publication records.
    seen = with_posted(with_posted({}, [_item("Twin", matched=True)]), [_item("twin", matched=False), {"title": "Plain"}])
    known = {"Twin", "Plain"}
    unmatched = "《Twin》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"
    generic = "本轮查询没有按发布记录过滤，不能据此断言没发过。"
    for checked in (False, True):
        assert check_answer("《Twin》没发过。", known_titles=known, posted_checked=checked, posted_seen=seen) == [unmatched], checked
    assert check_answer("《Plain》没发过。", known_titles=known, posted_checked=False, posted_seen=seen) == [generic]
    assert check_answer("《Plain》没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    assert check_answer("都没发过。", known_titles=known, posted_checked=False, posted_seen=seen) == [unmatched, generic]
    posted_and_unmatched = with_posted(seen, [_item("Twin", matched=True, posts=1)])
    notes = check_answer("《Twin》没发过。", known_titles=known, posted_checked=True, posted_seen=posted_and_unmatched)
    assert notes == ["发布记录显示《Twin》发过，不能说没发过。"]


def test_a_long_list_answer_is_judged_item_by_item():
    from ggwork_pick.answer_check import check_answer, with_posted

    items = [_item(f"Drama {index}", matched=index != 1_500) for index in range(2_000)]
    seen = with_posted({}, items)
    lines = "\n".join(f"{index + 1}. 《Drama {index}》\n   发布记录已对上，帖子数0，团队还没发过" for index in range(2_000))
    notes = check_answer(lines, known_titles={item["title"] for item in items}, posted_checked=False, posted_seen=seen)
    assert notes == ["《Drama 1500》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]


def test_a_summary_covers_what_it_sums_up():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Lost Heir", matched=True)])
    known = {"Big Boss", "Lost Heir"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    # "以上" sums up the titles named before it as well as the one it names.
    for text in ("《Big Boss》已发过；包括《Lost Heir》在内的以上两部都没发过。", "《Big Boss》发过。\n《Lost Heir》已对上。\n以上都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # "其余" and the like leave out the titles named up to them.
    for text in (
        "《Big Boss》发过，其余都没发过。",
        "除了《Big Boss》，其他都没发过。",
        "《Big Boss》发过。剩下的都没发过。",
        "除了《Big Boss》都没发过。",
        "《Big Boss》以外其余都没发过。",
    ):
        for checked in (False, True):
            assert check_answer(text, known_titles=known, posted_checked=checked, posted_seen=seen) == [], (text, checked)
    # The rest still stands on the records returned for it.
    three = with_posted(seen, [_item("No Match", matched=False)])
    notes = check_answer("《Big Boss》发过，其余都没发过。", known_titles=known | {"No Match"}, posted_checked=True, posted_seen=three)
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]


def test_a_clause_naming_an_account_replaces_the_account_of_the_clause_before():
    from ggwork_pick.answer_check import check_answer, with_posted

    # posted_account=A returned Big Boss; account B posted it.
    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"])], account="A")
    known = {"Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    for text in ("《Big Boss》在 B 账号发过，在 A 账号没发过。", "《Big Boss》团队发过，在 A 账号还没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text
    # A clause naming no account goes on with the one before; one naming the account that posted it is refuted.
    assert check_answer("《Big Boss》在 B 账号发过，在 A 账号，还没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    assert check_answer("《Big Boss》在 A 账号没发过，在 B 账号也没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == posted


def test_a_title_this_run_returned_is_recognized_without_its_brackets():
    from ggwork_pick.answer_check import check_answer, with_posted

    heir = with_posted({}, [_item("Lost Heir", matched=True)])
    both = with_posted(heir, [_item("Big Boss", matched=True, posts=3)])
    known = {"Lost Heir", "Big Boss"}
    generic = ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    for text in ("Lost Heir 没发过。", "lost  heir 已对上，团队还没发过。", "《Big Boss》之外，Lost Heir 在 DramaBox 还没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=both if "Big Boss" in text else heir) == [], text
    # A bare name nothing returned is still unplaced; a bare title returned is judged by its own record.
    assert check_answer("Big Boss 没发过。", known_titles=known, posted_checked=False, posted_seen=heir) == generic
    assert check_answer("Big Boss 没发过。", known_titles=known, posted_checked=False, posted_seen=both) == ["发布记录显示《Big Boss》发过，不能说没发过。"]
    assert check_answer("Lost Heir 和 Big Boss 都没发过。", known_titles=known, posted_checked=True, posted_seen=both) == [
        "发布记录显示《Big Boss》发过，不能说没发过。"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "a" * 50_000,
        "a" * 49_997 + "没发过",
        "a " * 24_998 + "没发过",
        "a," * 24_998 + "没发过",
        "lost heir " * 4_999 + "没发过",
        "《" * 50_000,
        "《a》没发过" * 7_142,
    ],
    ids=["one-word", "one-word-claim", "words", "clauses", "bare-titles", "brackets", "titled-claims"],
)
def test_a_long_answer_is_checked_in_linear_time(text):
    import time

    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True), _item("A", matched=True), _item("Big Boss", matched=True, posts=1, accounts=["B"])], account="C")
    started = time.perf_counter()
    check_answer(text, known_titles={"Lost Heir", "A", "Big Boss"}, posted_checked=False, posted_seen=seen)
    assert time.perf_counter() - started < 0.2


@pytest_asyncio.fixture
async def workbench(tmp_path):
    """A catalog with matched and unmatched records, posts from accounts A and B, and a way to start a run on it."""
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(service.session_factory, "alice")
    items = [
        _item("Lost Heir", matched=True),
        _item("No Match", matched=False),
        _item("Big Boss", matched=True, posts=2, accounts=["B"]),
        _item("Other Show", matched=True, posts=1, accounts=["A"]),
    ]
    rows = [{"source": "s", "source_id": str(index), "language": "en", **item} for index, item in enumerate(items, 1)]
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


@pytest.mark.asyncio
async def test_an_account_query_through_the_tools_backs_only_that_account(workbench):
    from ggwork_pick.tools import query_candidates_tool

    repo, start_run = workbench
    _, runtime = await start_run("run")
    filters = {"posted_account": "A", "query": "Big Boss", "exclude_selected": False}
    found = json.loads(await query_candidates_tool.coroutine(filters=filters, runtime=runtime))
    assert [item["title"] for item in found["items"]] == ["Big Boss"]
    assert await _answer_notes(repo, runtime, "《Big Boss》在 A 账号没发过。", "m1") is None
    assert await _answer_notes(repo, runtime, "《Big Boss》团队没发过。", "m2") == ["发布记录显示《Big Boss》发过，不能说没发过。"]
