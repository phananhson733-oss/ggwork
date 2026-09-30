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
async def test_one_document_with_long_metadata_does_not_crowd_out_the_others(searcher):
    long_ref = "upload:" + "x" * 2_040
    notes = ("# DramaBox 备注\n" + "备注内容。\n" * 30, "notes.md", long_ref)
    raw = await (await searcher([(RULES, "realshort-rules.md", "realshort:feed-v1/rules"), notes]))("DramaBox")
    documents = {doc["title"]: doc for doc in json.loads(raw)["documents"]}
    assert set(documents) == {"realshort-rules.md", "notes.md"} and len(raw) <= RESULT_LIMIT
    # Each entry pays for its own source ref: the rules document still comes back whole.
    assert documents["realshort-rules.md"]["excerpt"] == RULES and "truncated" not in documents["realshort-rules.md"]
    assert documents["notes.md"]["source_ref"] == long_ref
    # Five documents, one of them with a long source ref: all five still get an excerpt.
    refs = [long_ref, *(f"upload:doc{index}" for index in range(1, 5))]
    bundle = [(f"# 规则 {index}\n" + "规则说明。\n" * 600, f"doc{index}.md", ref) for index, ref in enumerate(refs)]
    raw = await (await searcher(bundle))("规则")
    assert sorted(doc["title"] for doc in json.loads(raw)["documents"]) == [f"doc{index}.md" for index in range(5)]
    assert len(raw) <= RESULT_LIMIT


@pytest.mark.asyncio
async def test_what_the_limits_leave_out_is_counted(searcher):
    # Seven matching documents: five come back, and the other two are counted.
    bundle = [(f"# 规则 {index}\n规则内容。\n", f"doc{index}.md", f"upload:doc{index}") for index in range(7)]
    result = json.loads(await (await searcher(bundle))("规则"))
    assert len(result["documents"]) == 5 and result["omitted"] == 2
    # One document with eight named sections: five excerpts come back, and the other three sections are counted.
    sections = "# 长规则\n\n" + "".join(f"## Theater {index}\n" + "规则说明。\n" * 200 + "\n" for index in range(8))
    raw = await (await searcher([(sections, "long.md", "upload:long")]))("theater")
    result = json.loads(raw)
    assert [doc["excerpt"].split("\n", 1)[0] for doc in result["documents"]] == [f"## Theater {index}" for index in range(5)]
    assert result["omitted"] == 3 and len(raw) <= RESULT_LIMIT


@pytest.mark.asyncio
async def test_nothing_left_out_adds_no_count(searcher):
    result = json.loads(await (await searcher([(RULES, "realshort-rules.md", "realshort:feed-v1/rules")]))("DramaBox"))
    assert "omitted" not in result
    assert len(result["documents"]) == 1 and "truncated" not in result["documents"][0]


@pytest.mark.asyncio
async def test_an_excerpt_cut_to_the_result_limit_says_so(searcher):
    bundle = [(f"# 规则 {index}\n" + "每一行都是一条较长的规则说明文字。\n" * 210, f"doc{index}.md", f"upload:doc{index}") for index in range(5)]
    raw = await (await searcher(bundle))("规则")
    documents = json.loads(raw)["documents"]
    assert len(documents) == 5 and all(doc["truncated"] is True for doc in documents) and len(raw) <= RESULT_LIMIT


@pytest.mark.asyncio
async def test_an_excerpt_cut_to_its_length_says_so(search, monkeypatch):
    from ggwork_pick import knowledge_excerpts

    # Cut to EXCERPT_CHARS, from a window or a section; a whole section is not cut.
    monkeypatch.setattr(knowledge_excerpts, "WHOLE_DOCUMENT_CHARS", 1_000)
    (window,) = await search("解禁通道")
    assert window["truncated"] is True
    (section,) = await search("DramaBox 必带标签")
    assert section["excerpt"] == _section("DramaBox") and "truncated" not in section


@pytest.mark.asyncio
async def test_a_document_whose_entry_does_not_fit_is_counted(searcher):
    # Each entry's source ref and title escape to their caps: two entries leave the excerpts too little.
    control = chr(1)
    bundle = [("# 规则\n规则内容。\n", f"{control * 495}{index}.md", f"{control * 2_040}{index}") for index in range(2)]
    raw = await (await searcher(bundle))("规则")
    result = json.loads(raw)
    assert len(result["documents"]) == 1 and result["omitted"] == 1 and len(raw) <= RESULT_LIMIT


def test_each_section_says_whether_it_is_cut():
    from ggwork_pick.knowledge_excerpts import choose_excerpts

    long_section = "规则说明。\n" * 400
    text = "# 长规则\n\n## Theater 0\n" + long_section + "\n## Theater 1\n规则说明。\n\n## Theater 2\n" + long_section
    chosen, omitted = choose_excerpts([text], ["theater"], 9_000, [300])
    assert [found.truncated for found in chosen] == [True, False, True] and omitted == 0
    assert text[chosen[1].start : chosen[1].end] == "## Theater 1\n规则说明。"


@pytest.mark.parametrize("word", ["Straße", "İstanbul", "ﬁnance"])
def test_a_word_only_casefolding_finds_still_places_the_window(word):
    from ggwork_pick.knowledge_excerpts import EXCERPT_CHARS, excerpt_spans

    # The query word folds to more than it is written ("strasse"): the text writes it the way the query does, after
    # characters that fold to two, so the hit lies further on in the casefolded text.
    text = "ß" * 500 + "\n" + "填充内容。\n" * 1_500 + f"{word} 规则在这里。\n" + "其余内容。\n" * 600
    start = text.index(word) - 100
    assert excerpt_spans(text, [word.casefold()]) == [(start, start + EXCERPT_CHARS)]


@pytest.mark.parametrize("wide", ["İ", "ß", "ﬁ"])
def test_the_window_starts_at_the_hit_when_casefolding_changes_lengths(wide):
    from ggwork_pick.knowledge_excerpts import EXCERPT_CHARS, excerpt_spans

    # Each of these casefolds to more than one character: a hit found in the casefolded text lies further on.
    text = wide * 2_000 + "\n" + "填充内容。\n" * 600 + "Needle 规则在这里。\n" + "其余内容。\n" * 600
    start = text.index("Needle") - 100
    assert excerpt_spans(text, ["needle"]) == [(start, start + EXCERPT_CHARS)]


def test_the_window_starts_at_the_earliest_hit_of_any_word():
    from ggwork_pick.knowledge_excerpts import EXCERPT_CHARS, excerpt_spans

    # "strasse" matches "Straße" only once casefolded; "needle" matches later as written.
    text = "填充内容。\n" * 30 + "Straße 规则在这里。\n" + "填充内容。\n" * 1_500 + "needle\n" + "其余内容。\n" * 600
    start = text.index("Straße") - 100
    assert excerpt_spans(text, ["strasse", "needle"]) == [(start, start + EXCERPT_CHARS)]


def test_absent_words_cost_about_one_casefolded_search_each():
    import time

    from ggwork_pick.knowledge_excerpts import _first_hit

    def best_of_three(call):
        times = []
        for _ in range(3):
            began = time.perf_counter()
            call()
            times.append(time.perf_counter() - began)
        return min(times)

    # Uploads reach 25MB and this runs on the event loop. Theater names share no first letter, so a case-insensitive
    # alternation of them gets no literal prefix to skip ahead by and tries every word at every character.
    text = "lorem ipsum dolor sit amet consectetur. " * 50_000
    words = ["kalostv", "reelshort", "dramabox", "flickreels", "goodshort", "shortmax", "moboreels", "touchshort", "flareflow", "starshort"]

    def casefolded_search():
        folded = text.casefold()
        return [folded.find(word) for word in words]

    assert _first_hit(text, words) == 0
    assert best_of_three(lambda: _first_hit(text, words)) < 3 * best_of_three(casefolded_search) + 0.01


@pytest.mark.asyncio
async def test_metadata_too_long_for_any_excerpt_gets_a_notice_not_a_silent_empty_result(searcher, monkeypatch):
    import ggwork_pick.tools as tools

    monkeypatch.setattr(tools, "_KNOWLEDGE_SOURCE_CHARS", 20_000)
    raw = await (await searcher([("# doc\n规则内容。\n", "doc.md", chr(1) * 2_048)]))("doc")
    result = json.loads(raw)
    assert result["documents"] == [] and "过长" in result["notice"]
