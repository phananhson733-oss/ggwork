"""The weekly pan check in docs/pick-workbench/supabase.md section 6: pan-check.sql, pan-redact.sql and the host's threads.

Until RealShort #67 scrubs v1 feed notes, a note holding a pan link is stored verbatim and copied onward: every catalog batch
the sync keeps, candidate snapshots, saved selections, and the host's checkpoints of the conversation that showed it. The
runbook's scripts run here as files from docs/, through psql, logged in as a stand-in for deerflow_app: not a superuser, owner
of the deerflow schema and every table in it. The contaminated data is written by the real sync, import, selection and
repository code; the host tables come from the host's own table definitions and langgraph's PostgresSaver.setup(). Every
location the check reports and every host branch holds a hit of its own, so disabling any one of them turns a test red. The
PostgreSQL half skips when PICK_TEST_PG_URL is unset. The observation radar's locations (migration 0007) are
test_pan_runbook_obs.py, the container's disk is test_pan_runbook_disk.py; the pattern and the runbook's own text are
test_pan_runbook_text.py.
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

import httpx
import pg
import pytest
import pytest_asyncio
import revisions
import yaml
from engines import HOST_JSON_SERIALIZER, host_engine
from pan_runbook import (
    CHECK,
    CLEARED,
    CODE_NOTE,
    DELETED_TEXT,
    JSON_COLUMNS,
    KEPT_JSON_COLUMNS,
    LOCATIONS,
    PASSWORD_NBSP,
    PASSWORD_NEWLINE,
    PASSWORD_TAB,
    PASSWORD_VT,
    REDACT,
    ROOT,
    Scan,
    clean_scan,
    cleared_at,
    feed_signal,
    feed_transport,
    password,
    pull,
    scan,
    shell,
    step_lines,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_realshort_sync import feed_row

PLACEHOLDER = "[网盘信息已移除]"
# pan-check.sql's owner for a thread the DELETE route cannot find (require_existing): the runbook stops there.
NO_META = "(没有 threads_meta 行)"
NOTHING = dict.fromkeys(LOCATIONS, 0)
CLEARED_NONE = dict.fromkeys(CLEARED, 0)
DELETED = {f"{table}.{column}" for table, column in DELETED_TEXT}

# A note quoting a link, with escaped quotes and backslashes in the same string; the strings next to it hold escapes too
# (grade ends in a backslash right before note's opening quote) and must come through byte for byte.
LINK_NOTE = 'He said "see PAN.baidu.com/s/1AbC?pwd=x7k2", saved to C:\\dl\\'
QUOTED_LABEL = 'KalosTV "日榜" 第1\\2'
TRAILING_BACKSLASH = "A\\"
SCOPE = "范围说明见 pan.quark.cn/s/scope1"
KNOWLEDGE_FILE = "网盘 pwd=ab12.md"
KNOWLEDGE_TEXT = "# 规则\n\n素材的提取码在群公告里"
# Near misses: 密码 without a separator is a common word in titles.
CLEAN_TITLE = "财富密码"
CLEAN_NOTE = "密码学入门，见 pan 字样也不算"


# ---- a database like production: ggwp tables at head, the host's tables, all owned by a deerflow_app stand-in ----


def _libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _create_host_tables(url: str) -> None:
    from deerflow.persistence.models.run_event import RunEventRow
    from deerflow.persistence.run.model import RunChangeClockRow, RunRow
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.user.model import UserRow
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(_libpq(url)) as saver:
        saver.setup()
    engine = create_engine(make_url(url).set(drivername="postgresql+psycopg"), json_serializer=HOST_JSON_SERIALIZER)
    try:
        with engine.begin() as conn:
            for table in (UserRow.__table__, ThreadMetaRow.__table__, RunRow.__table__, RunChangeClockRow.__table__, RunEventRow.__table__):
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

    def call(self, script: Path, *options: str) -> subprocess.CompletedProcess:
        """psql -f, logged in as the owner."""
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
        command = [self.psql, "-X", "-w", "-A", "-F", "|", "-P", "footer=off", *options, "-f", str(script)]
        return subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", timeout=120)

    def run(self, script: Path, *options: str) -> str:
        """psql -f; any error or warning fails the test."""
        result = self.call(script, *options)
        assert result.returncode == 0, result.stdout
        assert "ERROR" not in result.stdout and "WARNING" not in result.stdout, result.stdout
        return result.stdout

    def check(self, disk_threads: str = "") -> tuple[dict[str, int], list[tuple[str, str, str]]]:
        """pan-check.sql's two tables: rows with a hit per location, and threads with a hit, their owner and where the hit is."""
        lines = self.run(CHECK, "-v", f"disk_threads={disk_threads}").splitlines()
        assert lines[0] == "BEGIN" and lines[-1] == "COMMIT", lines
        header_counts, header_threads = lines.index("location|rows"), lines.index("thread_id|owner|found_in")
        counts = [line.split("|") for line in lines[header_counts + 1 : header_threads]]
        threads = [tuple(line.split("|")) for line in lines[header_threads + 1 : -1]]
        assert [location for location, _ in counts] == LOCATIONS
        return {location: int(rows) for location, rows in counts}, threads

    def redact(self, revision: str = "head") -> dict[str, int]:
        """pan-redact.sql's row counts by location; the output is exactly what the runbook shows for a database at that revision."""
        cleared = cleared_at(revision)
        lines = self.run(REDACT).splitlines()
        kinds = ["DELETE n" if location in DELETED else "UPDATE n" for location in cleared]
        expected = ["BEGIN", "CREATE FUNCTION", *kinds, "DROP FUNCTION", "COMMIT"]
        assert [re.sub(r"^(UPDATE|DELETE) \d+$", r"\1 n", line) for line in lines] == expected, lines
        return dict(zip(cleared, [int(line.split()[1]) for line in lines if line.startswith(("UPDATE ", "DELETE "))], strict=True))

    def json_columns(self) -> dict[tuple, str]:
        """Every ggwp JSON value as stored, byte for byte; the columns added for the mirror are null on older rows."""
        stored = {}
        for table, keys, column in [*JSON_COLUMNS, *KEPT_JSON_COLUMNS]:
            for *key, text in self.fetch(f"SELECT {keys}, {column}::text FROM deerflow.{table} WHERE {column} IS NOT NULL"):
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


def _contaminated_feed() -> list[dict]:
    return [
        feed_row(1, signals=[feed_signal(1, label=QUOTED_LABEL, grade=TRAILING_BACKSLASH, note=LINK_NOTE)]),
        feed_row(2, signals=[feed_signal(2, note=CODE_NOTE)]),
        feed_row(3, title=CLEAN_TITLE, signals=[feed_signal(3, note=CLEAN_NOTE)]),
        feed_row(4),
        feed_row(6, signals=[feed_signal(6, note=PASSWORD_NEWLINE)]),
        feed_row(7, signals=[feed_signal(7, note=PASSWORD_TAB)]),
        feed_row(8, signals=[feed_signal(8, note=PASSWORD_VT)]),
        feed_row(9, signals=[feed_signal(9, note=PASSWORD_NBSP)]),
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


def _source_hits(workbench, tmp_path: Path) -> int:
    """Step 3's check after the manual pull, in the psql of step 2: hits in the batch the latest successful pull used."""
    lines = step_lines(3)
    start = lines.index("```sql")
    query = "\n".join(lines[start + 1 : lines.index("```", start + 1)])
    script = tmp_path / "source.sql"
    script.write_text(f"\\set disk_threads ''\n\\i {CHECK}\n{query}\n", encoding="utf-8")
    return int(workbench.run(script).strip().split("\n")[-1])


def _knowledge(workbench) -> list[tuple]:
    return workbench.fetch("SELECT title, source_ref, text FROM deerflow.ggwp_knowledge_versions WHERE text = %s", KNOWLEDGE_TEXT)


@pytest.mark.asyncio
async def test_every_location_is_found_then_redacted_or_kept_by_design_and_the_workbench_keeps_working(workbench, service):
    from ggwork_pick.answer_check import check_answer
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import CATALOG_CACHE, PickRepository
    from ggwork_pick.selection import SelectionService

    alice = PickRepository(service.session_factory, "alice")
    selection = SelectionService(alice)
    # Batch A carries the notes and a scope quoting a link; a card, an empty query naming a pan domain, a saved choice and an
    # answer note use it. The save's request id holds a hit too: it is an id, kept by design.
    first = await pull(service, _contaminated_feed(), scope=SCOPE)
    card = await selection.query({"limit": 20}, thread_id="thread-pan", run_id="run-1", call_id="call-1")
    empty = await selection.query({"query": "pan.baidu"}, thread_id="thread-pan", run_id="run-1", call_id="call-2")
    assert empty["items"] == [] and card["catalog_batch_id"] == first["catalog_batch_id"]
    linked = next(item for item in card["items"] if item["identity"] == _identity(1))
    receipt = await alice.save_selection("req-pwd=1", card["id"], [linked["item_id"]])
    answer_notes = check_answer("可以看看《提取码 x7k2》。", known_titles=set(), posted_checked=True)
    await alice.record_answer_check(thread_id="thread-pan", run_id="run-1", message_id="msg-1", notes=answer_notes)
    # A manual knowledge upload whose file name, source and text all hit.
    knowledge = await Importer(alice, service.data_dir).knowledge(KNOWLEDGE_TEXT.encode(), KNOWLEDGE_FILE, f"upload:{KNOWLEDGE_FILE}")
    # Receipts the code writes hold only ids, identity, request_id, status and version; a free-text field a later
    # receipt might carry must still be cleared, so one row stands in for it.
    synthetic = json.dumps({"request_id": "req-synthetic", "saved": [], "note": "提取码 x7k2"}, ensure_ascii=False)
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_selection_commands (owner_id, request_id, payload_hash, receipt_json, created_at)"
        " VALUES ('alice', 'req-synthetic', 'h', %s::json, '2026-09-24T00:00:00.000000+00:00') RETURNING request_id",
        synthetic,
    )
    # The mirror's columns. Every query now freezes its batch's data_as_of, scope included (P2-5b): the card and the empty
    # query both carry A's. The run details have no writer yet (P2-5c), so the first run gets a free-text reason by hand,
    # and the card an excluded identity holding a hit (the queries here excluded nothing).
    assert [(await alice.result(result["id"]))["data_as_of_json"]["scope"] for result in (card, empty)] == [SCOPE, SCOPE]
    excluded = [json.dumps(["synthetic", "k-pwd=1", "en"], separators=(",", ":"))]
    workbench.fetch(
        "UPDATE deerflow.ggwp_candidate_sets SET excluded_json = %s::json WHERE id = %s RETURNING id",
        json.dumps(excluded, ensure_ascii=False),
        card["id"],
    )
    workbench.fetch(
        "UPDATE deerflow.ggwp_sync_runs SET details_json = %s::json WHERE catalog_batch_id = %s RETURNING id",
        json.dumps({"mode": "mirror", "outcome": "degraded", "reason": "提取码 x7k2"}, ensure_ascii=False),
        first["catalog_batch_id"],
    )
    # The source is not fixed yet: batch B still carries the notes and becomes current; A stays, the card uses it.
    second = await pull(service, [*_contaminated_feed(), feed_row(5)])
    assert (await alice.current_batch("catalog"))["id"] == second["catalog_batch_id"] != first["catalog_batch_id"]
    assert LINK_NOTE in _notes((await alice.catalog_rows(second["catalog_batch_id"]))[0])

    before = workbench.json_columns()
    counts, threads = workbench.check()
    assert counts == NOTHING | {
        "ggwp_drama_versions.payload_json": 12,
        "ggwp_candidate_sets.ordered_items_json": 1,
        "ggwp_candidate_sets.conditions_json": 1,
        "ggwp_selections.snapshot_json": 1,
        "ggwp_selection_commands.receipt_json": 2,
        "ggwp_answer_checks.notes_json": 1,
        "ggwp_import_batches.validation_json": 1,
        "ggwp_knowledge_versions.metadata_json": 1,
        "ggwp_candidate_sets.data_as_of_json": 2,
        "ggwp_sync_runs.details_json": 1,
        "ggwp_knowledge_versions.title": 1,
        "ggwp_knowledge_versions.text": 1,
        "ggwp_knowledge_versions.source_ref": 1,
        "ggwp_candidate_sets.excluded_json": 1,
    }
    assert threads == []

    redacted = workbench.redact()
    after = workbench.json_columns()
    # Left: the request id (an id), the knowledge source (the document's identity), its text (the whole rules) and the
    # excluded identities.
    kept = {
        "ggwp_selection_commands.receipt_json": 1,
        "ggwp_knowledge_versions.text": 1,
        "ggwp_knowledge_versions.source_ref": 1,
        "ggwp_candidate_sets.excluded_json": 1,
    }
    assert workbench.check() == (NOTHING | kept, [])
    # The second receipt's hit is its request id, which stays; every other hit found above is cleared.
    assert redacted == CLEARED_NONE | {location: counts[location] for location in CLEARED if counts[location]} | {"ggwp_selection_commands.receipt_json": 1}
    hits = (LINK_NOTE, CODE_NOTE, PASSWORD_NEWLINE, PASSWORD_TAB, PASSWORD_VT, PASSWORD_NBSP, "pan.baidu", *answer_notes, SCOPE, "提取码 x7k2", KNOWLEDGE_FILE)
    assert after == {key: _redacted(text, *hits) for key, text in before.items()}
    changed = {key for key in before if after[key] != before[key]}
    assert len(changed) == sum(redacted[f"{table}.{column}"] for table, _, column in JSON_COLUMNS)
    for key, text in after.items():
        assert PLACEHOLDER in text or key not in changed
        json.loads(text)
    assert _knowledge(workbench) == [(PLACEHOLDER, f"upload:{KNOWLEDGE_FILE}", KNOWLEDGE_TEXT)]

    # The gateway's batch cache still holds rows read before the redaction: the runbook restarts the gateway.
    assert LINK_NOTE in _notes((await alice.catalog_rows(second["catalog_batch_id"]))[0])
    CATALOG_CACHE.clear()
    for batch in (second, first):
        rows = {row["identity"]: row for row in await alice.catalog_rows(batch["catalog_batch_id"])}
        [signal] = rows[_identity(1)]["signals"]
        assert (signal["note"], signal["label"], signal["grade"]) == (PLACEHOLDER, QUOTED_LABEL, TRAILING_BACKSLASH)
        assert [_notes(rows[_identity(i)]) for i in (2, 6, 7, 8, 9)] == [[PLACEHOLDER]] * 5
        assert rows[_identity(3)]["title"] == CLEAN_TITLE and _notes(rows[_identity(3)]) == [CLEAN_NOTE]
    assert (await alice.batch_info(first["catalog_batch_id"]))["scope"] == PLACEHOLDER
    assert (await alice.result(card["id"]))["data_as_of_json"]["scope"] == PLACEHOLDER
    assert (await alice.result(card["id"]))["excluded_json"] == excluded
    [document] = await alice.knowledge_documents(knowledge["id"])
    assert (document["title"], document["metadata_json"]) == (PLACEHOLDER, {"filename": PLACEHOLDER})
    detail = await selection.detail(card["id"], linked["item_id"])
    assert [evidence["note"] for evidence in detail["item"]["evidence"]] == [PLACEHOLDER]
    assert {result["id"] for result in await alice.results("thread-pan")} == {card["id"], empty["id"]}
    assert (await alice.result(empty["id"]))["conditions_json"]["query"] == PLACEHOLDER
    [saved] = await alice.selections()
    assert saved["identity"] == saved["snapshot_json"]["identity"] == linked["identity"]
    assert [evidence["note"] for evidence in saved["snapshot_json"]["evidence"]] == [PLACEHOLDER]
    assert (await alice.answer_checks("thread-pan"))[0]["notes"] == [PLACEHOLDER]
    assert (await alice.command_receipt("req-synthetic"))["note"] == PLACEHOLDER
    # Replays, 换一批 on the old card and a new save all still work on the redacted rows.
    assert await alice.save_selection("req-pwd=1", card["id"], [linked["item_id"]]) == receipt
    again = await selection.query({"exclude_previous": True}, thread_id="thread-pan", run_id="run-2", call_id="call-3", parent_result_id=card["id"])
    assert again["catalog_batch_id"] == first["catalog_batch_id"] and again["items"] == []
    other = next(item for item in card["items"] if item["identity"] != linked["identity"])
    assert (await alice.save_selection("req-2", card["id"], [other["item_id"]]))["saved"][0]["status"] == "created"

    # Idempotent: a second run changes nothing.
    settled = workbench.json_columns()
    assert workbench.redact() == CLEARED_NONE
    assert workbench.json_columns() == settled


@pytest.mark.asyncio
async def test_step_3_tells_whether_the_latest_successful_pull_is_clean(workbench, service, tmp_path):
    from ggwork_pick.sync import RealShortSync

    await pull(service, _contaminated_feed())
    assert _source_hits(workbench, tmp_path) == 6
    # A pull that failed used no batch and does not count.
    failed = await RealShortSync(service, base_url="https://realshort.test", token="not-the-token", transport=feed_transport([], scope="s")).run("manual")
    assert failed["status"] == "failed" and _source_hits(workbench, tmp_path) == 6
    # Fixed at the source, the next pull's batch is clean even though the older batches still hold the notes.
    await pull(service, [feed_row(i) for i in (1, 2, 3, 4, 6, 7, 8, 9)])
    assert _source_hits(workbench, tmp_path) == 0 and workbench.check()[0]["ggwp_drama_versions.payload_json"] == 6


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
    assert workbench.redact() == CLEARED_NONE | {"ggwp_drama_versions.payload_json": 1}
    [row] = await alice.catalog_rows(batch["id"])
    assert (row["identity"], row["source_id"], row["original"]["source_id"]) == (identity, "k-pwd=1", "k-pwd=1")
    assert _notes(row) == [PLACEHOLDER] and row["original"]["signals"][0]["note"] == PLACEHOLDER
    # The runbook stops here: a hit left in an identity is not the script's to change.
    assert workbench.check()[0]["ggwp_drama_versions.payload_json"] == 1
    assert workbench.redact() == CLEARED_NONE


@pytest.mark.asyncio
async def test_both_scripts_still_run_on_a_database_at_0004(workbench):
    # Production stays at 0004 until the mirror ships, while the checkout the weekly check runs from has these scripts
    # already. The three columns 0005 adds count as 0 until they exist; their two UPDATEs are left out.
    engine = host_engine(workbench.url)
    try:
        await revisions.downgrade(engine, "0004")
    finally:
        await engine.dispose()
    assert workbench.fetch("SELECT version_num FROM deerflow.ggwp_alembic_version") == [("0004",)]
    added = ["data_as_of_json", "details_json", "excluded_json"]
    assert workbench.fetch("SELECT count(*) FROM information_schema.columns WHERE column_name = ANY(%s)", added) == [(0,)]
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_selection_commands (owner_id, request_id, payload_hash, receipt_json, created_at)"
        " VALUES ('alice', 'req-old', 'h', %s::json, '2026-09-24T00:00:00.000000+00:00') RETURNING request_id",
        json.dumps({"request_id": "req-old", "note": "提取码 x7k2"}, ensure_ascii=False),
    )
    assert workbench.check() == (NOTHING | {"ggwp_selection_commands.receipt_json": 1}, [])
    assert workbench.redact("0004") == dict.fromkeys(cleared_at("0004"), 0) | {"ggwp_selection_commands.receipt_json": 1}
    assert workbench.check() == (NOTHING, [])
    assert workbench.fetch("SELECT receipt_json::text FROM deerflow.ggwp_selection_commands") == [
        (json.dumps({"request_id": "req-old", "note": PLACEHOLDER}, ensure_ascii=False),)
    ]


def test_the_scripts_cover_every_json_column_of_the_workbench(workbench):
    # SQLAlchemy's JSON is PostgreSQL json, not jsonb: stored text is kept byte for byte, which the redaction relies on.
    columns = workbench.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'deerflow' AND table_name LIKE 'ggwp%%' AND data_type IN ('json', 'jsonb')"
    )
    assert sorted(columns) == sorted((table, column, "json") for table, _, column in [*JSON_COLUMNS, *KEPT_JSON_COLUMNS])


def test_feedback_json_is_detected_without_rewriting_immutable_provenance(workbench):
    payload = json.dumps({"note": "https://pan.quark.cn/s/synthetic-feedback"})
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_feedback_versions (id,owner_id,content_hash,scan_started_at,scan_completed_at,published_at,manifest_json)"
        " VALUES ('fv-test','alice','synthetic-hash','2026-10-07','2026-10-07','2026-10-07',%s::json) RETURNING id",
        payload,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_feedback_records (version_id,table_id,record_id,values_json)"
        " VALUES ('fv-test','tbl-test','rec-test',%s::json) RETURNING record_id",
        payload,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_feedback_result_evidence (result_id,owner_id,version_id,evidence_json,created_at)"
        " VALUES ('result-test','alice','fv-test',%s::json,'2026-10-07') RETURNING result_id",
        payload,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_feedback_identity_links (owner_id,source_record_id,catalog_identity,method,evidence_json,confirmed_at)"
        " VALUES ('alice','rec-test','synthetic','confirmed',%s::json,'2026-10-07') RETURNING source_record_id",
        payload,
    )
    locations = [
        f"{table}.{column}" for table, _, column in KEPT_JSON_COLUMNS if table.startswith("ggwp_feedback_") and not table.startswith("ggwp_feedback_plan_")
    ]
    assert len(locations) == 4
    assert all(workbench.check()[0][location] == 1 for location in locations)
    assert workbench.redact() == CLEARED_NONE
    assert all(workbench.check()[0][location] == 1 for location in locations)
    assert workbench.fetch("SELECT content_hash,manifest_json::text FROM deerflow.ggwp_feedback_versions") == [("synthetic-hash", payload)]


@pytest.mark.asyncio
async def test_text_escaped_as_unicode_escapes_is_found_and_redacted(workbench, service):
    # The host serializer keeps non-ASCII verbatim; a writer with ensure_ascii would store \\uXXXX escapes instead.
    notes = [PASSWORD_TAB, "提取码 x7k2", 'clean "quoted" \\ text']
    escaped = json.dumps(notes, ensure_ascii=True)
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_answer_checks (id, owner_id, thread_id, run_id, notes_json, created_at)"
        " VALUES ('check-1', 'alice', 'thread-x', 'run-x', %s::json, '2026-09-24T00:00:00.000000+00:00') RETURNING id",
        escaped,
    )
    assert workbench.check()[0]["ggwp_answer_checks.notes_json"] == 1
    assert workbench.redact() == CLEARED_NONE | {"ggwp_answer_checks.notes_json": 1}
    assert workbench.check()[0]["ggwp_answer_checks.notes_json"] == 0
    [(stored,)] = workbench.fetch("SELECT notes_json::text FROM deerflow.ggwp_answer_checks")
    # The escaped strings are replaced whole; the clean one keeps its escapes byte for byte.
    placeholder = json.dumps(PLACEHOLDER, ensure_ascii=False)
    assert stored == escaped.replace(json.dumps(notes[0]), placeholder).replace(json.dumps(notes[1]), placeholder)
    assert json.loads(stored) == [PLACEHOLDER, PLACEHOLDER, notes[2]]


# ---- host threads: binary checkpoints, found by pan-check.sql, removed only with the thread ----


def _put_thread(saver, thread_id: str, messages: list, *, title: str | None = None, writes: list | None = None, metadata: dict | None = None) -> None:
    from langgraph.checkpoint.base import empty_checkpoint

    values = {"messages": messages, **({"title": title} if title is not None else {})}
    versions = dict.fromkeys(values, "1")
    checkpoint = {**empty_checkpoint(), "channel_values": values, "channel_versions": versions}
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    config = saver.put(config, checkpoint, {"source": "loop", "step": 1, **(metadata or {})}, versions)
    if writes:
        saver.put_writes(config, writes, "task-1")


# thread -> (owner, table the only hit is in). Every column the check reads has a thread whose only hit is there.
HOST_THREADS = {
    "thread-ckpt": ("alice", "checkpoints"),  # checkpoints.checkpoint: a string channel stays inline
    "thread-ckpt-meta": ("alice", "checkpoints"),  # checkpoints.metadata
    "thread-tool": ("alice", "checkpoint_blobs"),  # a tool output quoting a link, other case
    "thread-json-newline": ("bob", "checkpoint_blobs"),  # the tool output's JSON writes the newline as backslash-n
    "thread-real-newline": ("bob", "checkpoint_blobs"),  # a model answer with a real newline byte
    "thread-write-ascii": (None, "checkpoint_writes"),  # a pending write, ensure_ascii; no threads_meta row
    # Raw non-ASCII whitespace in a tool output (ensure_ascii off), so its UTF-8 bytes land in the blob as they are.
    "thread-blob-nbsp": ("alice", "checkpoint_blobs"),  # U+00A0
    "thread-blob-ideographic": ("bob", "checkpoint_blobs"),  # U+3000
    "thread-write-em": ("alice", "checkpoint_writes"),  # U+2003, in a pending write
    "thread-write-line": ("bob", "checkpoint_writes"),  # U+2028, in a pending write
    "thread-run-first": ("bob", "runs"),  # runs.first_human_message
    "thread-run-last": ("bob", "runs"),  # runs.last_ai_message
    "thread-run-kwargs": ("bob", "runs"),  # runs.kwargs_json
    "thread-run-meta": ("bob", "runs"),  # runs.metadata_json
    "thread-event-double": ("alice", "run_events"),  # run_events.content: a tool message holding JSON, serialized again
    "thread-event-double-ascii": ("alice", "run_events"),  # the same with an ensure_ascii tool output: two backslashes before u
    "thread-event-meta": ("alice", "run_events"),  # run_events.event_metadata
    "thread-meta-name": ("bob", "threads_meta"),  # threads_meta.display_name
    "thread-meta-json": ("bob", "threads_meta"),  # threads_meta.metadata_json
    "thread-clean": ("alice", None),
}
RAW_SPACES = {"thread-blob-nbsp": 0xA0, "thread-blob-ideographic": 0x3000, "thread-write-em": 0x2003, "thread-write-line": 0x2028}


async def _record_events(service, owners: dict[str, str]) -> None:
    """Tool messages written the way the host's run journal persists them: message.model_dump() through the event store."""
    from types import SimpleNamespace

    from deerflow.runtime.events.store.db import DbRunEventStore
    from deerflow.runtime.user_context import reset_current_user, set_current_user
    from langchain_core.messages import AIMessage, ToolMessage

    store = DbRunEventStore(service.session_factory)
    events = {
        "thread-event-double": [(ToolMessage(json.dumps({"note": PASSWORD_NEWLINE}, ensure_ascii=False), tool_call_id="e1"), None)],
        "thread-event-double-ascii": [(ToolMessage(json.dumps({"note": PASSWORD_NEWLINE}, ensure_ascii=True), tool_call_id="e2"), None)],
        "thread-event-meta": [(AIMessage("好的"), {"note": "提取码 x7k2"})],
        "thread-clean": [(AIMessage("《财富密码》在候选里。"), {"note": CLEAN_TITLE})],
    }
    for thread, messages in events.items():
        token = set_current_user(SimpleNamespace(id=owners[thread]))
        try:
            for message, metadata in messages:
                await store.put(
                    thread_id=thread, run_id=f"run-{thread}", event_type="llm.message", category="message", content=message.model_dump(), metadata=metadata
                )
        finally:
            reset_current_user(token)
    [(content,)] = await _scalar_rows(service, "SELECT content FROM deerflow.run_events WHERE thread_id = 'thread-event-double'")
    # Two layers of JSON: the newline is two backslashes and an n.
    assert "密码" + chr(92) * 2 + "n：ab12" in content


async def _scalar_rows(service, statement: str) -> list[tuple]:
    from sqlalchemy import text

    async with service.session_factory() as session:
        return [tuple(row) for row in (await session.execute(text(statement))).all()]


@pytest.mark.asyncio
async def test_each_host_column_finds_its_thread_until_the_thread_is_deleted(workbench, service):
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.run.sql import RunRepository
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.thread_meta.sql import ThreadMetaRepository
    from deerflow.persistence.user.model import UserRow
    from deerflow.runtime.events.store.db import DbRunEventStore
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import insert

    users = {"alice": str(uuid4()), "bob": str(uuid4())}
    owners = {thread: users[owner] for thread, (owner, _) in HOST_THREADS.items() if owner}
    link = json.dumps({"items": [{"evidence": [{"note": "资源 PAN.Baidu.com/s/1x"}]}]}, ensure_ascii=False)
    quiet = [HumanMessage("找剧")]
    escaped = ToolMessage(json.dumps({"note": PASSWORD_TAB}, ensure_ascii=True), tool_call_id="c3")
    raw = {t: ToolMessage(json.dumps({"note": password(space)}, ensure_ascii=False), tool_call_id=t) for t, space in RAW_SPACES.items()}
    special = {
        "thread-ckpt": dict(messages=quiet, title="访问码 8k2p 的剧"),
        "thread-ckpt-meta": dict(messages=quiet, metadata={"note": "提取码 x7k2"}),
        "thread-tool": dict(messages=[*quiet, ToolMessage(link, tool_call_id="c1")]),
        "thread-json-newline": dict(messages=[ToolMessage(json.dumps({"note": PASSWORD_NEWLINE}, ensure_ascii=False), tool_call_id="c2")]),
        "thread-real-newline": dict(messages=[*quiet, AIMessage(PASSWORD_NEWLINE)]),
        "thread-write-ascii": dict(messages=quiet, writes=[("messages", [escaped])]),
        "thread-blob-nbsp": dict(messages=[*quiet, raw["thread-blob-nbsp"]]),
        "thread-blob-ideographic": dict(messages=[*quiet, raw["thread-blob-ideographic"]]),
        "thread-write-em": dict(messages=quiet, writes=[("messages", [raw["thread-write-em"]])]),
        "thread-write-line": dict(messages=quiet, writes=[("messages", [raw["thread-write-line"]])]),
        "thread-clean": dict(messages=[HumanMessage("有没有财富密码"), AIMessage("《财富密码》在候选里。")], title=CLEAN_TITLE),
    }
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        # One checkpoint per thread: a blob version is written once, a second put would keep the first one's messages.
        for thread in HOST_THREADS:
            _put_thread(saver, thread, **special.get(thread, dict(messages=quiet)))
    engine = service.session_factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.execute(insert(UserRow.__table__), [{"id": users[name], "email": f"{name}@example.test"} for name in users])
        names, meta = {"thread-meta-name": "访问码 8k2p", "thread-clean": CLEAN_TITLE}, {"thread-meta-json": {"note": "提取码 x7k2"}}
        await conn.execute(
            insert(ThreadMetaRow.__table__),
            [{"thread_id": t, "user_id": owner, "display_name": names.get(t), "metadata_json": meta.get(t, {})} for t, owner in owners.items()],
        )
        runs = {
            "thread-run-first": {"first_human_message": "密码：ab12 是这部剧的吗"},
            "thread-run-last": {"last_ai_message": PASSWORD_TAB},
            "thread-run-kwargs": {"kwargs_json": {"input": {"messages": [{"content": "提取码 x7k2"}]}}},
            "thread-run-meta": {"metadata_json": {"note": "访问码 8k2p"}},
            "thread-clean": {"first_human_message": "有没有财富密码", "last_ai_message": "《财富密码》在候选里。"},
        }
        for t, values in runs.items():  # one statement each: the rows set different columns
            await conn.execute(insert(RunRow.__table__).values(run_id=f"run-{t}", thread_id=t, user_id=owners[t], **values))
    await _record_events(service, owners)
    # The raw bytes of 密码 and the whitespace character are in the blob: only the pattern's literal branch matches them.
    for thread, space in RAW_SPACES.items():
        table = HOST_THREADS[thread][1]
        [(found,)] = workbench.fetch(
            f"SELECT bool_or(position(%s::bytea IN blob) > 0) FROM deerflow.{table} WHERE thread_id = %s", ("密码" + chr(space)).encode(), thread
        )
        assert found, thread

    listed = [(t, f"{owner}@example.test" if owner else NO_META, table) for t, (owner, table) in sorted(HOST_THREADS.items()) if table]
    assert workbench.check() == (NOTHING, listed)
    # pan-redact.sql never touches the host's tables.
    digest = workbench.checkpoint_digest()
    assert workbench.redact() == CLEARED_NONE
    assert workbench.checkpoint_digest() == digest
    assert workbench.check()[1] == listed

    # What the thread DELETE route does to each store, as each thread's owner.
    runs_store, events_store, metas = (
        RunRepository(service.session_factory),
        DbRunEventStore(service.session_factory),
        ThreadMetaRepository(service.session_factory),
    )
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        for thread, owner in owners.items():
            if thread != "thread-clean":
                saver.delete_thread(thread)
                await runs_store.delete_by_thread(thread, user_id=owner)
                await events_store.delete_by_thread(thread, user_id=owner)
                await metas.delete(thread, user_id=owner)
    # Without a threads_meta row the route answers 404: the runbook stops there.
    assert workbench.check() == (NOTHING, [("thread-write-ascii", NO_META, "checkpoint_writes")])
    assert workbench.fetch("SELECT count(*) FROM deerflow.checkpoints WHERE thread_id = 'thread-clean'") == [(1,)]


# ---- tool outputs the host keeps on disk: found by pan_scan, listed by thread, removed with the thread ----


async def _delete_as_owner(workbench, service, monkeypatch, home: Path, owner: str, threads: tuple[str, ...]) -> None:
    """DELETE /api/threads/{id} on the gateway's own router, signed in as the owner, with the stores production wires in."""
    from uuid import UUID

    from app.gateway.auth.models import User
    from app.gateway.authz import AuthContext, Permissions
    from app.gateway.routers import threads as threads_router
    from deerflow.config.paths import Paths
    from deerflow.persistence.run.sql import RunRepository
    from deerflow.persistence.thread_meta.sql import ThreadMetaRepository
    from deerflow.runtime.events.store.db import DbRunEventStore
    from deerflow.runtime.runs.manager import RunManager
    from deerflow.runtime.user_context import reset_current_user, set_current_user
    from fastapi import FastAPI
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from starlette.middleware.base import BaseHTTPMiddleware

    user = User(email="owner@example.com", password_hash="x", system_role="user", id=UUID(owner))

    class SignedIn(BaseHTTPMiddleware):
        """What AuthMiddleware leaves behind for a signed-in user: request.state and the user context."""

        async def dispatch(self, request, call_next):
            request.state.user = user
            request.state.auth = AuthContext(user=user, permissions=[Permissions.THREADS_DELETE])
            token = set_current_user(user)
            try:
                return await call_next(request)
            finally:
                reset_current_user(token)

    # The volume the gateway resolves from DEER_FLOW_HOME.
    monkeypatch.setattr("deerflow.config.paths._paths", Paths(home))
    app = FastAPI()
    app.add_middleware(SignedIn)
    app.include_router(threads_router.router)
    runs = RunRepository(service.session_factory)
    app.state.thread_store = ThreadMetaRepository(service.session_factory)
    app.state.run_store = runs
    app.state.run_event_store = DbRunEventStore(service.session_factory)
    app.state.run_manager = RunManager(store=runs)
    async with AsyncPostgresSaver.from_conn_string(_libpq(workbench.url)) as checkpointer:
        app.state.checkpointer = checkpointer
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
            for thread in threads:
                response = await client.delete(f"/api/threads/{thread}")
                assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_tool_outputs_kept_on_disk_bring_their_threads_into_the_check_and_go_with_them(workbench, service, tmp_path, monkeypatch):
    from deerflow.agents.middlewares.tool_output_budget_middleware import _budget_content
    from deerflow.config.paths import Paths
    from deerflow.config.tool_output_config import ToolOutputConfig
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.user.model import UserRow
    from langchain_core.messages import HumanMessage, ToolMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import insert

    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    monkeypatch.syspath_prepend(str(ROOT / "backend"))
    from app.gateway.routers.threads import _copy_branch_user_data_sync

    # The pick runtime keeps the host's defaults: a tool output over the threshold goes to .tool-results.
    pick = yaml.safe_load((ROOT / "config.pick.example.yaml").read_text(encoding="utf-8"))
    config = ToolOutputConfig(**(pick.get("tool_output") or {}))
    assert config.enabled and config.storage_subdir == ".tool-results"
    home, alice = tmp_path / "data", str(uuid4())
    paths = Paths(home)
    (home / "pick").mkdir(parents=True)
    # A long candidate list with one note deep inside, externalized the way the host does it: the file holds the note, the
    # preview that stays in the checkpoint does not.
    items = [{"title": f"剧目{i}", "note": ""} for i in range(400)]
    items[200]["note"] = CODE_NOTE
    output = json.dumps({"items": items}, ensure_ascii=False)
    outputs = str(paths.sandbox_outputs_dir("thread-big", user_id=alice))
    preview, kind = _budget_content(output, tool_name="query_candidates", tool_call_id="c-big", outputs_path=outputs, config=config)
    assert kind == "externalized" and CODE_NOTE not in preview
    # Branching the conversation copies its user-data, externalized outputs included, to the new thread.
    assert _copy_branch_user_data_sync(paths, "thread-big", "thread-big-branch", user_id=alice) == "current_thread_best_effort"
    # A thread in the layout from before per-user directories, with no metadata row; its plain-text output breaks the line
    # between 密码 and the colon, so only a grep that reads the whole file (-z) finds it.
    legacy = home / "threads/thread-legacy/user-data/outputs/.tool-results/web_fetch-0123456789ab.log"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("\n".join([*(f"第{i}行" for i in range(2000)), PASSWORD_NEWLINE, "结束"]), encoding="utf-8")
    threads = ("thread-big", "thread-big-branch")
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        for thread in threads:
            _put_thread(saver, thread, [HumanMessage("找剧"), ToolMessage(preview, tool_call_id="c-big")])
    async with service.session_factory.kw["bind"].begin() as conn:
        await conn.execute(insert(UserRow.__table__), [{"id": alice, "email": "alice@example.test"}])
        await conn.execute(insert(ThreadMetaRow.__table__), [{"thread_id": t, "user_id": alice, "display_name": None, "metadata_json": {}} for t in threads])

    # The database alone finds nothing. pan_scan names the threads; the check lists them with their owner.
    assert workbench.check() == (NOTHING, [])
    found = scan(home)
    assert found == Scan(0, [], "thread-big,thread-big-branch,thread-legacy", "")
    owner = "alice@example.test"
    listed = [("thread-big", owner, ".tool-results"), ("thread-big-branch", owner, ".tool-results"), ("thread-legacy", NO_META, ".tool-results")]
    assert workbench.check(found.threads) == (NOTHING, listed)
    # Without the disk's threads the check refuses to run.
    refused = workbench.call(CHECK)
    assert refused.returncode == 3 and "缺 -v disk_threads" in refused.stdout

    # The owner deletes both conversations; the route removes each thread directory with the rows.
    await _delete_as_owner(workbench, service, monkeypatch, home, alice, threads)
    assert not any(paths.thread_dir(thread, user_id=alice).exists() for thread in threads)
    assert workbench.fetch("SELECT count(*) FROM deerflow.threads_meta") == [(0,)]
    assert workbench.fetch("SELECT count(*) FROM deerflow.checkpoints") == [(0,)]
    assert scan(home) == Scan(0, [], "thread-legacy", "")
    assert workbench.check("thread-legacy") == (NOTHING, [listed[2]])
    # The route cannot reach that one: step 6 deletes its files directly.
    [removal] = [line for line in step_lines(6) if line.startswith("find /data ") and "xargs -0r rm" in line]
    shell(home, removal.replace("<线程>", "thread-legacy"))
    assert clean_scan(home) and workbench.check() == (NOTHING, [])
    assert not legacy.exists()


@pytest.mark.parametrize(
    "csv_bytes,csv_hits",
    [
        (b"\xef\xbb\xbf" + "copy,note\r\n密码\u2003：synthetic,保留\r\n".encode(), 1),
        (b"\xff\x00copy,note\r\nhttps://pan.quark.cn/s/synthetic,keep\r\n", 1),
        (b"\xff\x00copy,note\r\n" + "财富密码,clean\r\n".encode(), 0),
    ],
)
def test_completion_provenance_and_binary_csv_are_detected_but_never_rewritten(workbench, csv_bytes, csv_hits):
    import hashlib

    payload = json.dumps({"note": "https://pan.quark.cn/s/synthetic-completion", "quote": '保留 "原样"'}, ensure_ascii=False, indent=2)
    receipt = json.dumps({"filename": "pan.quark.cn-synthetic.csv", "sha256": hashlib.sha256(csv_bytes).hexdigest()}, indent=2)
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_content_plans (id,owner_id,version,title,timezone,created_at,updated_at) "
        "VALUES ('plan','alice',1,'pan.quark.cn/s/synthetic-title','UTC','2026-10-08','2026-10-08') RETURNING id"
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_content_plan_rows (plan_id,row_id,owner_id,position,source_json,editable_json) "
        "VALUES ('plan','row','alice',0,%s::json,%s::json) RETURNING row_id",
        payload,
        payload,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_content_plan_commands (owner_id,request_id,payload_hash,receipt_json,created_at) "
        "VALUES ('alice','create','request-hash',%s::json,'2026-10-08') RETURNING request_id",
        payload,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_content_plan_previews (id,owner_id,plan_id,plan_version,receipt_json,created_at) "
        "VALUES ('preview','alice','plan',1,%s::json,'2026-10-08') RETURNING id",
        payload,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_content_plan_exports (id,owner_id,plan_id,plan_version,preview_id,receipt_json,csv_bytes,created_at) "
        "VALUES ('export','alice','plan',1,'preview',%s::json,%s,'2026-10-08') RETURNING id",
        receipt,
        csv_bytes,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_feedback_plan_links (owner_id,post_key,plan_id,row_id,receipt_json,basis_json) "
        "VALUES ('alice','post','plan','row',%s::json,%s::json) RETURNING post_key",
        payload,
        payload,
    )
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_feedback_plan_link_commands (owner_id,request_id,payload_hash,receipt_json,basis_json,created_at) "
        "VALUES ('alice','link','request-hash',%s::json,%s::json,'2026-10-08') RETURNING request_id",
        payload,
        payload,
    )
    protected = [
        ("ggwp_content_plan_rows", "source_json"),
        ("ggwp_content_plan_rows", "editable_json"),
        ("ggwp_content_plan_commands", "receipt_json"),
        ("ggwp_content_plan_previews", "receipt_json"),
        ("ggwp_content_plan_exports", "receipt_json"),
        ("ggwp_feedback_plan_links", "receipt_json"),
        ("ggwp_feedback_plan_links", "basis_json"),
        ("ggwp_feedback_plan_link_commands", "receipt_json"),
        ("ggwp_feedback_plan_link_commands", "basis_json"),
    ]
    expected = {f"{table}.{column}": 1 for table, column in protected}
    expected |= {"ggwp_content_plans.title": 1, "ggwp_content_plan_exports.csv_bytes": csv_hits}
    before = {f"{table}.{column}": workbench.fetch(f"SELECT {column}::text FROM deerflow.{table}") for table, column in protected}
    counts, threads = workbench.check()
    assert threads == []
    assert {key: counts.get(key) for key in expected} == expected
    output = workbench.run(CHECK, "-v", "disk_threads=")
    assert "synthetic-completion" not in output and "synthetic-title" not in output and "synthetic.csv" not in output
    assert workbench.redact() == CLEARED_NONE
    assert {f"{table}.{column}": workbench.fetch(f"SELECT {column}::text FROM deerflow.{table}") for table, column in protected} == before
    assert workbench.fetch("SELECT receipt_json::text,csv_bytes FROM deerflow.ggwp_content_plan_exports") == [(receipt, csv_bytes)]
    assert workbench.fetch("SELECT title FROM deerflow.ggwp_content_plans") == [("pan.quark.cn/s/synthetic-title",)]
    assert {key: workbench.check()[0].get(key) for key in expected} == expected
