"""P2-8a: GET /api/pick/replay re-runs a stored result on its own batch, with the exclusions it froze.

Sources: plan:1615-1621 and plan 2.5 item 4; U35 (no mirror, so mirror_version is null on SQLite) and U36 (another
rule or ranking version is flagged, not refused). Both dialects through the conftest app_client. Synthetic data only.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import null, update

ALICE = {"test-owner": "alice"}
REPLAY_KEYS = {
    "result_id",
    "catalog_batch_id",
    "knowledge_batch_id",
    "mirror_version",
    "limit",
    "total",
    "identities",
    "truncated",
    "shown",
    "excluded_reproducible",
    "ranking_reproducible",
    "unmappable",
    "data_as_of",
}


def _catalog(count: int, *, tag: str = "r") -> bytes:
    # Evidence dates spread over 20 days, so the evidence_date order is not the identity order.
    rows = [
        {
            "source": "synthetic",
            "source_id": f"{tag}-{i:04d}",
            "language": "en",
            "title": f"合成{tag}{i}",
            "theater": "Example",
            "signals": [{"kind": "kd", "source_ref": f"ref:{i}", "observed_at": f"2026-09-{1 + i % 20:02d}", "rank": i}],
        }
        for i in range(1, count + 1)
    ]
    return json.dumps(rows, ensure_ascii=False).encode()


def _posted(accounts: list[str]) -> dict:
    return {"matched": bool(accounts), "records": ["SD-1"] if accounts else [], "post_count": len(accounts), "sched_count": 0, "accounts": accounts}


def _rich_catalog() -> bytes:
    """Rows every agent-only condition can act on: tags, a channel rule, publication records."""
    rows = [
        {
            "source": "synthetic",
            "source_id": f"rich-{i}",
            "language": "en",
            "title": f"合成甲{i}",
            "theater": "Example",
            "tags": ["复仇", "都市"],
            "availability": "active",
            "channel_rules": {"youtube": "allowed"},
            "posted": _posted(["acc-1"] if i == 1 else []),
            "signals": [{"kind": "kd", "source_ref": f"ref:{i}", "observed_at": f"2026-09-0{i}", "rank": i}],
        }
        for i in range(1, 5)
    ]
    return json.dumps(rows, ensure_ascii=False).encode()


def _alice(service):
    from ggwork_pick.repository import PickRepository

    return PickRepository(service.session_factory, "alice")


async def _import(service, payload: bytes, *, shared: bool = False) -> dict:
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    repo = PickRepository.shared(service.session_factory) if shared else _alice(service)
    return await Importer(repo, service.data_dir).catalog(payload, "json")


async def _query(service, filters: dict, call_id: str, **kwargs) -> dict:
    from ggwork_pick.selection import SelectionService

    return await SelectionService(_alice(service)).query(filters, thread_id="t", run_id="r", call_id=call_id, **kwargs)


async def _set(service, result_id: str, **values) -> None:
    from ggwork_pick.models import candidate_sets

    async with service.session_factory() as session, session.begin():
        await session.execute(update(candidate_sets).where(candidate_sets.c.id == result_id).values(**values))


async def _replay(client, result_id: str, headers=ALICE):
    return await client.get("/api/pick/replay", params={"result_id": result_id}, headers=headers)


@pytest.mark.asyncio
async def test_replay_is_owner_scoped(app_client):
    client, service = app_client
    await _import(service, _catalog(3))
    result = await _query(service, {}, "c1")
    assert (await _replay(client, result["id"])).status_code == 200
    assert (await _replay(client, result["id"], {"test-owner": "bob"})).status_code == 404
    assert (await _replay(client, result["id"], {})).status_code == 401
    for owner in ("default", "system:shared"):
        assert (await _replay(client, result["id"], {"test-owner": owner})).status_code == 401
    assert (await _replay(client, "no-such-result")).status_code == 404
    assert (await client.get("/api/pick/replay", headers=ALICE)).status_code == 422


@pytest.mark.asyncio
async def test_replay_gone_when_the_batch_was_pruned(app_client):
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    old = await _import(service, _catalog(3, tag="old"), shared=True)
    result = await _query(service, {}, "c1")
    assert result["catalog_batch_id"] == old["id"]
    await _import(service, _catalog(3, tag="new"), shared=True)
    # Nothing counts as a recent reference: the older shared batch loses its rows.
    await PickRepository.shared(service.session_factory).prune_shared("catalog", 1, referenced_within=timedelta(0))
    assert (await _alice(service).batch_info(old["id"]))["status"] == "pruned"
    response = await _replay(client, result["id"])
    assert response.status_code == 410
    assert result["id"] not in response.text


@pytest.mark.asyncio
async def test_replay_of_an_old_result_says_exclusions_cannot_be_reproduced(app_client):
    client, service = app_client
    await _import(service, _catalog(3))
    result = await _query(service, {}, "c1")
    # Nothing was excluded, and that is recorded: an empty list still reproduces.
    assert (await _alice(service).result(result["id"]))["excluded_json"] == []
    assert (await _replay(client, result["id"])).json()["excluded_reproducible"] is True
    await _set(service, result["id"], excluded_json=null())
    body = (await _replay(client, result["id"])).json()
    assert body["excluded_reproducible"] is False
    assert body["shown"] == [item["identity"] for item in result["items"]]
    # Fewer matches than the card's limit: the stored limit comes back as it was, for the page to highlight by.
    assert body["limit"] == 5 and body["total"] == len(body["shown"]) == 3


@pytest.mark.asyncio
async def test_replay_first_n_equals_the_query(app_client):
    client, service = app_client
    await _import(service, _catalog(12))
    first = await _query(service, {"limit": 3}, "c1")
    # A fourth drama is saved afterwards; 换一批 then leaves out the three it showed and the saved one.
    side = await _query(service, {"limit": 4, "exclude_selected": False}, "c0")
    saved = side["items"][3]
    assert saved["identity"] not in {item["identity"] for item in first["items"]}
    await _alice(service).save_selection("save-1", side["id"], [saved["item_id"]])
    more = await _query(service, {"exclude_previous": True}, "c2", parent_result_id=first["id"])
    excluded = (await _alice(service).result(more["id"]))["excluded_json"]
    assert excluded == sorted([saved["identity"], *(item["identity"] for item in first["items"])])
    body = (await _replay(client, more["id"])).json()
    assert set(body) == REPLAY_KEYS
    assert body["shown"] == [item["identity"] for item in more["items"]]
    assert body["total"] == more["matched_total"] == 8 and len(body["identities"]) == 8
    assert not set(excluded) & set(body["identities"])
    assert body["identities"][: body["limit"]] == body["shown"] and body["limit"] == 3
    assert (body["result_id"], body["catalog_batch_id"], body["knowledge_batch_id"]) == (more["id"], more["catalog_batch_id"], more["knowledge_batch_id"])
    assert body["excluded_reproducible"] is True and body["ranking_reproducible"] is True and body["truncated"] is False
    # 换一批 is redone through the exclusion list only: the data page has no filter for either exclusion.
    assert body["unmappable"] == ["exclude_selected", "exclude_previous"]
    # No mirror here (always so on SQLite, U35): the version is null, data_as_of the one the result froze.
    record = await _alice(service).result(more["id"])
    assert body["mirror_version"] is None and body["data_as_of"] == record["data_as_of_json"] is not None
    # The first card replays as it was, although a drama it matched is saved now: replay uses what it froze.
    again = (await _replay(client, first["id"])).json()
    assert again["shown"] == [item["identity"] for item in first["items"]] and again["total"] == 12
    assert saved["identity"] in again["identities"]


@pytest.mark.asyncio
async def test_replay_lists_the_conditions_the_data_page_cannot_express(app_client):
    client, service = app_client
    await _import(service, _rich_catalog())
    agent_only = await _query(service, {"tags": ["复仇"], "posted_account": "acc-1", "channel": "youtube", "query": "合成"}, "c1")
    body = (await _replay(client, agent_only["id"])).json()
    assert body["unmappable"] == ["tags", "posted_account", "channel", "confirmed_eligible_only", "query", "exclude_selected"]
    assert body["shown"] == [item["identity"] for item in agent_only["items"]] and body["total"] == 3
    mappable = {"exclude_selected": False, "theater": "Example", "language": "en", "signal_kind": "kd", "exclude_posted": True}
    plain = await _query(service, mappable, "c2")
    assert (await _replay(client, plain["id"])).json()["unmappable"] == []
    # confirmed_eligible_only is listed only when it filters: set, and with a channel.
    loose = await _query(service, {"channel": "youtube", "confirmed_eligible_only": False, "exclude_selected": False}, "c3")
    assert (await _replay(client, loose["id"])).json()["unmappable"] == ["channel"]


@pytest.mark.asyncio
async def test_replay_truncates_long_lists(app_client):
    client, service = app_client
    await _import(service, _catalog(2003))
    result = await _query(service, {"limit": 5}, "c1")
    body = (await _replay(client, result["id"])).json()
    assert body["total"] == 2003 and body["truncated"] is True and len(body["identities"]) == 2000
    assert body["shown"] == [item["identity"] for item in result["items"]] == body["identities"][:5]


@pytest.mark.asyncio
async def test_replay_truncates_only_past_the_limit(app_client, monkeypatch):
    from ggwork_pick import selection

    client, service = app_client
    monkeypatch.setattr(selection, "REPLAY_LIMIT", 3)
    await _import(service, _catalog(3, tag="a"))
    exact = (await _replay(client, (await _query(service, {}, "c1"))["id"])).json()
    assert (exact["total"], len(exact["identities"]), exact["truncated"]) == (3, 3, False)
    await _import(service, _catalog(4, tag="b"))
    over = (await _replay(client, (await _query(service, {}, "c2"))["id"])).json()
    assert (over["total"], len(over["identities"]), over["truncated"]) == (4, 3, True)


@pytest.mark.asyncio
async def test_replay_under_another_rule_or_ranking_version_is_flagged_not_refused(app_client):
    client, service = app_client
    await _import(service, _catalog(4))
    result = await _query(service, {"signal_kind": "kd", "sort": "rank"}, "c1")
    assert (await _replay(client, result["id"])).json()["ranking_reproducible"] is True
    await _set(service, result["id"], rule_version="pick-rules-v0")
    response = await _replay(client, result["id"])
    assert response.status_code == 200 and response.json()["ranking_reproducible"] is False
    # A known ranking version, but not the one this code gives these conditions.
    await _set(service, result["id"], rule_version="pick-rules-v1", ranking_version="evidence-date-v1")
    response = await _replay(client, result["id"])
    assert response.status_code == 200 and response.json()["ranking_reproducible"] is False


@pytest.mark.asyncio
async def test_replay_of_conditions_this_code_refuses_is_a_conflict(app_client):
    client, service = app_client
    await _import(service, _catalog(2))
    result = await _query(service, {}, "c1")
    stored = (await _alice(service).result(result["id"]))["conditions_json"]
    await _set(service, result["id"], conditions_json={**stored, "removed_filter": "秘密值"})
    response = await _replay(client, result["id"])
    assert response.status_code == 409 and "秘密值" not in response.text
    # Conditions that validate but that matching refuses: ranked without a board.
    await _set(service, result["id"], conditions_json={**stored, "sort": "rank"})
    assert (await _replay(client, result["id"])).status_code == 409


@pytest.mark.asyncio
async def test_replay_of_a_value_a_later_check_refuses_reruns_instead_of_conflicting(app_client):
    """09-28's theater="US" results were stored before theaters were checked: they replay to their zero, not a 409."""
    client, service = app_client
    await _import(service, _catalog(3))
    result = await _query(service, {}, "c1")
    stored = (await _alice(service).result(result["id"]))["conditions_json"]
    for call_id, value in (("c2", {"theater": "US"}), ("c3", {"language": "USA"}), ("c4", {"tags": ["美国"]}), ("c5", {"query": "US"})):
        with pytest.raises(ValueError):
            await _query(service, value, call_id)
        await _set(service, result["id"], conditions_json={**stored, **value}, ordered_items_json=[])
        response = await _replay(client, result["id"])
        assert response.status_code == 200 and response.json()["total"] == 0, value


@pytest.mark.asyncio
async def test_replay_of_a_hot_only_result_is_its_query(app_client):
    client, service = app_client
    rows = json.loads(_catalog(4))
    rows[0]["signals"] = [{"kind": "clk", "source_ref": "ref:clk", "observed_at": "2026-09-28"}]
    await _import(service, json.dumps(rows, ensure_ascii=False).encode())
    result = await _query(service, {"hot_only": True, "limit": 2}, "c1")
    body = (await _replay(client, result["id"])).json()
    assert body["total"] == result["matched_total"] == 3 and body["ranking_reproducible"] is True
    assert body["shown"] == [item["identity"] for item in result["items"]]
    assert body["unmappable"] == ["exclude_selected", "hot_only"]


@pytest.mark.asyncio
async def test_replay_code_bugs_stay_errors_not_a_not_found(app_client, monkeypatch):
    from ggwork_pick import selection

    client, service = app_client
    await _import(service, _catalog(2))
    result = await _query(service, {}, "c1")

    def broken(record, rows):
        return {}["identities"]

    monkeypatch.setattr(selection, "replay_view", broken)
    with pytest.raises(KeyError):
        await _replay(client, result["id"])


@pytest.mark.asyncio
async def test_replay_errors_outside_the_rerun_are_not_a_conflict(app_client, monkeypatch):
    """Only the re-run itself answers 409; a ValueError elsewhere (a corrupt JSON column, say) stays a 500."""
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    await _import(service, _catalog(2))
    result = await _query(service, {}, "c1")

    async def corrupt(self, record):
        raise json.JSONDecodeError("corrupt", "", 0)

    monkeypatch.setattr(PickRepository, "frozen_data_as_of", corrupt)
    with pytest.raises(json.JSONDecodeError):
        await _replay(client, result["id"])
