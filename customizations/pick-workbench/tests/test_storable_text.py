"""Text that reaches the tables without passing StrictInput (plan 6.7, the host row).

Request bodies are checked by StrictInput, and in production the pick entrypoint's JSON body sanitizer has
already replaced NUL and lone surrogates. Neither sees feed metadata, ids the model provider assigns or text the
model writes. Each such writer stores the text with U+FFFD in place of the character, or the whole input is
refused before the database. Every test runs on both dialects through an engine that serializes JSON like the
host's (engines.host_engine), so a lone surrogate fails here exactly as it would in production.
"""

import json

import httpx
import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_realshort_sync import TOKEN, feed_row

NUL = chr(0)
LONE_SURROGATE = chr(0xD800)
REPLACEMENT = chr(0xFFFD)
UNSTORABLE = pytest.mark.parametrize("bad", [NUL, LONE_SURROGATE], ids=["nul", "lone-surrogate"])
CATALOG = b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]'


@pytest_asyncio.fixture
async def service(pick_db_url, tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield svc
    await engine.dispose()


def _feed(rows, **page):
    """One feed page. json.dumps escapes NUL and lone surrogates, as a JavaScript feed sends them."""
    content = json.dumps(
        {"ok": True, "version": "pick-feed-v1", "capturedAt": "2026-09-23T03:00:00.000Z", "total": len(rows), "rows": rows, "nextCursor": None, **page}
    ).encode()

    def handler(request):
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, content=content)

    return httpx.MockTransport(handler)


async def _run_sync(service, transport) -> dict:
    from ggwork_pick.sync import RealShortSync

    return await RealShortSync(service, base_url="https://realshort.test", token=TOKEN, transport=transport).run("cron")


def _alice(service):
    from ggwork_pick.repository import PickRepository

    return PickRepository(service.session_factory, "alice")


# ---- the RealShort feed ----


@UNSTORABLE
@pytest.mark.asyncio
async def test_feed_metadata_is_stored_with_replacements(service, bad):
    # Only rows pass DramaInput; the page's capture time, scope, freshness and revision go straight to the batch.
    page = {"capturedAt": f"2026-09-23T03:00:00{bad}Z", "scope": f"scope{bad}", "freshness": {f"at{bad}": f"v{bad}"}, "sourceRevision": f"rev{bad}"}
    first = await _run_sync(service, _feed([feed_row(1)], **page))
    assert first["status"] == "success", first["error"]
    # The same content again takes the reuse path, which rewrites the metadata.
    again = await _run_sync(service, _feed([feed_row(1)], **page))
    assert again["status"] == "success", again["error"]
    assert again["catalog_batch_id"] == first["catalog_batch_id"]
    as_of = f"2026-09-23T03:00:00{REPLACEMENT}Z"
    assert first["source_as_of"] == again["source_as_of"] == as_of
    catalog = await _alice(service).current_batch("catalog")
    assert catalog["source_as_of"] == as_of
    assert catalog["validation_json"]["scope"] == f"scope{REPLACEMENT}"
    assert catalog["validation_json"]["freshness"] == {f"at{REPLACEMENT}": f"v{REPLACEMENT}"}
    assert catalog["validation_json"]["source_revision"] == f"rev{REPLACEMENT}"


@pytest.mark.asyncio
async def test_feed_rows_refuse_nul_and_replace_lone_surrogates(service):
    refused = await _run_sync(service, _feed([feed_row(1, title=f"a{NUL}b")]))
    assert refused["status"] == "failed" and "unstorable_text" in refused["error"]
    assert await _alice(service).current_batch("catalog") is None
    # A string cut mid-emoji upstream: sync.py encodes rows with errors="replace".
    cut = await _run_sync(service, _feed([feed_row(1, title=f"a{LONE_SURROGATE}b")]))
    assert cut["status"] == "success", cut["error"]
    assert [row["title"] for row in await _alice(service).catalog_rows(cut["catalog_batch_id"])] == ["a?b"]


@UNSTORABLE
@pytest.mark.asyncio
async def test_feed_rules_with_unstorable_text_fail_the_sync_before_publishing(service, bad):
    from ggwork_pick.repository import PickRepository

    good = await _run_sync(service, _feed([feed_row(1)], rules="# 规则"))
    refused = await _run_sync(service, _feed([feed_row(2)], rules=f"# 规则{bad}"))
    assert refused["status"] == "failed"
    alice = _alice(service)
    assert (await alice.current_batch("catalog"))["id"] == good["catalog_batch_id"]
    assert (await alice.current_batch("knowledge"))["id"] == good["knowledge_batch_id"]
    assert (await PickRepository.shared(service.session_factory).sync_runs())[0]["error"] == refused["error"]


@UNSTORABLE
@pytest.mark.asyncio
async def test_sync_run_outcomes_are_stored_with_replacements(service, bad):
    from ggwork_pick.repository import PickRepository

    shared = PickRepository.shared(service.session_factory)
    run = await shared.start_sync_run("realshort", "manual")
    finished = await shared.finish_sync_run(run["id"], status="failed", error=f"boom{bad}", source_as_of=f"t{bad}")
    assert (finished["error"], finished["source_as_of"]) == (f"boom{REPLACEMENT}", f"t{REPLACEMENT}")
    assert (await shared.sync_runs())[0]["error"] == f"boom{REPLACEMENT}"


# ---- ids and text from the model provider ----


@UNSTORABLE
@pytest.mark.asyncio
async def test_a_tool_call_id_is_stored_with_replacements(service, bad):
    from ggwork_pick.imports import Importer
    from ggwork_pick.selection import SelectionService

    repo = _alice(service)
    await Importer(repo, service.data_dir).catalog(CATALOG, "json")
    selection = SelectionService(repo)
    first = await selection.query({}, thread_id="t", run_id="r", call_id=f"call{bad}")
    # A retried call finds its own snapshot instead of writing a second one.
    assert await selection.query({}, thread_id="t", run_id="r", call_id=f"call{bad}") == first
    assert (await repo.result_for_call("r", f"call{bad}"))["tool_call_id"] == f"call{REPLACEMENT}"
    assert [row["id"] for row in await repo.results("t")] == [first["id"]]


@UNSTORABLE
@pytest.mark.asyncio
async def test_a_result_id_the_model_invents_is_not_found(service, bad):
    # The model passes result ids to pick_get_drama_detail and pick_prepare_selection; the lookup must not reach the driver.
    with pytest.raises(LookupError):
        await _alice(service).result(f"result{bad}")


@UNSTORABLE
@pytest.mark.asyncio
async def test_answer_check_notes_and_message_ids_are_stored_with_replacements(service, bad):
    from ggwork_pick.answer_check import check_answer

    notes = check_answer(f"推荐《Invented{bad}》。", known_titles=set(), posted_checked=True)
    assert f"《Invented{bad}》" in notes[0]
    repo = _alice(service)
    for _ in range(2):  # a retried model call records the same answer once
        await repo.record_answer_check(thread_id="t", run_id="r", message_id=f"msg{bad}", notes=notes)
    [check] = await repo.answer_checks("t")
    assert check["message_id"] == f"msg{REPLACEMENT}"
    assert check["notes"] == [note.replace(bad, REPLACEMENT) for note in notes]


def test_thread_ids_are_refused_by_the_host_before_a_run_starts():
    # Candidate snapshots and answer checks are read back by thread id; the host only lets ASCII ids through.
    from deerflow.utils.thread_id import validate_thread_id

    for bad in (NUL, LONE_SURROGATE):
        with pytest.raises(ValueError):
            validate_thread_id(f"thread{bad}")


def test_storable_replaces_keys_and_values_and_keeps_surrogate_pairs():
    from ggwork_pick.contracts import storable

    pair = json.loads('"\\ud83c\\udfac"')
    value = {f"k{NUL}": [f"a{LONE_SURROGATE}", {"n": 1, "p": pair}], "t": (f"x{chr(0xDFFF)}",), "none": None}
    assert storable(value) == {f"k{REPLACEMENT}": [f"a{REPLACEMENT}", {"n": 1, "p": pair}], "t": (f"x{REPLACEMENT}",), "none": None}
    assert value[f"k{NUL}"][0] == f"a{LONE_SURROGATE}"
