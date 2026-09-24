"""board_fixture (P3-1): the database the pick data board's integration tests and e2e read.

PostgreSQL only (skipped when PICK_TEST_PG_URL is unset). One board is built for the module through the command line
entry, read back as the reader it creates, and taken down at the end. Every value is synthetic.
"""

import io
import json
import os
import re
import stat
from contextlib import closing
from pathlib import Path

import board_fixture as bf
import pytest
from gate_world import b64url

from ggwork_pick.mirror.publish import READER_ROLE_ENV

PASSWORD = "board-fixture-test-password"


def _connect(url: str):
    import psycopg

    return closing(psycopg.connect(url, autocommit=True))


def _admin_url(cluster, database: str) -> str:
    return cluster.url.set(drivername="postgresql", database=database).render_as_string(hide_password=False)


def _rows(url: str, statement: str, *params) -> list[tuple]:
    with _connect(url) as conn:
        return conn.execute(statement, params).fetchall()


@pytest.fixture(scope="module")
def board(pg_cluster, tmp_path_factory):
    work = tmp_path_factory.mktemp("board")
    url_file = work / "reader.url"
    out = io.StringIO()
    before = os.environ.get(READER_ROLE_ENV)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(bf.PASSWORD_ENV, PASSWORD)
        assert bf.main(["up", "--url-file", str(url_file), "--data-dir", str(work / "files")], out=out) == 0
    info = json.loads(out.getvalue())
    try:
        yield {
            "info": info,
            "output": out.getvalue(),
            "url_file": url_file,
            "reader": url_file.read_text(encoding="utf-8"),
            "env_before": before,
            "data_dir": work / "files",
        }
    finally:
        assert bf.main(["down", "--database", info["database"], "--role", info["role"]], out=io.StringIO()) == 0


def test_output_names_only_and_the_url_file_is_private(board):
    info = board["info"]
    assert set(info) == {"database", "role", "data_dir", "versions"}
    assert PASSWORD not in board["output"]
    assert stat.S_IMODE(board["url_file"].stat().st_mode) == 0o600
    assert f"{info['role']}:{PASSWORD}@" in board["reader"]
    assert board["reader"].endswith(f"/{info['database']}")
    # up leaves the process environment as it found it
    assert os.environ.get(READER_ROLE_ENV) == board["env_before"]


def test_reader_session_is_set_up_like_the_bootstrap(board):
    with _connect(board["reader"]) as conn:
        settings = {name: conn.execute(f"SHOW {name}").fetchone()[0] for name in ("default_transaction_read_only", "search_path", "TimeZone")}
        timeout = conn.execute("SHOW statement_timeout").fetchone()[0]
        who = conn.execute("SELECT current_user").fetchone()[0]
    assert settings == {"default_transaction_read_only": "on", "search_path": "pick_mirror", "TimeZone": "UTC"}
    assert timeout == "8s"
    assert who == board["info"]["role"]


def test_versions_in_every_state(board):
    ids = board["info"]["versions"]
    rows = dict(_rows(board["reader"], "SELECT id::int, status FROM pick_mirror.versions"))
    assert rows == {ids["dropped"]: "dropped", ids["v1"]: "published", ids["v2"]: "published", ids["building"]: "building", ids["failed"]: "failed"}
    current = _rows(board["reader"], "SELECT id::int FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1")
    assert current == [(ids["v2"],)]


def test_reader_reads_published_versions_only(board):
    ids = board["info"]["versions"]
    query = (
        "SELECT v.id::int, n.nspname IS NOT NULL, coalesce(has_schema_privilege(n.oid, 'USAGE'), false)"
        " FROM pick_mirror.versions v LEFT JOIN pg_namespace n ON n.nspname = v.schema_name"
    )
    seen = {row[0]: row[1:] for row in _rows(board["reader"], query)}
    assert seen == {
        ids["dropped"]: (False, False),
        ids["v1"]: (True, True),
        ids["v2"]: (True, True),
        ids["building"]: (True, False),
        ids["failed"]: (False, False),
    }


def test_reader_may_not_read_control_nor_write(board):
    import psycopg

    with _connect(board["reader"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM pick_mirror.control")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TEMP TABLE scratch (n int)")


def test_the_two_published_versions_differ(board):
    ids = board["info"]["versions"]
    titles, rules = {}, {}
    for name in ("v1", "v2"):
        schema = f"pickm_v{ids[name]:06d}"
        titles[name] = _rows(board["reader"], f"SELECT title FROM {schema}.catalog_rows WHERE row_key = 'c-1'")[0][0]
        rules[name] = _rows(board["reader"], f"SELECT value -> 'platformRules' -> 'shortmax' ->> 'yt' FROM {schema}.meta WHERE key = 'rules'")[0][0]
    assert titles == {"v1": bf.V1_TITLE, "v2": bf.V2_TITLE}
    assert rules == {"v1": "ok", "v2": "no"}
    platforms = _rows(board["reader"], f"SELECT row_key, platform FROM pickm_v{ids['v2']:06d}.catalog_rows ORDER BY row_key")
    assert platforms == list(zip(("c-1", "c-2", "c-3", "c-4"), bf.PLATFORMS, strict=True))


def test_each_published_version_is_paired_with_its_v1_batches(pg_cluster, board):
    info = board["info"]
    admin = _admin_url(pg_cluster, info["database"])
    pairs = _rows(admin, "SELECT id::int, agent_catalog_batch_id, agent_knowledge_batch_id FROM pick_mirror.versions WHERE status = 'published'")
    assert len(pairs) == 2 and all(catalog and knowledge for _, catalog, knowledge in pairs)
    batches = dict(_rows(admin, "SELECT id, status FROM deerflow.ggwp_import_batches"))
    assert all(batches[catalog] == "published" and batches[knowledge] == "published" for _, catalog, knowledge in pairs)
    v2_catalog = next(catalog for version, catalog, _ in pairs if version == info["versions"]["v2"])
    identities = {row[0] for row in _rows(admin, "SELECT identity FROM deerflow.ggwp_drama_versions WHERE batch_id = %s", v2_catalog)}
    assert json.dumps(["realshort-pick", b64url("c-1"), "英语"], ensure_ascii=False, separators=(",", ":")) in identities


def test_series_for_one_canonical_drama(board):
    state = _rows(board["reader"], "SELECT through::text, trimmed_before::text FROM pick_mirror.series_state WHERE id = 1")
    assert state == [("2026-09-23", "2026-06-25")]
    series = _rows(board["reader"], "SELECT drama_id, cardinality(days) FROM pick_mirror.series")
    assert series == [(bf.SERIES_DRAMA, 3)]


def test_reelshort_ids_are_shaped_like_book_ids_and_resolve_as_realshort_does(board):
    """reelshortId (request.ts) takes [a-z0-9]{6,40} only; a sibling resolves to its canonical, rs0007 to none."""
    schema = f"pickm_v{board['info']['versions']['v2']:06d}"
    drama_ids = [row[0] for row in _rows(board["reader"], f"SELECT drama_id FROM {schema}.rs_rows ORDER BY drama_id")]
    assert drama_ids == ["rs0001", "rs0002", "rs0003", "rs0004", "rs0005"]
    assert all(re.fullmatch(r"[a-z0-9]{6,40}", drama_id) for drama_id in drama_ids)
    canonical = dict(_rows(board["reader"], f"SELECT id, canonical_id FROM {schema}.rs_ids WHERE id IN ('rs0006', 'rs0007')"))
    assert canonical == {"rs0006": "rs0001", "rs0007": None}
    bills = _rows(board["reader"], f"SELECT book_id, canonical_id FROM {schema}.rs_bill_orders ORDER BY bill_date")
    assert bills == [("rs0001", "rs0001"), ("book-x", None)]
    counts = _rows(board["reader"], f"SELECT value -> 'rs_ids' FROM {schema}.meta WHERE key = 'counts'")
    assert counts == [(7,)]


def test_v1_identities_name_the_mirror_row_keys(pg_cluster, board):
    """The agent's v1 identities and the board's row keys are the same keys: replay (P4-2) maps one onto the other."""
    info = board["info"]
    admin = _admin_url(pg_cluster, info["database"])
    schema = f"pickm_v{info['versions']['v2']:06d}"
    catalog = _rows(admin, "SELECT agent_catalog_batch_id FROM pick_mirror.versions WHERE id = %s", info["versions"]["v2"])[0][0]
    identities = {row[0] for row in _rows(admin, "SELECT identity FROM deerflow.ggwp_drama_versions WHERE batch_id = %s", catalog)}
    keys = {row[0] for row in _rows(board["reader"], f"SELECT row_key FROM {schema}.rs_rows WHERE has_signal")}
    for key in keys:
        assert json.dumps(["realshort-pick", b64url(key), "英语"], ensure_ascii=False, separators=(",", ":")) in identities


def test_down_refuses_names_it_did_not_make():
    assert bf.main(["down", "--database", "postgres", "--role", "pick_board_reader_000000000000"], out=io.StringIO()) == 2
    assert bf.main(["down", "--database", "pick_board_000000000000", "--role", "postgres"], out=io.StringIO()) == 2


def test_blobs_land_in_the_data_dir_itself_as_absolute_paths(pg_cluster, board):
    """The gateway roots PickService at its data dir (ggwork_pick/__init__.py): the blobs go straight under it."""
    info = board["info"]
    assert info["data_dir"] == str(board["data_dir"].resolve())
    paths = [row[0] for row in _rows(_admin_url(pg_cluster, info["database"]), "SELECT raw_blob_path FROM deerflow.ggwp_import_batches")]
    assert paths
    for path in map(Path, paths):
        assert path.is_absolute() and path.parent.parent == Path(info["data_dir"]) and path.is_file()


def test_the_data_dir_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert bf.gateway_data_dir(Path("gateway-data")) == tmp_path.resolve() / "gateway-data"
    made = bf.gateway_data_dir(None)
    try:
        assert made.is_absolute() and made.is_dir() and made.name.startswith("pick-board-")
    finally:
        made.rmdir()
