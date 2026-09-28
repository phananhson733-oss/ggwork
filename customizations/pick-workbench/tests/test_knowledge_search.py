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
async def search(tmp_path):
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import search_knowledge_tool

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    importer = Importer(PickRepository(service.session_factory, "alice"), service.data_dir)
    await importer.catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json")
    await importer.knowledge_bundle([(RULES.encode(), "realshort-rules.md", "realshort:feed-v1/rules")])
    store = ExtensionData("task")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call")

    async def run(query: str) -> list[dict]:
        return json.loads(await search_knowledge_tool.coroutine(query=query, runtime=runtime))["documents"]

    yield run
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "theater"),
    [
        ("DramaBox", "DramaBox"),
        ("MoboReels", "MoboReels"),
        ("DramaBox 必带标签", "DramaBox"),
        ("flareflow YouTube", "flareflow"),
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
