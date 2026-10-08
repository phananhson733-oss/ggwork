"""Public owner checks and honest private synthetic catalogs (no mirror provenance)."""

import json

import pytest


@pytest.mark.asyncio
async def test_query_api_auth_private_catalog_and_source_identity(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    repo = PickRepository(service.session_factory, "alice")
    rows = [
        {
            "source": "synthetic",
            "source_id": "exact-123",
            "language": "en",
            "title": "合成中文剧名",
            "theater": "Example",
            "availability": "active",
            "signals": [],
        }
    ]
    await Importer(repo, service.data_dir).catalog(json.dumps(rows).encode(), "json")
    body = {"domain": "catalog", "scope": "full_catalog", "query": "exact-123"}
    assert (await client.post("/api/pick/query", json=body)).status_code == 401
    response = await client.post("/api/pick/query", headers={"test-owner": "alice"}, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rows"][0]["drama"]["source"] == "synthetic"
    assert result["pin"]["mirror_version"] is None
    assert result["board"] is None
    assert result["counts"] == {"total": 1, "matched": 1, "returned": 1, "excluded": {}}
    assert result["rows"][0]["posted_status"] == "unknown"
    denied = await client.post("/api/pick/query", headers={"test-owner": "bob"}, json={**body, "pin": result["pin"]})
    assert denied.status_code == 404
    history = await client.post(
        "/api/pick/query",
        headers={"test-owner": "alice"},
        json={**body, "domain": "rankings", "rank": "kd", "period": {"kind": "daily", "value": "2026-09-01"}},
    )
    assert history.status_code == 503
    assert history.json()["detail"]["code"] == "source_unavailable"


@pytest.mark.asyncio
async def test_legacy_tool_queries_shared_service_exact_source_id_and_default_exclusion(app_client):
    from types import SimpleNamespace

    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.tools import query_candidates_tool

    _, service = app_client
    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).catalog(
        json.dumps([{"source": "synthetic", "source_id": "stable-id", "language": "en", "title": "Synthetic exact title"}]).encode(), "json"
    )
    store = ExtensionData("task")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="query-1")
    result = json.loads(await query_candidates_tool.coroutine(filters={"query": "stable-id"}, runtime=runtime))
    assert result["matched_total"] == 1
    assert result["conditions"]["exclude_selected"] is True
    await repo.save_selection("save", result["id"], [result["items"][0]["item_id"]])
    runtime.tool_call_id = "query-2"
    second = json.loads(await query_candidates_tool.coroutine(filters={"query": "stable-id"}, runtime=runtime))
    assert second["matched_total"] == 0


@pytest.mark.asyncio
async def test_common_tool_binds_run_pin_and_rejects_model_changed_pin(app_client):
    from types import SimpleNamespace

    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    import ggwork_pick.tools as tools
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    _, service = app_client
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(
        b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json"
    )
    store = ExtensionData("task")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="common-1")
    tool = getattr(tools, "query_data_tool", None)
    assert tool is not None, "Agent must have the same common-query entrypoint as the board"
    reply = json.loads(await tool.coroutine(query={"domain": "catalog", "scope": "full_catalog"}, runtime=runtime))
    assert reply["counts"]["matched"] == 1
    changed = {**reply["pin"], "catalog_batch_id": "foreign"}
    refused = json.loads(await tool.coroutine(query={"domain": "catalog", "scope": "full_catalog", "pin": changed}, runtime=runtime))
    assert refused["status"] == "rejected"


def test_scoped_publication_keeps_archives_and_missing_details_unknown():
    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.query_posted import publication_truth

    req = CommonQuery(domain="catalog", scope="full_catalog", account="Account A", published_from="2026-09-01", published_to="2026-09-30")
    record = {"archived": True, "post_count": 1, "sched_count": 0, "posts": [{"st": "已公开", "acct": "Account A", "d": "2026-09-02"}]}
    assert publication_truth([record], req) == ("posted", True)
    assert publication_truth([record], req.model_copy(update={"account": "Account B"})) == ("not_posted", True)
    assert publication_truth([], req) == ("unknown", False)
    assert publication_truth([{**record, "posts": []}], req) == ("unknown", False)
    assert publication_truth([record], req.model_copy(update={"channel": "youtube"})) == ("unknown", False)


def test_common_query_evidence_uses_tool_receipt_without_inventing_saved_results():
    from ggwork_pick.answer_evidence import AnswerEvidence

    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_data",
        "read1",
        {"counts": {"matched": 2}, "request": {"domain": "catalog"}, "pin": {"catalog_batch_id": "b"}, "source_as_of": "2026-09-01", "rows": []},
    )
    assert len(evidence.reads) == 1
    assert evidence.reads[0].result_id is None
    assert any(a.value == "2" and a.reference == "tool:read1" for a in evidence.atoms)


def test_common_failure_invalidates_older_count_evidence():
    from ggwork_pick.answer_evidence import AnswerEvidence

    evidence = AnswerEvidence()
    evidence.capture("pick_query_data", "read1", {"counts": {"matched": 4}, "request": {"domain": "catalog"}, "pin": {"catalog_batch_id": "b"}, "rows": []})
    evidence.capture("pick_query_data", "read2", {"status": "unavailable"})
    assert next(a for a in reversed(evidence.atoms) if a.field_name == "matched_total").value is None


@pytest.mark.asyncio
async def test_private_query_honors_off_signal_and_posted_filters(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    common = {"source": "synthetic", "language": "en", "theater": "Example"}
    rows = [
        {
            **common,
            "source_id": "off",
            "title": "Off",
            "availability": "delisted",
            "signals": [{"kind": "kd", "source_ref": "fixture:off", "observed_at": "2026-09-01"}],
        },
        {**common, "source_id": "plain", "title": "Plain", "availability": "active", "posted": {"matched": True, "post_count": 1}},
        {**common, "source_id": "none", "title": "None", "availability": "active", "posted": {"matched": True, "post_count": 0}},
    ]
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(json.dumps(rows).encode(), "json")
    base = {"domain": "catalog", "scope": "full_catalog"}

    async def read(**changes):
        response = await client.post("/api/pick/query", headers={"test-owner": "alice"}, json={**base, **changes})
        assert response.status_code == 200, response.text
        return [r["drama"]["source_id"] for r in response.json()["rows"]]

    assert set(await read(with_off=True)) == {"off", "plain", "none"}
    assert await read(signal_only=True, with_off=True) == ["off"]
    assert await read(posted_filter="yes") == ["plain"]
    assert await read(posted_filter="no") == ["none"]


@pytest.mark.asyncio
async def test_private_pin_rejects_fabricated_knowledge(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(
        b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json"
    )
    body = {"domain": "catalog", "scope": "full_catalog"}
    reply = (await client.post("/api/pick/query", headers={"test-owner": "alice"}, json=body)).json()
    response = await client.post("/api/pick/query", headers={"test-owner": "alice"}, json={**body, "pin": {**reply["pin"], "knowledge_batch_id": "invented"}})
    assert response.status_code == 404


def test_query_calendar_and_source_publication_unknown_dates():
    from pydantic import ValidationError

    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.query_posted import publication_truth

    with pytest.raises(ValidationError):
        CommonQuery(domain="posted", scope="full_catalog", published_from="2026-02-30")
    request = CommonQuery(domain="catalog", scope="full_catalog", published_from="2026-09-01")
    record = {"post_count": 1, "sched_count": 0, "posts": [{"st": "已公开", "acct": "A", "d": "TBD"}]}
    assert publication_truth([record], request) == ("unknown", False)


def test_common_rank_evidence_uses_actual_period_and_tool_citation():
    from ggwork_pick.answer_evidence import AnswerEvidence

    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_data",
        "read-rank",
        {
            "counts": {"matched": 1},
            "request": {"domain": "rankings", "rank": "kd"},
            "pin": {"catalog_batch_id": "b"},
            "rows": [
                {
                    "identity": "synthetic-1",
                    "drama": {"title": "Synthetic", "signals": [{"kind": "kd", "observed_at": "2026-09-02", "rank": 3, "source_ref": "mirror:1:kd"}]},
                }
            ],
        },
    )
    rank = next(a for a in evidence.atoms if a.field_name == "kd.rank")
    assert rank.value == "3" and rank.observed_at == "2026-09-02"
    assert rank.reference.startswith("tool:read-rank:")
    assert all(r.result_id is None for r in evidence.reads)


def test_common_evidence_duplicate_title_requires_encoded_explicit_source():
    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.answer_evidence import AnswerEvidence
    from ggwork_pick.contracts import DramaInput

    evidence = AnswerEvidence()
    rows = []
    for source_id in ("one", "two"):
        drama = DramaInput(
            source="synthetic",
            source_id=source_id,
            language="en",
            title="Same",
            signals=[{"kind": "kd", "rank": 3, "observed_at": "2026-09-02", "source_ref": "https://example.test/" + source_id}],
        )
        rows.append({"identity": drama.identity, "drama": drama.model_dump(mode="json")})
    evidence.capture("pick_query_data", "r", {"counts": {"matched": 2}, "request": {"domain": "rankings"}, "pin": {"catalog_batch_id": "b"}, "rows": rows})

    def check(text):
        return build_checked_publication(text, evidence=evidence, thread_id="t", run_id="r", message_id="m")

    assert check("《Same》的kd名次为3。").status == "incomplete"
    atom = next(a for a in evidence.atoms if a.field_name == "kd.rank")
    assert check(f"{atom.claim} [{atom.reference}]。").status == "confirmed"


@pytest.mark.asyncio
async def test_private_unknown_filter_is_safe_invalid_query(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(
        b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json"
    )
    response = await client.post("/api/pick/query", headers={"test-owner": "alice"}, json={"domain": "catalog", "scope": "full_catalog", "theater": "not-real"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_query"


@pytest.mark.parametrize("field,label", [("source", "来源"), ("source_id", "来源编号"), ("language", "语种"), ("theater", "剧场")])
def test_common_evidence_does_not_certify_prose_inside_source_fields(field, label):
    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.answer_evidence import AnswerEvidence
    from ggwork_pick.contracts import DramaInput

    drama = DramaInput(**{"source": "synthetic", "source_id": "id", "language": "en", "title": "甲", field: "X 保证盈利"})
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_data",
        "c",
        {
            "counts": {"matched": 1},
            "request": {"domain": "catalog"},
            "pin": {"catalog_batch_id": "b"},
            "rows": [{"identity": drama.identity, "drama": drama.model_dump(mode="json")}],
        },
    )
    result = build_checked_publication(f"《甲》的{label}为X 保证盈利。", evidence=evidence, thread_id="t", run_id="r", message_id="m")
    assert result.status == "incomplete"


@pytest.mark.parametrize(
    "claim", ["本次榜单期次为2026-09-02", "本次资料来源时点为2026-09-02", "本次镜像版本为1", "本次规则版本为mirror-rules-v1", "本次查询范围为完整剧库"]
)
def test_common_failed_read_invalidates_all_current_scope_claims(claim):
    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.answer_evidence import AnswerEvidence

    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_data",
        "old",
        {
            "counts": {"matched": 1},
            "request": {"domain": "rankings", "scope": "full_catalog"},
            "pin": {"catalog_batch_id": "b", "mirror_version": 1, "rule_version": "mirror-rules-v1"},
            "actual_period": {"value": "2026-09-02"},
            "source_as_of": "2026-09-02",
            "rows": [],
        },
    )
    evidence.capture("pick_query_data", "failed", {"status": "rejected"})

    def check(text):
        return build_checked_publication(text, evidence=evidence, thread_id="t", run_id="r", message_id="m")

    assert check(claim + "。").status == "incomplete"
    assert check(claim + " [tool:old]。").status == "confirmed"


@pytest.mark.parametrize("value", ["20260901", "2026-W36-2", "2026-02-30", "TBD"])
def test_noncanonical_publication_dates_remain_unknown(value):
    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.query_posted import publication_truth

    request = CommonQuery(domain="catalog", scope="full_catalog", account="A", published_from="2026-09-01", published_to="2026-09-30")
    assert publication_truth([{"post_count": 1, "sched_count": 0, "posts": [{"st": "已公开", "acct": "A", "d": value}]}], request) == ("unknown", False)


@pytest.mark.parametrize("account", [None, True, ["A"], {"name": "A"}, " "])
def test_unknown_account_mapping_never_proves_scoped_absence(account):
    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.query_posted import publication_truth

    request = CommonQuery(domain="catalog", scope="full_catalog", account="A", published_from="2026-09-01")
    assert publication_truth([{"post_count": 1, "sched_count": 0, "posts": [{"st": "已公开", "acct": account, "d": "2026-09-02"}]}], request) == (
        "unknown",
        False,
    )
