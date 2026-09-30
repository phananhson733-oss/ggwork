"""pick_search_knowledge on the one document production imports.

fixtures/realshort_rules.md is realshort-rules.md as RealShort's feedRulesMarkdown (src/lib/pick/feed-map.ts, main
358b1ab) renders it for capturedAt 2026-09-23T00:00:00.000Z: the '## 信号种类' list near the top names most theaters,
and each theater's own section comes after the first 1,900 characters. An excerpt anchored on the earliest hit returned
the list and cut the sections off, so theater rules came back unknown or from the neighbouring theater.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

RULES = (Path(__file__).parent / "fixtures" / "realshort_rules.md").read_text(encoding="utf-8")
# ToolOutputBudgetMiddleware externalizes a result over 12,000 characters (its default externalize_min_chars): it reaches
# the model as a head/tail preview, and the pick agent has no read_file to open the rest. The tool keeps its whole
# serialized result within this, below the host's limit.
RESULT_LIMIT = 10_000
THEATERS = ("ReelShort", "KalosTV", "ShortMax", "FlickReels", "StarShort", "GoodShort", "DramaBox", "MoboReels", "flareflow", "TouchShort")


def _section(name: str) -> str:
    """The theater's section as the document has it: its heading through its last line."""
    start = RULES.index(f"## {name}\n")
    end = RULES.find("\n\n## ", start)
    return RULES[start : end if end != -1 else len(RULES)]


def test_the_fixture_has_the_production_layout():
    assert 2_700 < len(RULES) < 3_000
    assert RULES.index("dbn：DramaBox") < 500 and RULES.index("mg：MoboReels") < 500
    assert all(RULES.index(f"## {name}\n") > RULES.index("## 信号种类") for name in THEATERS)
    assert RULES.index("## DramaBox") > RULES.index("DramaBox") + 1_500
    assert RULES.index("## MoboReels") > RULES.index("MoboReels") + 1_600
    assert "必带标签：—" in _section("DramaBox") and "YouTube：禁 YouTube；不能在 YouTube 推广" in _section("MoboReels")


@pytest_asyncio.fixture
async def searcher(tmp_path):
    """Import a knowledge bundle of (text, filename, source_ref) and return the raw pick_search_knowledge output for a query."""
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import search_knowledge_tool

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))

    async def load(bundle):
        importer = Importer(PickRepository(service.session_factory, "alice"), service.data_dir)
        await importer.catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json")
        await importer.knowledge_bundle([(body.encode(), filename, source_ref) for body, filename, source_ref in bundle])
        store = ExtensionData("task")
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
        runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call")

        async def run(query: str) -> str:
            return await search_knowledge_tool.coroutine(query=query, runtime=runtime)

        return run

    yield load
    await engine.dispose()


@pytest_asyncio.fixture
async def search(searcher):
    run = await searcher([(RULES, "realshort-rules.md", "realshort:feed-v1/rules")])

    async def documents(query: str) -> list[dict]:
        return json.loads(await run(query))["documents"]

    return documents


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "theater"),
    [
        ("DramaBox", "DramaBox"),
        ("MoboReels", "MoboReels"),
        ("DramaBox 必带标签", "DramaBox"),
        ("flareflow YouTube", "flareflow"),
        ("flareflow 规则 YouTube 报备 必带标签", "flareflow"),
        ("TouchShort", "TouchShort"),
        ("TouchShort 报备", "TouchShort"),
        ("YouTube 报备 必带标签 规则", "TouchShort"),
    ],
)
async def test_a_theater_query_returns_that_theaters_section(search, query, theater):
    documents = await search(query)
    assert len(documents) == 1
    assert _section(theater) in documents[0]["excerpt"]


@pytest.mark.asyncio
async def test_the_rules_document_comes_back_whole_with_its_citation(search):
    (found,) = await search("DramaBox")
    assert found["excerpt"] == RULES
    assert found["line_start"] == 1 and found["citation_id"] == found["document_id"] + ":0"
    assert found["title"] == "realshort-rules.md" and found["source_ref"] == "realshort:feed-v1/rules"


@pytest.mark.asyncio
async def test_a_longer_document_is_excerpted_at_the_theaters_heading(search, monkeypatch):
    from ggwork_pick import knowledge_excerpts

    monkeypatch.setattr(knowledge_excerpts, "WHOLE_DOCUMENT_CHARS", 1_000)
    (found,) = await search("DramaBox 必带标签")
    assert found["excerpt"] == _section("DramaBox")
    start = RULES.index("## DramaBox")
    assert found["citation_id"].endswith(f":{start}") and found["line_start"] == RULES.count("\n", 0, start) + 1
    (found,) = await search("flareflow YouTube")
    assert found["excerpt"] == _section("flareflow")
    both = await search("MoboReels DramaBox")
    assert [doc["excerpt"] for doc in both] == [_section("DramaBox"), _section("MoboReels")]
    for query, theater in (("flareflow 规则 YouTube 报备 必带标签", "flareflow"), ("TouchShort", "TouchShort")):
        (found,) = await search(query)
        assert found["excerpt"] == _section(theater), query


@pytest.mark.asyncio
async def test_a_longer_document_without_a_named_heading_keeps_the_earliest_hit_window(search, monkeypatch):
    from ggwork_pick import knowledge_excerpts

    monkeypatch.setattr(knowledge_excerpts, "WHOLE_DOCUMENT_CHARS", 1_000)
    (found,) = await search("解禁通道")
    start = RULES.index("解禁通道") - 100
    assert found["excerpt"] == RULES[start : start + knowledge_excerpts.EXCERPT_CHARS]


def test_the_innermost_named_section_wins_and_a_long_one_is_cut():
    from ggwork_pick.knowledge_excerpts import EXCERPT_CHARS, excerpt_spans

    text = "# Rules for DramaBox and others\n\n" + "intro line\n" * 400 + "## DramaBox\n" + "x" * 3_000 + "\n\n## Other\nother rules\n"
    start = text.index("## DramaBox")
    assert excerpt_spans(text, ["dramabox"]) == [(start, start + EXCERPT_CHARS)]
    assert excerpt_spans(text, ["rules"]) == [(0, EXCERPT_CHARS)]
    other = text.index("## Other")
    assert excerpt_spans(text, ["other"]) == [(other, len(text.rstrip()))]


def test_nested_sections_end_at_the_next_heading_of_their_level_or_above():
    from ggwork_pick.knowledge_excerpts import excerpt_spans

    pad = "p" * 5_000
    text = f"# Top\n{pad}\n## Alpha\na1\n### Alpha Deep\nd1\n#### Alpha Deeper\nd2\n## Beta\nb1\n# Next\nn1"
    spans = {word: [text[start:end] for start, end in excerpt_spans(text, [word])] for word in ("alpha", "deep", "beta", "next")}
    assert spans["alpha"] == ["#### Alpha Deeper\nd2"]
    assert spans["deep"] == ["#### Alpha Deeper\nd2"]
    assert spans["beta"] == ["## Beta\nb1"]
    assert spans["next"] == ["# Next\nn1"]
    # Closing hashes and trailing spaces do not hide a heading.
    closed = pad + "\n## Gamma ##  \ng1"
    assert [closed[start:end] for start, end in excerpt_spans(closed, ["gamma"])] == ["## Gamma ##  \ng1"]


def test_a_document_with_many_named_headings_returns_a_bounded_number_of_excerpts():
    from ggwork_pick.knowledge_excerpts import MAX_EXCERPTS, excerpt_spans

    text = "".join(f"## Theater {index}\nrule {index}\n\n" for index in range(50_000))
    spans = excerpt_spans(text, ["theater"])
    assert len(spans) == MAX_EXCERPTS
    assert [text[start:end] for start, end in spans] == [f"## Theater {index}\nrule {index}" for index in range(MAX_EXCERPTS)]


def test_a_rule_under_every_theater_comes_from_the_theater_the_query_names(monkeypatch):
    from ggwork_pick import knowledge_excerpts

    monkeypatch.setattr(knowledge_excerpts, "WHOLE_DOCUMENT_CHARS", 1_000)
    text = RULES.replace("\nYouTube：", "\n### YouTube\nYouTube：")

    def excerpts(query: str) -> list[str]:
        return [text[start:end] for start, end in knowledge_excerpts.excerpt_spans(text, query.casefold().split())]

    def youtube(theater: str) -> str:
        start = text.index("### YouTube", text.index(f"## {theater}\n"))
        end = text.find("\n\n## ", start)
        return text[start : end if end != -1 else len(text)].rstrip()

    # Every theater has a "### YouTube": the one under the theater named wins over the first five in the document.
    assert excerpts("DramaBox YouTube") == [youtube("DramaBox")]
    assert excerpts("flareflow 规则 YouTube 报备 必带标签") == [youtube("flareflow")]
    assert excerpts("DramaBox") == [_section("DramaBox").replace("\nYouTube：", "\n### YouTube\nYouTube：")]
    assert excerpts("YouTube") == [youtube(theater) for theater in THEATERS[:5]]


@pytest.mark.asyncio
async def test_every_matching_document_gets_an_excerpt_before_one_gets_a_second(searcher):
    from ggwork_pick.knowledge_excerpts import MAX_EXCERPTS

    sections = "".join(f"## Theater {index}\n" + "规则说明。\n" * 200 + "\n" for index in range(8))
    run = await searcher(
        [
            ("# 长规则\n\n" + sections, "long.md", "upload:long"),
            (RULES, "realshort-rules.md", "realshort:feed-v1/rules"),
            ("Theater 备注\n" + "规则备注。\n" * 600, "notes.md", "upload:notes"),
        ]
    )
    raw = await run("theater 规则")
    documents = json.loads(raw)["documents"]
    assert {doc["title"] for doc in documents} == {"long.md", "realshort-rules.md", "notes.md"}
    assert len(documents) <= MAX_EXCERPTS and len(raw) <= RESULT_LIMIT


@pytest.mark.asyncio
async def test_documents_that_fit_whole_alone_are_cut_to_keep_the_result_under_the_host_limit(searcher):
    bundle = [(f"# 规则 {index}\n" + "每一行都是一条较长的规则说明文字。\n" * 210, f"doc{index}.md", f"upload:doc{index}") for index in range(5)]
    assert all(len(body) <= 4_000 for body, _, _ in bundle)
    raw = await (await searcher(bundle))("规则")
    documents = json.loads(raw)["documents"]
    assert sorted(doc["title"] for doc in documents) == [f"doc{index}.md" for index in range(5)]
    assert len(raw) <= RESULT_LIMIT
    for doc in documents:
        assert doc["excerpt"].startswith("# 规则 ")


@pytest.mark.asyncio
async def test_titles_and_sources_at_their_limits_return_fewer_entries_under_the_host_limit(searcher):
    bundle = [(f"# 规则 {index}\n" + "规则说明。\n" * 600, f"{'长' * 495}{index}.md", f"upload:{'x' * 2_030}{index}") for index in range(5)]
    raw = await (await searcher(bundle))("规则")
    documents = json.loads(raw)["documents"]
    assert 0 < len(documents) < 5 and len(raw) <= RESULT_LIMIT
    assert all(doc["excerpt"].startswith("# 规则 ") and len(doc["excerpt"]) > 100 for doc in documents)


@pytest.mark.asyncio
async def test_the_json_around_the_entries_counts_toward_the_result_limit(searcher):
    # Quotes escape to two characters each; five documents that fill their shares leave no slack for the wrapper.
    bundle = [('"' * 5_000, f"doc{index}.md", f"upload:doc{index}") for index in range(5)]
    raw = await (await searcher(bundle))("")
    assert len(json.loads(raw)["documents"]) == 5
    assert len(raw) <= RESULT_LIMIT


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_ref", "title"])
async def test_metadata_that_escapes_past_the_limit_is_cut_instead_of_emptying_the_result(searcher, field):
    control = chr(1)
    title, source_ref = (f"{control * 495}.md", "upload:doc") if field == "title" else ("doc.md", control * 2_048)
    raw = await (await searcher([("# doc\n规则内容。\n", title, source_ref)]))("doc")
    assert len(raw) <= RESULT_LIMIT
    (document,) = json.loads(raw)["documents"]
    assert document["excerpt"] == "# doc\n规则内容。\n"
    kept = document[field]
    assert kept.startswith(control * 10) and kept.endswith("…") and len(json.dumps(kept)) < len(json.dumps(title if field == "title" else source_ref))


@pytest.mark.asyncio
async def test_metadata_too_long_for_any_excerpt_gets_a_notice_not_a_silent_empty_result(searcher, monkeypatch):
    import ggwork_pick.tools as tools

    monkeypatch.setattr(tools, "_KNOWLEDGE_SOURCE_CHARS", 20_000)
    raw = await (await searcher([("# doc\n规则内容。\n", "doc.md", chr(1) * 2_048)]))("doc")
    result = json.loads(raw)
    assert result["documents"] == [] and "过长" in result["notice"]
