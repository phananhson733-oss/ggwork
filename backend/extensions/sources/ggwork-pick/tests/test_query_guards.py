"""2026-09-28: 「US 地区热门、没上过」 was sent as theater="US" and matched nothing three times; the model then blamed the
candidate pool. Values the batch lacks are refused, a zero result says which condition emptied it, and hot_only asks
for any theater board without letting ReelShort's site-activity kinds count. Both dialects; synthetic data only."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

REQUEST_TS = Path(__file__).resolve().parents[3] / "frontend/src/core/pick-board/request.ts"


def signal(kind, observed, rank=None):
    return {"kind": kind, "source_ref": f"ref:{kind}:{observed}", "observed_at": observed, "rank": rank}


def drama(i, *, theater="KalosTV", language="en", signals=(), posted=0, tags=(), availability="unknown", rules=None):
    return {
        "source": "realshort-pick",
        "source_id": f"g{i}",
        "language": language,
        "title": f"Guard {i}",
        "theater": theater,
        "tags": list(tags),
        "availability": availability,
        "channel_rules": rules or {"youtube": "unknown"},
        "signals": list(signals),
        "posted": {
            "matched": bool(posted),
            "records": [f"SD-{i}"] if posted else [],
            "post_count": posted,
            "sched_count": 0,
            "last_post_on": "2026-09-10" if posted else None,
            "accounts": ["acc"] if posted else [],
        },
    }


CATALOG = [
    # ReelShort rows carry only site activity: clk dated today, like production.
    drama(1, theater="ReelShort", signals=[signal("clk", "2026-09-28")]),
    drama(2, theater="ReelShort", signals=[signal("clk", "2026-09-28"), signal("bill", "2026-09-24")]),
    drama(3, signals=[signal("kd", "2026-09-23", 2)], tags=["复仇"]),
    drama(4, signals=[signal("kd", "2026-09-23", 5)], posted=2),
    drama(5, theater="ShortMax", signals=[signal("sm", None)]),
    drama(6, theater="ReelShort", signals=[signal("clk", "2026-09-28"), signal("qr", "2026-09-20", 3)]),
    drama(7, theater="ShortMax", language="ko", signals=[signal("sm", None)]),
    drama(8, signals=[signal("kd", "2026-09-21", 1)], availability="delisted"),
]


@pytest_asyncio.fixture
async def repo(pick_db_url, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(svc.session_factory, "alice")
    await Importer(repo, svc.data_dir).catalog(json.dumps(CATALOG).encode(), "json")
    yield repo
    await engine.dispose()


async def query(repo, filters, call_id="c", **kwargs):
    from ggwork_pick.selection import SelectionService

    return await SelectionService(repo).query(filters, thread_id="t", run_id="r", call_id=call_id, **kwargs)


# ---- values the batch does not contain -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_region_as_theater_is_refused_with_the_theaters_and_the_language_to_use(repo):
    from ggwork_pick.selection import SelectionService

    with pytest.raises(ValueError) as refused:
        await query(repo, {"theater": "US", "exclude_posted": True})
    message = str(refused.value)
    assert "剧场「US」" in message and "language=en" in message
    # The theaters the batch has, most rows first, so the model can pick one.
    assert message.index("ReelShort") < message.index("KalosTV") < message.index("ShortMax")
    with pytest.raises(ValueError, match="剧场「US」"):
        await SelectionService(repo).count({"theater": "US"})
    # A known theater in another case still matches, like before.
    kept = await query(repo, {"theater": "kalostv"}, call_id="c2")
    assert kept["matched_total"] == 2


@pytest.mark.asyncio
async def test_an_unknown_theater_that_is_not_a_region_gets_the_general_hint(repo):
    with pytest.raises(ValueError) as refused:
        await query(repo, {"theater": "DramaWave"})
    assert "剧库没有地区字段" in str(refused.value) and "language=" not in str(refused.value)


@pytest.mark.asyncio
async def test_an_unknown_language_is_refused_with_the_codes(repo):
    with pytest.raises(ValueError) as refused:
        await query(repo, {"language": "USA"})
    message = str(refused.value)
    assert "语种「USA」" in message and "en、ko" in message and "language=en" in message
    with pytest.raises(ValueError, match="语种「english」"):
        await query(repo, {"language": "english"}, call_id="c2")
    assert (await query(repo, {"language": "EN"}, call_id="c3"))["matched_total"] == 6


@pytest.mark.asyncio
async def test_unknown_tags_are_refused_but_a_known_tag_combination_may_match_nothing(repo):
    with pytest.raises(ValueError, match="标签「美国」.*language=en"):
        await query(repo, {"tags": ["复仇", "美国"]})
    with pytest.raises(ValueError, match="标签「霸总」"):
        await query(repo, {"tags": ["霸总"]}, call_id="c2")
    # Every tag exists, the combination with another condition is simply empty: a real zero.
    empty = await query(repo, {"tags": ["复仇"], "theater": "ReelShort"}, call_id="c3")
    assert empty["matched_total"] == 0


@pytest.mark.asyncio
async def test_a_region_as_the_title_word_is_refused_instead_of_matching_inside_titles(repo):
    # "us" is a substring of many English titles ("Husband", "Business"): it must not pass as a region filter.
    # A region alone, or a region with nothing but filter words (09-28's ask as a title word), is refused.
    for call_id, word in enumerate(("US", " 美国 ", "North America", "美国地区", "US 美国", "US 热门", "美国热门短剧", "hot dramas in the US")):
        with pytest.raises(ValueError, match="不是地区"):
            await query(repo, {"query": word}, call_id=f"c{call_id}")
    # Anything else stays a title search, including titles holding a region word or "us" inside a word.
    for call_id, word in enumerate(("Guard 3", "Husband", "美國總裁", "Made in USA")):
        await query(repo, {"query": word}, call_id=f"ok{call_id}")
    assert (await query(repo, {"query": "Guard 3"}, call_id="ok9"))["matched_total"] == 1


@pytest.mark.asyncio
async def test_unknown_tag_lists_the_batch_tags_or_says_there_are_none(repo, pick_db_url, tmp_path):
    with pytest.raises(ValueError, match="可选：复仇"):
        await query(repo, {"tags": ["Revenge"]})


@pytest.mark.asyncio
async def test_hot_only_on_a_batch_without_hot_evidence_is_refused(pick_db_url, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(svc.session_factory, "alice")
    await Importer(repo, svc.data_dir).catalog(json.dumps(CATALOG[:2]).encode(), "json")
    with pytest.raises(ValueError, match="没有热门依据"):
        await query(repo, {"hot_only": True})
    with pytest.raises(ValueError, match="都没有标签"):
        await query(repo, {"tags": ["复仇"]}, call_id="c2")
    await engine.dispose()


# ---- zero results explain themselves ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_diagnosis_counts_each_condition_taken_away(repo):
    from ggwork_pick.selection import SelectionService

    service = SelectionService(repo)
    result = await query(repo, {"theater": "ShortMax", "language": "en", "signal_kind": "kd", "exclude_posted": True})
    assert result["matched_total"] == 0
    explained = await service.explain(await repo.result(result["id"]))
    diagnosis = explained["zero_diagnosis"]
    assert diagnosis["catalog_rows"] == 8 and diagnosis["delisted_rows"] == 1
    assert [(d["condition"], d["value"], d["matched_total"]) for d in diagnosis["without_each"]] == [
        ("theater", "ShortMax", 1),  # KalosTV kd rows, en, not posted: drama 3 (4 was posted, 8 is delisted)
        ("language", "en", 0),
        ("signal_kind", "kd", 1),  # ShortMax en: drama 5
        ("exclude_posted", True, 0),
    ]
    assert "hot_scope" not in explained
    # A non-empty result carries no diagnosis, and neither does the stored view the card reads.
    kept = await query(repo, {"language": "en"}, call_id="c2")
    assert await service.explain(await repo.result(kept["id"])) == {}
    assert "zero_diagnosis" not in result


@pytest.mark.asyncio
async def test_zero_diagnosis_covers_channel_eligibility_rank_boards_and_exclusions(repo):
    from ggwork_pick.selection import SelectionService

    service = SelectionService(repo)
    # Every row's youtube rule is unknown, so a confirmed-eligible channel query is always empty (production 09-23/24).
    counted = await service.count({"language": "en", "channel": "youtube"})
    assert counted["total"] == 0
    steps = {d["condition"]: d["matched_total"] for d in counted["zero_diagnosis"]["without_each"]}
    assert steps == {"language": 0, "channel": 6, "confirmed_eligible_only": 6}
    # Ranked on the latest kd board (09-23): drama 3 and 4; not posted and not ReelShort leaves 3, then excluded by id.
    parent = await query(repo, {"signal_kind": "kd", "sort": "rank", "exclude_posted": True, "limit": 1}, call_id="p")
    assert [i["title"] for i in parent["items"]] == ["Guard 3"]
    more = await query(repo, {"exclude_previous": True}, call_id="m", parent_result_id=parent["id"])
    assert more["matched_total"] == 0
    steps = [(d["condition"], d["matched_total"]) for d in (await service.explain(await repo.result(more["id"])))["zero_diagnosis"]["without_each"]]
    # Without signal_kind the sort goes with it; without sort only the latest-board limit goes (and 8 is delisted).
    assert steps == [("signal_kind", 5), ("sort", 0), ("exclude_posted", 1), ("excluded", 1)]


@pytest.mark.asyncio
async def test_the_signal_kind_step_says_it_took_the_rank_sort_with_it(repo):
    from ggwork_pick.selection import SelectionService

    counted = await SelectionService(repo).count({"language": "ko", "signal_kind": "kd", "sort": "rank"})
    steps = {d["condition"]: d for d in counted["zero_diagnosis"]["without_each"]}
    assert steps["signal_kind"]["also_removed"] == ["sort"] and steps["signal_kind"]["matched_total"] == 1
    assert "also_removed" not in steps["sort"] and "also_removed" not in steps["language"]
    assert "also_removed" in counted["zero_diagnosis"]["note"]


@pytest.mark.asyncio
async def test_a_refusal_on_an_old_card_says_how_to_clear_the_inherited_field(repo):
    """An old theater="US" card: 换一批 keeps the parent's theater unless it is sent as null, as the notice says."""
    from sqlalchemy import update

    from ggwork_pick.models import candidate_sets

    parent = await query(repo, {"exclude_posted": True, "limit": 1}, call_id="p")
    stored = (await repo.result(parent["id"]))["conditions_json"]
    async with repo.session_factory() as session, session.begin():
        await session.execute(update(candidate_sets).where(candidate_sets.c.id == parent["id"]).values(conditions_json={**stored, "theater": "US"}))
    for call_id, filters in (("m1", {"exclude_previous": True}), ("m2", {"exclude_previous": True, "language": "en"})):
        with pytest.raises(ValueError, match="theater:null"):
            await query(repo, filters, call_id=call_id, parent_result_id=parent["id"])
    recovered = await query(repo, {"exclude_previous": True, "language": "en", "theater": None}, call_id="m3", parent_result_id=parent["id"])
    assert recovered["conditions"]["theater"] is None and recovered["matched_total"] == 4
    with pytest.raises(ValueError, match=r"tags:\[\]"):
        await query(repo, {"tags": ["霸总"]}, call_id="t")
    with pytest.raises(ValueError, match="query:null"):
        await query(repo, {"query": "US"}, call_id="q")


@pytest.mark.asyncio
async def test_query_tool_answers_with_the_diagnosis_also_on_a_repeated_call(tmp_path):
    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import count_candidates_tool, query_candidates_tool

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(json.dumps(CATALOG).encode(), "json")
    store = ExtensionData("task1")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task1", "run1", "thread1", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call1")
    filters = {"theater": "ShortMax", "language": "en", "signal_kind": "kd"}
    first = json.loads(await query_candidates_tool.coroutine(filters=filters, runtime=runtime))
    again = json.loads(await query_candidates_tool.coroutine(filters=filters, runtime=runtime))
    assert first["matched_total"] == 0 and first["zero_diagnosis"] == again["zero_diagnosis"] and first["id"] == again["id"]
    # The tool refuses the region the way the card shows it: status rejected with a notice, no result stored.
    runtime.tool_call_id = "call2"
    refused = json.loads(await query_candidates_tool.coroutine(filters={"theater": "US"}, runtime=runtime))
    assert refused["status"] == "rejected" and "language=en" in refused["notice"]
    runtime.tool_call_id = "call3"
    counted = json.loads(await count_candidates_tool.coroutine(filters={"language": "ko", "signal_kind": "kd"}, runtime=runtime))
    assert counted["total"] == 0 and counted["zero_diagnosis"]["without_each"][0] == {"condition": "language", "value": "ko", "matched_total": 2}
    assert len(await PickRepository(service.session_factory, "alice").results("thread1")) == 1
    await engine.dispose()


# ---- hot_only ----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hot_only_keeps_theater_evidence_and_orders_by_it_not_by_site_activity(repo):
    from ggwork_pick.selection import HOT_RANKING_VERSION, SelectionService

    result = await query(repo, {"language": "en", "hot_only": True, "exclude_posted": True})
    # 1 and 2 only have clk/bill; 4 is posted; 8 delisted. Drama 6's clk is newest, but its hot evidence (qr 09-20) is
    # older than drama 3's kd (09-23); drama 5's sm has no date.
    assert [i["title"] for i in result["items"]] == ["Guard 3", "Guard 6", "Guard 5"]
    assert result["ranking_version"] == HOT_RANKING_VERSION
    assert result["conditions"]["hot_only"] is True
    assert "其中1条是热门依据" in result["items"][1]["reason"]
    explained = await SelectionService(repo).explain(await repo.result(result["id"]))
    assert explained == {"hot_scope": {"counted": ["kd", "qr", "sm"], "not_counted": ["bill", "clk"]}}
    counted = await SelectionService(repo).count({"language": "en", "hot_only": True})
    assert counted["total"] == 4 and counted["hot_scope"]["not_counted"] == ["bill", "clk"]


@pytest.mark.asyncio
async def test_hot_only_is_stored_only_when_set_so_old_card_shapes_and_hashes_stay(repo):
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.selection import RANKING_VERSION, _request_hash, unmappable_conditions

    plain = await query(repo, {"language": "en"})
    assert "hot_only" not in plain["conditions"] and plain["ranking_version"] == RANKING_VERSION
    assert "hot_only" not in (await repo.result(plain["id"]))["conditions_json"]
    assert "hot_only" not in PickConditions(hot_only=False).model_dump()
    assert _request_hash(PickConditions(language="en"), None, False) == _request_hash(PickConditions(language="en", hot_only=False), None, False)
    assert PickConditions(hot_only=True).model_dump(exclude_unset=True) == {"hot_only": True}
    assert unmappable_conditions(PickConditions(hot_only=True, exclude_selected=False, exclude_previous=True)) == ["exclude_previous", "hot_only"]
    # 换一批 on a hot card keeps it hot.
    parent = await query(repo, {"hot_only": True, "limit": 1}, call_id="p")
    more = await query(repo, {"exclude_previous": True}, call_id="m", parent_result_id=parent["id"])
    assert more["conditions"]["hot_only"] is True and more["matched_total"] == parent["matched_total"] - 1


@pytest.mark.asyncio
async def test_an_explicit_hot_only_false_lifts_a_hot_parent_through_the_tools(tmp_path):
    """换一批 on a hot card with hot_only=false must drop the filter: the tools pass the explicit false on to the merge."""
    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import count_candidates_tool, query_candidates_tool

    assert PickConditions.model_validate({"exclude_previous": True, "hot_only": False}).requested() == {"exclude_previous": True, "hot_only": False}
    assert PickConditions.model_validate({"exclude_previous": True}).requested() == {"exclude_previous": True}
    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).catalog(json.dumps(CATALOG).encode(), "json")
    parent = await SelectionService(repo).query({"language": "en", "hot_only": True, "limit": 1}, thread_id="t", run_id="r0", call_id="c0")
    store = ExtensionData("task")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "r1", "t", "lead"))
    context = {"user_id": "alice", "pick_reference": {"result_id": parent["id"]}, EXTENSION_TASK_STORE_KEY: store}
    runtime = SimpleNamespace(context=context, tool_call_id="q1")
    lifted = json.loads(await query_candidates_tool.coroutine(filters={"exclude_previous": True, "hot_only": False, "limit": 10}, runtime=runtime))
    assert "hot_only" not in lifted["conditions"] and lifted["ranking_version"] == "evidence-date-v1"
    # en and listed: 1-6 without the parent's item (3); drama 1 and 2 have only clk/bill and come back.
    assert lifted["matched_total"] == 5 and {"Guard 1", "Guard 2"} <= {i["title"] for i in lifted["items"]}
    counted = json.loads(await count_candidates_tool.coroutine(filters={"exclude_previous": True, "hot_only": False}, runtime=runtime))
    assert counted["total"] == 5 and "hot_only" not in counted["conditions"]
    runtime.tool_call_id = "q2"
    kept = json.loads(await query_candidates_tool.coroutine(filters={"exclude_previous": True}, runtime=runtime))
    assert kept["conditions"]["hot_only"] is True and kept["matched_total"] == parent["matched_total"] - 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_step_that_reaches_rows_without_publication_records_is_uncountable_not_a_refusal(pick_db_url, tmp_path):
    """The query stops at the title and never checks publication records; taking the title away would. Its zero stands."""
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(svc.session_factory, "alice")
    legacy = {key: value for key, value in drama(1, signals=[signal("kd", "2026-09-28", 1)]).items() if key != "posted"}
    await Importer(repo, svc.data_dir).catalog(json.dumps([legacy]).encode(), "json")
    result = await query(repo, {"query": "missing title", "exclude_posted": True})
    assert result["matched_total"] == 0
    steps = (await SelectionService(repo).explain(await repo.result(result["id"])))["zero_diagnosis"]["without_each"]
    assert steps[0]["condition"] == "query" and steps[0]["matched_total"] is None and "发布记录" in steps[0]["unavailable"]
    assert steps[1] == {"condition": "exclude_posted", "value": True, "matched_total": 0}
    counted = await SelectionService(repo).count({"query": "missing title", "exclude_posted": True})
    assert counted["total"] == 0 and counted["zero_diagnosis"]["without_each"][0]["matched_total"] is None
    await engine.dispose()


def test_every_condition_field_is_classified_for_the_diagnosis():
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.selection import DIAGNOSED_FIELDS, UNDIAGNOSED_FIELDS, _relaxations

    assert set(DIAGNOSED_FIELDS) | UNDIAGNOSED_FIELDS == set(PickConditions.model_fields)
    assert not set(DIAGNOSED_FIELDS) & UNDIAGNOSED_FIELDS
    every = PickConditions(
        theater="t", language="en", channel="youtube", query="q", tags=["x"], signal_kind="kd", sort="rank", hot_only=True,
        exclude_posted=True, posted_account="a",
    )  # fmt: skip
    # The list says what _relaxations really takes away, in its order, and the exclusions come as one extra step.
    assert [name for name, *_ in _relaxations(every, frozenset({"id"}))] == [*DIAGNOSED_FIELDS, "excluded"]


def test_omitted_defaults_compare_against_a_factory_default():
    from ggwork_pick.contracts import PickConditions

    class WithList(PickConditions):
        OMIT_AT_DEFAULT = (*PickConditions.OMIT_AT_DEFAULT, "tags")

    assert "tags" not in WithList().model_dump() and WithList(tags=["x"]).model_dump()["tags"] == ["x"]
    assert WithList.model_validate({"tags": []}).requested()["tags"] == []


def test_hot_kinds_are_the_data_page_theater_bases():
    from ggwork_pick.references import HOT_SIGNAL_KINDS

    body = re.search(r"export const THEATER_BASES = \[(.*?)\] as const;", REQUEST_TS.read_text(), re.S)
    assert body, "THEATER_BASES moved: keep hot_only's whitelist equal to the data page's theater bases"
    assert tuple(re.findall(r'"([a-z]+)"', body.group(1))) == HOT_SIGNAL_KINDS


def test_region_hints_point_at_languages_the_feed_uses():
    from ggwork_pick.references import REGION_LANGUAGES, region_language

    assert set(REGION_LANGUAGES.values()) <= {"en", "ko", "ja", "es", "pt", "id", "th", "fr", "de", "zh-hant", "ar"}
    assert all(key == key.strip().casefold() for key in REGION_LANGUAGES)
    assert region_language(" USA ") == region_language("美国") == "en" and region_language("ReelShort") is None
