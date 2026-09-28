import json

import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker


def row(i, *, rank=None, kind="kd", observed="2026-09-20", posted=None, sched=0, accounts=(), matched=None, language="en"):
    signals = [{"kind": kind, "label": "KalosTV 日榜", "source_ref": f"ref:{i}", "observed_at": observed, "rank": rank, "grade": "", "note": ""}]
    record = {
        "source": "realshort-pick",
        "source_id": f"r{i}",
        "language": language,
        "title": f"Drama {i}",
        "theater": "KalosTV",
        "signals": signals,
    }
    if posted is not None:
        record["posted"] = {
            "matched": bool(posted or sched or accounts) if matched is None else matched,
            "records": [f"SD-{i}"] if (posted or sched) else [],
            "post_count": posted,
            "sched_count": sched,
            "last_post_on": "2026-09-10" if posted else None,
            "accounts": list(accounts),
        }
    return record


@pytest_asyncio.fixture
async def repo(pick_db_url, tmp_path):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield PickRepository(svc.session_factory, "alice"), svc
    await engine.dispose()


async def load(repo_svc, rows):
    from ggwork_pick.imports import Importer

    repo, svc = repo_svc
    return await Importer(repo, svc.data_dir).catalog(json.dumps(rows).encode(), "json")


@pytest.mark.asyncio
async def test_rank_sort_only_compares_one_signal_kind(repo):
    from ggwork_pick.selection import SelectionService

    await load(
        repo,
        [
            row(1, rank=5, observed="2026-09-21"),
            row(2, rank=1, observed="2026-09-21"),
            row(3, rank=None, observed="2026-09-21"),
            row(4, rank=2, kind="sm", observed="2026-09-21"),
            row(5, rank=1, observed="2026-08-05"),
        ],
    )
    result = await SelectionService(repo[0]).query({"signal_kind": "kd", "sort": "rank", "limit": 10}, thread_id="t", run_id="r", call_id="c")
    # One board at a time: only the latest kd day is ranked; an older day's #1 is not mixed in.
    assert [i["title"] for i in result["items"]] == ["Drama 2", "Drama 1", "Drama 3"]
    assert result["ranking_version"] == "signal-rank-v1"
    assert result["matched_total"] == 3
    assert "2026-09-21" in result["items"][0]["reason"]
    with pytest.raises(ValueError, match="signal_kind"):
        await SelectionService(repo[0]).query({"sort": "rank"}, thread_id="t", run_id="r", call_id="c2")


@pytest.mark.asyncio
async def test_exclude_posted_and_account_use_synced_records_and_warn_about_unmatched(repo):
    from ggwork_pick.selection import SelectionService

    await load(
        repo,
        [
            row(1, posted=2, accounts=["dramaclips0364"]),
            row(2, posted=0, sched=1, accounts=[]),
            row(3, posted=0),
            row(4, posted=1, accounts=["other"]),
        ],
    )
    service = SelectionService(repo[0])
    unposted = await service.query({"exclude_posted": True, "limit": 10}, thread_id="t", run_id="r", call_id="c1")
    assert {i["title"] for i in unposted["items"]} == {"Drama 2", "Drama 3"}
    by_title = {i["title"]: i for i in unposted["items"]}
    assert any("已排期" in w for w in by_title["Drama 2"]["warnings"])
    assert any("不代表从未发布" in w for w in by_title["Drama 3"]["warnings"])
    assert by_title["Drama 3"]["posted"]["matched"] is False
    account = await service.query({"posted_account": "DramaClips0364", "limit": 10}, thread_id="t", run_id="r", call_id="c2")
    assert {i["title"] for i in account["items"]} == {"Drama 2", "Drama 3", "Drama 4"}


@pytest.mark.asyncio
async def test_posted_filters_refuse_batches_without_publication_records(repo):
    from ggwork_pick.selection import PostedDataUnavailable, SelectionService

    await load(repo, [row(1), row(2)])
    with pytest.raises(PostedDataUnavailable):
        await SelectionService(repo[0]).query({"exclude_posted": True}, thread_id="t", run_id="r", call_id="c")


@pytest.mark.asyncio
async def test_count_matches_query_without_creating_a_result(repo):
    from ggwork_pick.selection import SelectionService

    await load(repo, [row(1, posted=0), row(2, posted=1), row(3, posted=0, language="ko")])
    service = SelectionService(repo[0])
    counted = await service.count({"exclude_posted": True})
    assert counted["total"] == 2
    assert counted["by_language"] == {"en": 1, "ko": 1}
    assert counted["by_theater"] == {"KalosTV": 2}
    assert await repo[0].results("t") == []


@pytest.mark.asyncio
async def test_signal_fields_survive_import_and_reach_evidence(repo):
    from ggwork_pick.selection import SelectionService

    await load(repo, [row(1, rank=7)])
    result = await SelectionService(repo[0]).query({}, thread_id="t", run_id="r", call_id="c")
    evidence = result["items"][0]["evidence"][0]
    assert evidence["rank"] == 7 and evidence["label"] == "KalosTV 日榜"


@pytest.mark.asyncio
async def test_rank_sort_on_a_signal_without_ranks_is_refused(repo):
    from ggwork_pick.selection import SelectionService

    await load(repo, [row(1, kind="kw", rank=None), row(2, kind="kw", rank=None)])
    with pytest.raises(ValueError, match="没有名次"):
        await SelectionService(repo[0]).query({"signal_kind": "kw", "sort": "rank"}, thread_id="t", run_id="r", call_id="c")
    plain = await SelectionService(repo[0]).query({"signal_kind": "kw"}, thread_id="t", run_id="r", call_id="c2")
    assert plain["matched_total"] == 2


@pytest.mark.asyncio
async def test_empty_result_reports_zero_matches(repo):
    from ggwork_pick.selection import SelectionService

    await load(repo, [row(1)])
    # A language the batch lacks is refused now (test_query_guards); a query word that matches nothing is still a zero.
    result = await SelectionService(repo[0]).query({"query": "no such drama"}, thread_id="t", run_id="r", call_id="c")
    assert result["items"] == [] and result["matched_total"] == 0


def test_same_day_ranked_signal_beats_an_unranked_one():
    from ggwork_pick.selection import _kind_signal

    signals = [{"kind": "kd", "observed_at": "2026-09-21", "rank": 5}, {"kind": "kd", "observed_at": "2026-09-21", "rank": None}]
    assert _kind_signal({"signals": signals}, "kd")["rank"] == 5
    assert _kind_signal({"signals": list(reversed(signals))}, "kd")["rank"] == 5


@pytest.mark.asyncio
async def test_unknown_account_or_signal_kind_is_refused_instead_of_matching_everything(repo):
    from ggwork_pick.selection import SelectionService

    await load(repo, [row(1, posted=1, accounts=["Acc One"]), row(2, posted=0)])
    service = SelectionService(repo[0])
    with pytest.raises(ValueError, match="账号"):
        await service.query({"posted_account": "acc onee"}, thread_id="t", run_id="r", call_id="c1")
    with pytest.raises(ValueError, match="xx"):
        await service.query({"signal_kind": "xx"}, thread_id="t", run_id="r", call_id="c2")
    kept = await service.query({"posted_account": "ACC ONE"}, thread_id="t", run_id="r", call_id="c3")
    assert [i["title"] for i in kept["items"]] == ["Drama 2"]


@pytest.mark.asyncio
async def test_count_honours_a_new_batch_request_like_the_query(repo):
    from ggwork_pick.selection import SelectionService

    await load(repo, [row(1), row(2), row(3)])
    service = SelectionService(repo[0])
    parent = await service.query({"limit": 1}, thread_id="t", run_id="r", call_id="c")
    more = await service.query({"exclude_previous": True}, thread_id="t", run_id="r2", call_id="c2", parent_result_id=parent["id"])
    counted = await service.count({"exclude_previous": True}, parent=await repo[0].result(parent["id"]))
    assert counted["total"] == more["matched_total"] == 2
    with pytest.raises(ValueError, match="换一批"):
        await service.count({"exclude_previous": True})
