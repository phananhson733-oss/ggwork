"""The weekly pan check in docs/pick-workbench/supabase.md section 6: pan-check.sql, pan-redact.sql and the grep for raw files.

Until RealShort #67 scrubs v1 feed notes, a note holding a pan link is stored verbatim and copied onward: every catalog batch
the sync keeps, candidate snapshots, saved selections, and the host's checkpoints of the conversation that showed it. The
runbook's scripts run here as files from docs/, through psql, logged in as a stand-in for deerflow_app: not a superuser, owner
of the deerflow schema and every table in it. The contaminated data is written by the real sync, selection and repository
code; the host tables come from the host's own table definitions and langgraph's PostgresSaver.setup(). The PostgreSQL half
skips when PICK_TEST_PG_URL is unset.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pg
import pytest
import pytest_asyncio
from engines import HOST_JSON_SERIALIZER, host_engine
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_realshort_sync import TOKEN, feed_row, feed_transport

DOCS = Path(__file__).resolve().parents[3] / "docs/pick-workbench"
CHECK = DOCS / "supabase/pan-check.sql"
REDACT = DOCS / "supabase/pan-redact.sql"
RUNBOOK = DOCS / "supabase.md"
PLACEHOLDER = "[网盘信息已移除]"
# pan-check.sql's owner for a thread the DELETE route cannot find (require_existing): the runbook stops there.
NO_META = "(没有 threads_meta 行)"
# The runbook's broad pattern before this change; the scripts must keep matching all of it.
OLD_PATTERN = r"pan\.baidu|pan\.quark|aliyundrive|alipan|115\.com|123pan|lanzou|drive\.uc\.cn|cloud\.189\.cn|pan\.xunlei|提取码|提取碼|访问码|訪問碼|pwd="
LOCATIONS = [
    "ggwp_drama_versions.payload_json",
    "ggwp_candidate_sets.ordered_items_json",
    "ggwp_candidate_sets.conditions_json",
    "ggwp_selections.snapshot_json",
    "ggwp_selection_commands.receipt_json",
    "ggwp_answer_checks.notes_json",
    "ggwp_import_batches.validation_json",
    "ggwp_knowledge_versions.metadata_json",
    "ggwp_knowledge_versions.text",
]
# (table, key columns, JSON column): every JSON column of the ggwp tables.
JSON_COLUMNS = [
    ("ggwp_drama_versions", "batch_id, identity", "payload_json"),
    ("ggwp_candidate_sets", "id", "ordered_items_json"),
    ("ggwp_candidate_sets", "id", "conditions_json"),
    ("ggwp_selections", "id", "snapshot_json"),
    ("ggwp_selection_commands", "owner_id, request_id", "receipt_json"),
    ("ggwp_answer_checks", "id", "notes_json"),
    ("ggwp_import_batches", "id", "validation_json"),
    ("ggwp_knowledge_versions", "batch_id, document_id", "metadata_json"),
]
REDACT_OUTPUT = ["BEGIN", "CREATE FUNCTION", *["UPDATE n"] * len(JSON_COLUMNS), "DROP FUNCTION", "COMMIT"]

# A note quoting a link, with escaped quotes and backslashes in the same string; the strings next to it hold escapes too
# (grade ends in a backslash right before note's opening quote) and must come through byte for byte.
LINK_NOTE = 'He said "see PAN.baidu.com/s/1AbC?pwd=x7k2", saved to C:\\dl\\'
CODE_NOTE = "访问码：8k2p，速存"
QUOTED_LABEL = 'KalosTV "日榜" 第1\\2'
TRAILING_BACKSLASH = "A\\"
# Near misses: 密码 without a separator is a common word in titles.
CLEAN_TITLE = "财富密码"
CLEAN_NOTE = "密码学入门，见 pan 字样也不算"


def _pattern(text: str) -> str:
    [pattern] = re.findall(r"^SELECT '([^']*)' AS pan \\gset$", text, re.M)
    return pattern


def _runbook_greps() -> list[str]:
    return re.findall(r"^ *grep -rliE (?:--null )?'([^']*)' /data/pick", RUNBOOK.read_text(encoding="utf-8"), re.M)


def test_the_scripts_and_the_runbook_grep_use_one_pattern_that_keeps_the_old_one():
    pattern = _pattern(CHECK.read_text(encoding="utf-8"))
    assert _pattern(REDACT.read_text(encoding="utf-8")) == pattern
    greps = _runbook_greps()
    assert len(greps) == 3 and set(greps) == {pattern}
    assert set(OLD_PATTERN.split("|")) <= set(pattern.split("|"))
    # The old inline queries are gone: in the runbook the pattern appears only in those three commands.
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert runbook.count(pattern) == 3 and OLD_PATTERN not in runbook


# ---- a database like production: ggwp tables at head, the host's tables, all owned by a deerflow_app stand-in ----


def _libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _create_host_tables(url: str) -> None:
    from deerflow.persistence.models.run_event import RunEventRow
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.user.model import UserRow
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(_libpq(url)) as saver:
        saver.setup()
    engine = create_engine(make_url(url).set(drivername="postgresql+psycopg"), json_serializer=HOST_JSON_SERIALIZER)
    try:
        with engine.begin() as conn:
            for table in (UserRow.__table__, ThreadMetaRow.__table__, RunRow.__table__, RunEventRow.__table__):
                table.create(conn)
    finally:
        engine.dispose()


def _admin(url: str, *statements: str) -> None:
    import psycopg

    with psycopg.connect(_libpq(url), autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


@dataclass(frozen=True)
class Workbench:
    url: str
    owner: str
    password: str
    psql: str

    def fetch(self, statement: str, *params) -> list[tuple]:
        import psycopg

        with psycopg.connect(_libpq(self.url)) as conn:
            return conn.execute(statement, params).fetchall()

    def run(self, script: Path) -> str:
        """psql -f, logged in as the owner; any error or warning fails the test."""
        url = make_url(self.url)
        env = {key: value for key, value in os.environ.items() if not key.startswith("PG")} | {
            "PGHOST": url.host,
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": url.database,
            "PGUSER": self.owner,
            "PGPASSWORD": self.password,
            "PGCLIENTENCODING": "UTF8",
            "PGCONNECT_TIMEOUT": "10",
        }
        command = [self.psql, "-X", "-w", "-A", "-F", "|", "-P", "footer=off", "-f", str(script)]
        result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", timeout=120)
        assert result.returncode == 0, result.stdout
        assert "ERROR" not in result.stdout and "WARNING" not in result.stdout, result.stdout
        return result.stdout

    def check(self) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
        """pan-check.sql's two tables: rows with a hit per location, and threads with a hit, their owner and where the hit is."""
        lines = self.run(CHECK).splitlines()
        assert lines[0] == "BEGIN" and lines[-1] == "COMMIT", lines
        header_counts, header_threads = lines.index("location|rows"), lines.index("thread_id|owner|found_in")
        counts = [line.split("|") for line in lines[header_counts + 1 : header_threads]]
        threads = [tuple(line.split("|")) for line in lines[header_threads + 1 : -1]]
        assert [location for location, _ in counts] == LOCATIONS
        return {location: int(rows) for location, rows in counts}, threads

    def redact(self) -> list[int]:
        """pan-redact.sql's UPDATE counts, in script order; the output is exactly what the runbook shows."""
        lines = self.run(REDACT).splitlines()
        assert [re.sub(r"^UPDATE \d+$", "UPDATE n", line) for line in lines] == REDACT_OUTPUT, lines
        return [int(line.split()[1]) for line in lines if line.startswith("UPDATE ")]

    def json_columns(self) -> dict[tuple, str]:
        """Every ggwp JSON value as stored, byte for byte."""
        stored = {}
        for table, keys, column in JSON_COLUMNS:
            for *key, text in self.fetch(f"SELECT {keys}, {column}::text FROM deerflow.{table}"):
                stored[(table, column, *key)] = text
        return stored

    def checkpoint_digest(self) -> list:
        tables = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
        return [self.fetch(f"SELECT count(*), md5(string_agg(t::text, '' ORDER BY t::text)) FROM deerflow.{name} AS t")[0] for name in tables]


@pytest.fixture
def workbench(pg_cluster, pg_template, pg_reader_role):
    psql = shutil.which("psql")
    if psql is None:
        pytest.fail("the runbook's scripts are psql scripts: put psql on PATH")
    owner, password = pg.unique_name("pan_owner"), secrets.token_hex(16)
    admin_url = pg_cluster.async_url(pg_cluster.url.database)
    # Created before the database and dropped after it, which takes the ownership along.
    _admin(admin_url, f"CREATE ROLE {owner} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{password}'")
    name = pg.unique_name("t")
    try:
        pg_cluster.create_database(name, template=pg_template)
        url = pg_cluster.async_url(name)
        _create_host_tables(url)
        _admin(
            url,
            f"ALTER SCHEMA deerflow OWNER TO {owner}",
            "DO $$ DECLARE t text; BEGIN FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'deerflow' LOOP"
            f" EXECUTE format('ALTER TABLE deerflow.%I OWNER TO {owner}', t); END LOOP; END $$",
        )
        yield Workbench(url, owner, password, psql)
    finally:
        pg_cluster.drop_database(name)
        _admin(admin_url, f"DROP ROLE IF EXISTS {owner}")


@pytest_asyncio.fixture
async def service(workbench, tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(workbench.url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield svc
    await engine.dispose()


async def _sync(service, rows, **kwargs) -> dict:
    from ggwork_pick.sync import RealShortSync

    outcome = await RealShortSync(service, base_url="https://realshort.test", token=TOKEN, transport=feed_transport(rows), **kwargs).run("manual")
    assert outcome["status"] == "success", outcome
    return outcome


def _signal(i, **fields):
    return {"kind": "kd", "label": "KalosTV 日榜", "source_ref": f"ref:{i}", "observed_at": "2026-09-20", "rank": i, "grade": "", "note": "", **fields}


def _contaminated_feed() -> list[dict]:
    return [
        feed_row(1, signals=[_signal(1, label=QUOTED_LABEL, grade=TRAILING_BACKSLASH, note=LINK_NOTE)]),
        feed_row(2, signals=[_signal(2, note=CODE_NOTE)]),
        feed_row(3, title=CLEAN_TITLE, signals=[_signal(3, note=CLEAN_NOTE)]),
        feed_row(4),
    ]


def _redacted(text: str, *hits: str) -> str:
    """What pan-redact.sql must leave: each string holding a hit replaced whole, every other byte as it was."""
    for hit in hits:
        text = text.replace(json.dumps(hit, ensure_ascii=False), json.dumps(PLACEHOLDER, ensure_ascii=False))
    return text


def _notes(row: dict) -> list[str]:
    return [signal["note"] for signal in row["signals"]]


def _identity(i: int) -> str:
    return json.dumps(["realshort-pick", f"row{i}", "en"], separators=(",", ":"))


@pytest.mark.asyncio
async def test_check_redact_check_cleans_every_ggwp_copy_and_the_workbench_keeps_working(workbench, service):
    from ggwork_pick.answer_check import check_answer
    from ggwork_pick.repository import CATALOG_CACHE, PickRepository
    from ggwork_pick.selection import SelectionService

    alice = PickRepository(service.session_factory, "alice")
    selection = SelectionService(alice)
    # Batch A carries the notes; a card, an empty query naming a pan domain, a saved choice and an answer note use it.
    first = await _sync(service, _contaminated_feed())
    card = await selection.query({"limit": 20}, thread_id="thread-pan", run_id="run-1", call_id="call-1")
    empty = await selection.query({"query": "pan.baidu"}, thread_id="thread-pan", run_id="run-1", call_id="call-2")
    assert empty["items"] == [] and card["catalog_batch_id"] == first["catalog_batch_id"]
    linked = next(item for item in card["items"] if item["identity"] == _identity(1))
    receipt = await alice.save_selection("req-1", card["id"], [linked["item_id"]])
    answer_notes = check_answer("可以看看《提取码 x7k2》。", known_titles=set(), posted_checked=True)
    await alice.record_answer_check(thread_id="thread-pan", run_id="run-1", message_id="msg-1", notes=answer_notes)
    # The source is not fixed yet: batch B still carries the notes and becomes current; A stays, the card uses it.
    second = await _sync(service, [*_contaminated_feed(), feed_row(5)])
    assert (await alice.current_batch("catalog"))["id"] == second["catalog_batch_id"] != first["catalog_batch_id"]
    assert LINK_NOTE in _notes((await alice.catalog_rows(second["catalog_batch_id"]))[0])

    before = workbench.json_columns()
    counts, threads = workbench.check()
    assert counts == dict.fromkeys(LOCATIONS, 0) | {
        "ggwp_drama_versions.payload_json": 4,
        "ggwp_candidate_sets.ordered_items_json": 1,
        "ggwp_candidate_sets.conditions_json": 1,
        "ggwp_selections.snapshot_json": 1,
        "ggwp_answer_checks.notes_json": 1,
    }
    assert threads == []

    redacted = workbench.redact()
    after = workbench.json_columns()
    assert workbench.check() == (dict.fromkeys(LOCATIONS, 0), [])
    assert redacted == [4, 1, 1, 1, 0, 1, 0, 0]
    hits = (LINK_NOTE, CODE_NOTE, "pan.baidu", *answer_notes)
    assert after == {key: _redacted(text, *hits) for key, text in before.items()}
    changed = {key for key in before if after[key] != before[key]}
    assert len(changed) == 8
    # Every value still parses, and nothing but the placeholder replaced the hits.
    for key, text in after.items():
        assert PLACEHOLDER in text or key not in changed
        json.loads(text)

    # The gateway's batch cache still holds rows read before the redaction: the runbook restarts the gateway.
    assert LINK_NOTE in _notes((await alice.catalog_rows(second["catalog_batch_id"]))[0])
    CATALOG_CACHE.clear()
    for batch in (second, first):
        rows = await alice.catalog_rows(batch["catalog_batch_id"])
        assert [row["identity"] for row in rows[:4]] == [_identity(i) for i in range(1, 5)]
        [signal] = rows[0]["signals"]
        assert (signal["note"], signal["label"], signal["grade"]) == (PLACEHOLDER, QUOTED_LABEL, TRAILING_BACKSLASH)
        assert _notes(rows[1]) == [PLACEHOLDER] and rows[2]["title"] == CLEAN_TITLE and _notes(rows[2]) == [CLEAN_NOTE]
    detail = await selection.detail(card["id"], linked["item_id"])
    assert [evidence["note"] for evidence in detail["item"]["evidence"]] == [PLACEHOLDER]
    assert {result["id"] for result in await alice.results("thread-pan")} == {card["id"], empty["id"]}
    assert (await alice.result(empty["id"]))["conditions_json"]["query"] == PLACEHOLDER
    [saved] = await alice.selections()
    assert saved["identity"] == saved["snapshot_json"]["identity"] == linked["identity"]
    assert [evidence["note"] for evidence in saved["snapshot_json"]["evidence"]] == [PLACEHOLDER]
    assert (await alice.answer_checks("thread-pan"))[0]["notes"] == [PLACEHOLDER]
    # Replays, 换一批 on the old card and a new save all still work on the redacted rows.
    assert await alice.save_selection("req-1", card["id"], [linked["item_id"]]) == receipt
    again = await selection.query({"exclude_previous": True}, thread_id="thread-pan", run_id="run-2", call_id="call-3", parent_result_id=card["id"])
    assert again["catalog_batch_id"] == first["catalog_batch_id"] and [item["identity"] for item in again["items"]] == []
    other = next(item for item in card["items"] if item["identity"] != linked["identity"])
    assert (await alice.save_selection("req-2", card["id"], [other["item_id"]]))["saved"][0]["status"] == "created"

    # Idempotent: a second run changes nothing.
    settled = workbench.json_columns()
    assert workbench.redact() == [0] * 8
    assert workbench.json_columns() == settled


@pytest.mark.asyncio
async def test_identity_values_are_left_alone_and_the_check_keeps_reporting_them(workbench, service):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    alice = PickRepository(service.session_factory, "alice")
    rows = [
        {
            "source": "synthetic",
            "source_id": "k-pwd=1",
            "language": "en",
            "title": "Example",
            "signals": [{"kind": "rank", "source_ref": "x", "note": LINK_NOTE}],
        }
    ]
    batch = await Importer(alice, service.data_dir).catalog(json.dumps(rows).encode(), "json")
    [(identity,)] = workbench.fetch("SELECT identity FROM deerflow.ggwp_drama_versions")
    assert workbench.redact() == [1, 0, 0, 0, 0, 0, 0, 0]
    [row] = await alice.catalog_rows(batch["id"])
    assert (row["identity"], row["source_id"], row["original"]["source_id"]) == (identity, "k-pwd=1", "k-pwd=1")
    assert _notes(row) == [PLACEHOLDER] and row["original"]["signals"][0]["note"] == PLACEHOLDER
    # The runbook stops here: a hit left in an identity is not the script's to change.
    assert workbench.check()[0]["ggwp_drama_versions.payload_json"] == 1
    assert workbench.redact() == [0] * 8


def test_the_scripts_cover_every_json_column_of_the_workbench(workbench):
    # SQLAlchemy's JSON is PostgreSQL json, not jsonb: stored text is kept byte for byte, which the redaction relies on.
    columns = workbench.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'deerflow' AND table_name LIKE 'ggwp%%' AND data_type IN ('json', 'jsonb')"
    )
    assert sorted(columns) == sorted((table, column, "json") for table, _, column in JSON_COLUMNS)
    redact = REDACT.read_text(encoding="utf-8")
    for table, _, column in JSON_COLUMNS:
        assert f"UPDATE deerflow.{table} SET {column} = deerflow.ggwp_pan_redact({column}, :'pan')" in redact
    assert [f"{table}.{column}" for table, _, column in JSON_COLUMNS] == LOCATIONS[:-1]


@pytest.mark.asyncio
async def test_text_escaped_as_unicode_escapes_is_found_and_redacted(workbench, service):
    # The host serializer keeps non-ASCII verbatim; a writer with ensure_ascii would store \uXXXX escapes instead.
    escaped = json.dumps(["提取码 x7k2", 'clean "quoted" \\ text'], ensure_ascii=True)
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_answer_checks (id, owner_id, thread_id, run_id, notes_json, created_at)"
        " VALUES ('check-1', 'alice', 'thread-x', 'run-x', %s::json, '2026-09-24T00:00:00.000000+00:00') RETURNING id",
        escaped,
    )
    assert workbench.check()[0]["ggwp_answer_checks.notes_json"] == 1
    assert workbench.redact() == [0, 0, 0, 0, 0, 1, 0, 0]
    assert workbench.check()[0]["ggwp_answer_checks.notes_json"] == 0
    [(stored,)] = workbench.fetch("SELECT notes_json::text FROM deerflow.ggwp_answer_checks")
    assert stored == escaped.replace(json.dumps("提取码 x7k2"), json.dumps(PLACEHOLDER, ensure_ascii=False))
    assert json.loads(stored) == [PLACEHOLDER, 'clean "quoted" \\ text']


# ---- host threads: binary checkpoints, found by pan-check.sql, removed only with the thread ----


def _put_thread(saver, thread_id: str, messages: list, *, title: str | None = None, writes: list | None = None) -> None:
    from langgraph.checkpoint.base import empty_checkpoint

    values = {"messages": messages, **({"title": title} if title is not None else {})}
    versions = dict.fromkeys(values, "1")
    checkpoint = {**empty_checkpoint(), "channel_values": values, "channel_versions": versions}
    config = saver.put({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}, checkpoint, {"source": "loop", "step": 1}, versions)
    if writes:
        saver.put_writes(config, writes, "task-1")


@pytest.mark.asyncio
async def test_threads_with_a_hit_are_listed_until_they_are_deleted(workbench, service):
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.run.sql import RunRepository
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.thread_meta.sql import ThreadMetaRepository
    from deerflow.persistence.user.model import UserRow
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import insert

    alice, bob = str(uuid4()), str(uuid4())
    result = json.dumps({"items": [{"evidence": [{"note": "资源 PAN.Baidu.com/s/1x"}]}]}, ensure_ascii=False)
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        _put_thread(saver, "thread-tool", [HumanMessage("找剧"), ToolMessage(result, tool_call_id="c1")])
        # A tool output written with ensure_ascii: the keyword is \\uXXXX text in the blob; no threads_meta row either.
        escaped = ToolMessage(json.dumps({"note": "提取码 x7k2"}, ensure_ascii=True), tool_call_id="c2")
        _put_thread(saver, "thread-escaped", [HumanMessage("找剧")], writes=[("messages", [escaped])])
        _put_thread(saver, "thread-answer", [HumanMessage("找剧"), AIMessage("密码：ab12")], title="访问码 8k2p 的剧")
        _put_thread(saver, "thread-clean", [HumanMessage("有没有财富密码"), AIMessage("《财富密码》在候选里。")], title=CLEAN_TITLE)
    engine = service.session_factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.execute(insert(UserRow.__table__), [{"id": alice, "email": "alice@example.test"}, {"id": bob, "email": "bob@example.test"}])
        await conn.execute(
            insert(ThreadMetaRow.__table__),
            [{"thread_id": thread, "user_id": owner} for thread, owner in (("thread-tool", alice), ("thread-answer", bob), ("thread-clean", alice))],
        )
        await conn.execute(
            insert(RunRow.__table__),
            [
                {"run_id": "run-answer", "thread_id": "thread-answer", "user_id": bob, "last_ai_message": "密码：ab12"},
                {"run_id": "run-clean", "thread_id": "thread-clean", "user_id": alice, "last_ai_message": "《财富密码》在候选里。"},
            ],
        )

    listed = [
        ("thread-answer", "bob@example.test", "checkpoint_blobs,checkpoints,runs"),
        ("thread-escaped", NO_META, "checkpoint_writes"),
        ("thread-tool", "alice@example.test", "checkpoint_blobs"),
    ]
    assert workbench.check() == (dict.fromkeys(LOCATIONS, 0), listed)
    # pan-redact.sql never touches the host's tables.
    digest = workbench.checkpoint_digest()
    assert workbench.redact() == [0] * 8
    assert workbench.checkpoint_digest() == digest
    assert workbench.check()[1] == listed

    # What the thread DELETE route does to each store, as each thread's owner.
    runs, metas = RunRepository(service.session_factory), ThreadMetaRepository(service.session_factory)
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        for thread, owner in (("thread-answer", bob), ("thread-tool", alice)):
            saver.delete_thread(thread)
            await runs.delete_by_thread(thread, user_id=owner)
            await metas.delete(thread, user_id=owner)
    # Without a threads_meta row the route answers 404: the runbook stops there.
    assert workbench.check() == (dict.fromkeys(LOCATIONS, 0), [("thread-escaped", NO_META, "checkpoint_writes")])
    assert workbench.fetch("SELECT count(*) FROM deerflow.checkpoints WHERE thread_id = 'thread-clean'") == [(1,)]


# ---- raw feed files: found with the runbook's grep, deleted, never overwritten ----


def _runbook_commands(data_dir: Path) -> tuple[str, str]:
    """The runbook's list and delete commands, pointed at a test data directory."""
    lines = [line.strip() for line in RUNBOOK.read_text(encoding="utf-8").splitlines()]
    [listing] = {line for line in lines if line.startswith("grep -rliE '") and line.endswith(" /data/pick")}
    [removal] = [line for line in lines if line.startswith("grep -rliE --null '")]
    return listing.replace("/data/pick", str(data_dir)), removal.replace("/data/pick", str(data_dir))


def _shell(command: str) -> list[str]:
    result = subprocess.run(["bash", "-c", command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", timeout=60)
    assert result.returncode in (0, 1), result.stdout  # grep exits 1 when nothing matches
    return sorted(result.stdout.split())


@pytest_asyncio.fixture
async def files_service(pick_db_url, tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "pick")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield svc
    await engine.dispose()


@pytest.mark.asyncio
async def test_raw_files_with_a_hit_are_found_and_deleting_them_is_safe(files_service):
    from ggwork_pick.repository import PickRepository

    if shutil.which("bash") is None or shutil.which("grep") is None:
        pytest.skip("needs bash and grep")
    listing, removal = _runbook_commands(files_service.data_dir)
    batches = PickRepository.shared(files_service.session_factory)
    first = await _sync(files_service, _contaminated_feed())
    second = await _sync(files_service, [*_contaminated_feed(), feed_row(5)])
    paths = {row["id"]: row["raw_blob_path"] for row in await batches.batches()}
    assert _shell(listing) == sorted([paths[first["catalog_batch_id"]], paths[second["catalog_batch_id"]]])
    _shell(removal)
    assert _shell(listing) == [] and not any(Path(paths[batch["catalog_batch_id"]]).exists() for batch in (first, second))
    # The source is fixed; the next sync prunes both batches, whose files are already gone.
    fixed = [feed_row(1), feed_row(2), feed_row(3, title=CLEAN_TITLE), feed_row(4)]
    clean = await _sync(files_service, fixed, keep_batches=1)
    statuses = {row["id"]: row["status"] for row in await batches.batches()}
    assert (statuses[first["catalog_batch_id"]], statuses[second["catalog_batch_id"]]) == ("pruned", "pruned")
    assert _shell(listing) == []
    # Nothing reads a raw file at runtime; only an import of the same content looks at it again, and rewrites a missing one.
    blob = Path((await batches.current_batch("catalog"))["raw_blob_path"])
    blob.unlink()
    assert (await _sync(files_service, fixed))["catalog_batch_id"] == clean["catalog_batch_id"] and blob.exists()
    # An overwritten file fails that same import, which is why the runbook deletes and never rewrites.
    blob.write_bytes(b"[]")
    from ggwork_pick.sync import RealShortSync

    refused = await RealShortSync(files_service, base_url="https://realshort.test", token=TOKEN, transport=feed_transport(fixed)).run("manual")
    assert refused["status"] == "failed" and "校验失败" in refused["error"]
