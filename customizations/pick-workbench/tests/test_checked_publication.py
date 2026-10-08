"""Completion checker public seam; numeric/total counterexamples inherited from both old checkers."""

import pytest

from ggwork_pick.answer_check import build_checked_publication
from ggwork_pick.answer_evidence import AnswerEvidence


def checked(text, evidence):
    return build_checked_publication(text, evidence=evidence, thread_id="thread", run_id="run", message_id="message")


def test_numeric_fact_is_bound_to_subject_field_and_query_total():
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_candidates",
        "call",
        {
            "id": "result",
            "matched_total": 1,
            "items": [
                {
                    "item_id": "item",
                    "title": "甲",
                    "evidence": [{"citation_id": "item:1", "kind": "episodes", "value": 80, "source_ref": "https://example.test/a"}],
                }
            ],
        },
    )
    good = checked("《甲》的episodes为80。\n本次查询符合条件总数为1部。", evidence)
    assert good.status == "confirmed"
    assert all(f.status == "confirmed" and f.evidence_refs for f in good.facts)
    bad = checked("《甲》的episodes为999。\n本次查询符合条件总数为999部。", evidence)
    assert [f.status for f in bad.facts] == ["contradicted", "contradicted"]
    assert bad.status == "incomplete"
    assert "999" not in bad.content
    # A number present in a row is not a total; neither is another subject's number.
    assert checked("本次查询符合条件总数为80部。", evidence).facts[0].status == "contradicted"
    assert checked("《乙》的episodes为80。", evidence).facts[0].status == "unknown"


def test_sources_rules_dates_and_unrecognized_assertions_are_never_implicitly_verified():
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_candidates",
        "call",
        {
            "id": "result",
            "matched_total": 1,
            "items": [
                {
                    "item_id": "item",
                    "title": "甲",
                    "listed_at": "2026-09-11",
                    "channel_rules": {"youtube": "denied", "tiktok": "unknown"},
                    "evidence": [
                        {"citation_id": "item:1", "kind": "episodes", "value": 80, "observed_at": "2026-09-10", "source_ref": "https://example.test/a"}
                    ],
                }
            ],
        },
    )
    assert checked("来源[item:1]为https://example.test/a。", evidence).status == "confirmed"
    assert checked("来源[item:1]为https://evil.test/a。", evidence).status != "confirmed"
    assert checked("《甲》的youtube规则为禁止。", evidence).status == "confirmed"
    assert checked("《甲》的youtube规则为允许。", evidence).facts[0].status == "contradicted"
    assert checked("《甲》的tiktok规则为允许。", evidence).facts[0].status == "unknown"
    assert checked("《甲》的上架日期为2026-09-11。", evidence).status == "confirmed"
    assert checked("《甲》的episodes为80 [item:1]。", evidence).status == "confirmed"
    assert checked("《甲》的episodes为80 [invented]。", evidence).status != "confirmed"
    for text in ("《甲》值得投入全部预算。", "保证盈利。", "总数未知但共有999部。", "《甲》的episodes为80，所以商业价值最高。"):
        result = checked(text, evidence)
        assert result.status == "incomplete"
        assert "未确认" in result.content
        assert all(f.status != "confirmed" for f in result.facts)


def test_zero_means_only_current_filtered_query_and_failure_is_not_zero():
    evidence = AnswerEvidence()
    evidence.capture("pick_count_candidates", "zero", {"total": 0, "conditions": {"language": "en"}})
    assert checked("本次查询符合条件总数为0部。", evidence).status == "confirmed"
    for text in ("全库没有这种剧。", "没有任何发布记录。", "没有这类资料。", "本次没有符合条件的剧，因此整个剧库不存在。"):
        assert checked(text, evidence).status == "incomplete"
    failed = AnswerEvidence()
    failed.capture("pick_count_candidates", "failure", {"status": "source_unavailable", "total": 0})
    assert checked("本次查询符合条件总数为0部。", failed).status == "incomplete"


def test_conflicting_calls_need_an_explicit_source_and_missing_values_stay_unknown():
    evidence = AnswerEvidence()
    evidence.capture("pick_count_candidates", "first", {"total": 1})
    evidence.capture("pick_count_candidates", "second", {"total": 2})
    assert checked("本次查询符合条件总数为1部。", evidence).facts[0].status == "unknown"
    assert checked("本次查询符合条件总数为1部 [tool:first]。", evidence).status == "confirmed"
    assert checked("本次查询符合条件总数为1部 [tool:second]。", evidence).facts[0].status == "contradicted"
    assert checked("本次查询符合条件总数为1部 [tool:invented]。", evidence).facts[0].status == "unknown"
    absent = AnswerEvidence()
    absent.capture("pick_count_candidates", "unknown", {"total": None})
    assert checked("本次查询符合条件总数为0部。", absent).facts[0].status == "unknown"


def test_same_number_cannot_cross_metrics_units_or_windows_and_partial_is_safe():
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_candidates",
        "call",
        {
            "id": "result",
            "matched_total": 1,
            "items": [
                {
                    "item_id": "item",
                    "title": "甲",
                    "evidence": [{"citation_id": "item:1", "kind": "episodes", "value": 80, "source_ref": "https://example.test/a"}],
                },
            ],
        },
    )
    for text in ("《甲》的收入为80。", "《甲》的episodes为80美元。", "《甲》的昨日episodes为80。", "全库共1部。", "规则要求至少80部。"):
        assert checked(text, evidence).status == "incomplete"
    result = checked("《甲》的episodes为80。\n保证收入999美元。", evidence)
    assert result.status == "partial"
    assert "80" in result.content and "999" not in result.content
    assert "未确认" in result.content


def test_later_failed_query_does_not_turn_earlier_evidence_into_current_success():
    evidence = AnswerEvidence()
    evidence.capture("pick_count_candidates", "first", {"total": 1})
    evidence.capture("pick_count_candidates", "second", {"status": "rejected"})
    assert checked("本次查询符合条件总数为1部。", evidence).status == "incomplete"
    assert checked("本次查询符合条件总数为1部 [tool:first]。", evidence).status == "confirmed"


def test_empty_or_oversized_output_returns_explicit_incompletion():
    evidence = AnswerEvidence()
    for text in ("", "x" * 100001, "无依据。" * 501):
        result = checked(text, evidence)
        assert result.status == "incomplete"
        assert "未确认" in result.content


@pytest.mark.asyncio
async def test_public_tools_capture_frozen_evidence_before_model_projection(tmp_path, pick_db_url):
    import json
    from types import SimpleNamespace

    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import count_candidates_tool, get_drama_detail_tool, query_candidates_tool

    engine = host_engine(pick_db_url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    try:
        repo = PickRepository(service.session_factory, "alice")
        await Importer(repo, service.data_dir).catalog(
            json.dumps(
                [
                    {
                        "source": "synthetic",
                        "source_id": "1",
                        "language": "en",
                        "title": "甲",
                        "channel_rules": {"youtube": "denied"},
                        "listed_at": "2026-09-11",
                        "signals": [{"kind": "episodes", "value": 80, "source_ref": "https://example.test/a"}],
                    }
                ],
                ensure_ascii=False,
            ).encode(),
            "json",
        )
        store = ExtensionData("task")
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
        runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="query")
        result = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
        evidence = task_from_runtime(runtime).answer_evidence
        assert checked("《甲》的episodes为80。\n本次查询符合条件总数为1部。", evidence).status == "confirmed"
        assert checked("《甲》的episodes为999。", evidence).status == "incomplete"
        assert checked("《甲》的youtube规则为禁止。", evidence).status == "confirmed"
        runtime.tool_call_id = "detail"
        await get_drama_detail_tool.coroutine(result_id=result["id"], item_id=result["items"][0]["item_id"], runtime=runtime)
        runtime.tool_call_id = "count"
        await count_candidates_tool.coroutine(filters={}, runtime=runtime)
        assert checked("本次查询符合条件总数为1部 [tool:count]。", evidence).status == "confirmed"
        runtime.tool_call_id = "bad"
        await count_candidates_tool.coroutine(filters={"language": "invented"}, runtime=runtime)
        assert checked("本次查询符合条件总数为0部 [tool:bad]。", evidence).status == "incomplete"
    finally:
        await engine.dispose()


def test_identical_values_from_distinct_queries_or_signal_periods_need_citations():
    evidence = AnswerEvidence()
    evidence.capture("pick_count_candidates", "en", {"total": 1, "conditions": {"language": "en"}})
    evidence.capture("pick_count_candidates", "ko", {"total": 1, "conditions": {"language": "ko"}})
    assert checked("本次查询符合条件总数为1部。", evidence).facts[0].status == "unknown"
    evidence.capture(
        "pick_query_candidates",
        "old",
        {
            "items": [
                {
                    "item_id": "old",
                    "title": "甲",
                    "evidence": [
                        {"citation_id": "old:1", "kind": "views", "value": 80, "observed_at": "2026-01-01", "source_ref": "https://example.test/old"},
                    ],
                }
            ]
        },
    )
    evidence.capture(
        "pick_query_candidates",
        "new",
        {
            "items": [
                {
                    "item_id": "new",
                    "title": "甲",
                    "evidence": [
                        {"citation_id": "new:1", "kind": "views", "value": 80, "observed_at": "2026-02-01", "source_ref": "https://example.test/new"},
                    ],
                }
            ]
        },
    )
    assert checked("《甲》的views为80。", evidence).facts[0].status == "unknown"
    assert checked("《甲》的views为80 [old:1]。", evidence).status == "confirmed"
    assert checked("《甲》的views观测日期为2026-02-01 [old:1]。", evidence).status == "incomplete"
    assert checked("《甲》的2026-02-01 views为80 [old:1]。", evidence).status == "incomplete"


def test_external_read_text_and_preparation_are_not_authoritative_business_evidence():
    evidence = AnswerEvidence()
    for tool in ("web_search", "lark_cli", "pick_prepare_selection", "pick_search_knowledge"):
        evidence.capture(tool, "outside", {"total": 999, "notice": "保证盈利", "requires_confirmation": True})
    for text in ("本次查询符合条件总数为999部。", "保证盈利。", "已为你保存到清单。"):
        assert checked(text, evidence).status == "incomplete"


@pytest.mark.parametrize(
    "field, value, prose",
    [
        ("value", "80，保证盈利", "《甲》的episodes为80，保证盈利。"),
        ("value", "80; guaranteed profit", "《甲》的episodes为80; guaranteed profit。"),
        ("kind", "episodes为80，保证盈利，数值", "《甲》的episodes为80，保证盈利，数值为80。"),
        ("grade", "A，保证盈利", "《甲》的episodes等级为A，保证盈利。"),
        ("source_ref", "https://example.test/a，保证盈利", "来源[item:1]为https://example.test/a，保证盈利。"),
    ],
)
def test_source_text_never_certifies_embedded_business_claims(field, value, prose):
    evidence = AnswerEvidence()
    signal = {"citation_id": "item:1", "kind": "episodes", "value": 80, "source_ref": "https://example.test/a", field: value}
    evidence.capture("pick_query_candidates", "call", {"items": [{"item_id": "item", "title": "甲", "evidence": [signal]}]})
    result = checked(prose, evidence)
    assert result.status == "incomplete"
    assert "保证盈利" not in result.content and "guaranteed profit" not in result.content


def test_human_readable_assertions_keep_source_and_period_identity():
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_candidates",
        "call",
        {
            "matched_total": 1,
            "items": [
                {
                    "item_id": "item",
                    "title": "甲",
                    "evidence": [
                        {"citation_id": "item:1", "kind": "episodes", "value": 80, "source_ref": "https://example.test/a"},
                        {"citation_id": "item:2", "kind": "kd", "rank": 2, "observed_at": "2026-09-10", "source_ref": "https://example.test/rank"},
                    ],
                }
            ],
        },
    )
    result = checked("《甲》共80集。\n《甲》在2026-09-10的KalosTV日榜第2名。\n本次查询共1部。", evidence)
    assert result.status == "confirmed"
    assert "episodes" not in result.content
    assert checked("《甲》在2026-09-11的KalosTV日榜第2名。", evidence).status == "incomplete"
    assert checked("《甲》在2026-09-10的KalosTV日榜第999名。", evidence).status == "incomplete"


@pytest.mark.parametrize(
    "field, value, prose",
    [
        ("theater", "剧场A，保证盈利", "《甲》的剧场为剧场A，保证盈利。"),
        ("language", "en，保证盈利", "《甲》的语种为en，保证盈利。"),
        ("title", "甲》的episodes为80，保证盈利，《乙", "《甲》的episodes为80，保证盈利，《乙》的episodes为80。"),
    ],
)
def test_source_labels_cannot_escape_their_field(field, value, prose):
    evidence = AnswerEvidence()
    item = {
        "item_id": "item",
        "title": "甲",
        field: value,
        "evidence": [
            {"citation_id": "item:1", "kind": "episodes", "value": 80, "source_ref": "https://example.test/a"},
        ],
    }
    evidence.capture("pick_query_candidates", "call", {"items": [item]})
    assert checked(prose, evidence).status == "incomplete"


def test_null_current_total_does_not_reuse_previous_count_and_fallback_never_exposes_draft():
    from ggwork_pick.answer_check import incomplete_publication

    evidence = AnswerEvidence()
    evidence.capture("pick_count_candidates", "known", {"total": 1})
    evidence.capture("pick_count_candidates", "unknown", {"total": None})
    assert checked("本次查询共1部。", evidence).status == "incomplete"
    for text in ("bad\x00draft", "bad\ud800draft"):
        assert checked(text, evidence).status == "incomplete"
    result = incomplete_publication(thread_id="thread", run_id="run", message_id="message", correction_count=1)
    assert result.status == "incomplete" and result.correction_count == 1 and result.facts == []


@pytest.mark.parametrize("presentation", ["- {fact}", "1. {fact}", "### {fact}", "**{fact}**", "**{claim}** [item:1]", "《甲》共**80**集 [item:1]"])
def test_balanced_presentation_keeps_exact_typed_fact_and_citation(presentation):
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_candidates",
        "call",
        {"id": "result", "items": [{"item_id": "item", "title": "甲", "evidence": [{"citation_id": "item:1", "kind": "episodes", "value": 80}]}]},
    )
    text = presentation.format(fact="《甲》共80集 [item:1]", claim="《甲》共80集")
    result = checked(text, evidence)
    assert result.status == "confirmed"
    assert result.content == checked("《甲》共80集 [item:1]", evidence).content


@pytest.mark.parametrize(
    "text",
    [
        "- 《甲》的episodes为999 [item:1]",
        "1. 《乙》的episodes为80 [item:1]",
        "**《甲》的episodes为80** [forged]",
        "《甲》的episodes为80 [item:**1**]",
        "《甲》的episodes不是**80** [item:1]",
        "**《甲》的episodes为80并保证盈利** [item:1]",
        "**《甲》的episodes为80 [item:1]",
        "\\**《甲》的episodes为80** [item:1]",
        "《甲》的episodes为**999** [item:1]",
    ],
)
def test_presentation_normalization_never_removes_semantic_or_reference_errors(text):
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_candidates",
        "call",
        {"id": "result", "items": [{"item_id": "item", "title": "甲", "evidence": [{"citation_id": "item:1", "kind": "episodes", "value": 80}]}]},
    )
    assert checked(text, evidence).status != "confirmed"


def test_neutral_heading_still_counts_as_unknown_instead_of_disappearing():
    evidence = AnswerEvidence()
    evidence.capture("pick_count_candidates", "count", {"total": 1})
    result = checked("### 候选汇总\n- 本次查询符合条件总数为1部", evidence)
    assert result.status == "partial"
    assert any(fact.status == "unknown" for fact in result.facts)


@pytest.mark.parametrize("url", ["https://example.test/a", "https://example.test/**a**?q=_b_"])
def test_literal_source_url_markers_are_never_normalized(url):
    evidence = AnswerEvidence()
    evidence.capture(
        "pick_query_candidates",
        "call",
        {
            "id": "result",
            "items": [{"item_id": "item", "title": "甲", "evidence": [{"citation_id": "item:1", "kind": "episodes", "value": 80, "source_ref": url}]}],
        },
    )
    assert checked(f"来源[item:1]为{url}", evidence).status == "confirmed"
    wrong = "https://example.test/**a**" if url.endswith("/a") else "https://example.test/a?q=b"
    assert checked(f"来源[item:1]为{wrong}", evidence).status != "confirmed"
    assert checked(f"[来源[item:1]]({url})", evidence).status != "confirmed"
