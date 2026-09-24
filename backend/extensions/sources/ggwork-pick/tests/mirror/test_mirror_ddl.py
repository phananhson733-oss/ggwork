"""The version DDL against the row contract, schema names and create_version's arguments (P2-3; U1, U3, U6).

Nothing here needs a database: ddl.sql is parsed as text, and every check that must run before SQL is sent uses a
connection that fails when touched.
"""

import re
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import pytest
from mirror_rows import MANIFEST, TABLES, NoSql, version_args

from ggwork_pick.mirror.contracts import FORBIDDEN_NAME, RESOURCE_COLUMNS

PROJECT = Path(__file__).resolve().parents[2]
DDL_PATH = PROJECT / "ggwork_pick" / "mirror" / "ddl.sql"
# The P2-3 type table (U3: int is integer, float is double precision), written out by hand.
SQL_TYPES = {
    "text": "text",
    "day": "text",
    "ts": "timestamptz",
    "bool": "boolean",
    "int": "integer",
    "float": "double precision",
    "text[]": "text[]",
    "json": "jsonb",
}
BAD_NAMES = (
    "pickm_v00012",
    "pickm_v0001234",
    "pickm_v000123\n",
    "PICKM_V000123",
    "pickm_v00012\u0663",
    "pickm_v00012\uff13",
    'pickm_v000123"; DROP SCHEMA public CASCADE; --',
    "public",
    "pick_mirror",
    "",
    None,
    b"pickm_v000123",
)


def _ddl_tables(text: str) -> dict[str, list[tuple[str, str, bool]]]:
    """table -> [(column, SQL type, nullable)] from ddl.sql's CREATE TABLE statements."""
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    tables = {}
    for statement in (part.strip() for part in body.split(";")):
        if not statement:
            continue
        match = re.fullmatch(r"CREATE TABLE __SCHEMA__\.([a-z0-9_]+) \((.*)\)", statement, re.DOTALL)
        assert match is not None, f"not a CREATE TABLE under __SCHEMA__: {statement[:60]}"
        columns = []
        for line in match.group(2).split(","):
            column = re.fullmatch(r"([a-z0-9_]+) ([a-z ]+(?:\[\])?)( NOT NULL)?", line.strip())
            assert column is not None, line
            columns.append((column.group(1), column.group(2), column.group(3) is None))
        assert match.group(1) not in tables
        tables[match.group(1)] = columns
    return tables


def _expected_tables() -> dict[str, list[tuple[str, str, bool]]]:
    tables = {table: [(column.name, SQL_TYPES[column.type], column.nullable) for column in RESOURCE_COLUMNS[table]] for table in TABLES}
    return {**tables, "meta": [("key", "text", False), ("value", "jsonb", False)]}


def test_ddl_sql_matches_the_row_contract_column_by_column():
    # The guard against drift: RealShort changes a column, the contract follows, and this fails until ddl.sql does.
    assert _ddl_tables(DDL_PATH.read_text(encoding="utf-8")) == _expected_tables()


def test_ddl_sql_only_creates_tables_under_the_placeholder():
    text = DDL_PATH.read_text(encoding="utf-8")
    code = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("--")]
    statements = [line for line in code if not line.startswith(" ") and line != ");"]
    assert statements and all(re.fullmatch(r"CREATE TABLE __SCHEMA__\.[a-z0-9_]+ \(", line) for line in statements)
    # No keys or indexes yet: they are built after COPY (U4).
    assert not re.search(r"(?i)\b(PRIMARY|UNIQUE|INDEX|REFERENCES|DEFAULT|ALTER|DROP|GRANT|SET|INSERT)\b", "\n".join(code))


def test_ddl_sql_has_no_forbidden_column():
    names = [name for columns in _ddl_tables(DDL_PATH.read_text(encoding="utf-8")).values() for name, _, _ in columns]
    assert [name for name in names if FORBIDDEN_NAME.search(name)] == []
    for name in ("pan_url", "pan_pw", "creator", "bill_usd", "group_key", "revenue_usd", "promotion_value"):
        assert name not in names
    assert "has_pan" in names and "bill_rank" in names


def test_the_package_reads_ddl_sql_through_importlib_resources():
    from ggwork_pick.mirror import versions

    packaged = resources.files("ggwork_pick.mirror").joinpath("ddl.sql").read_text(encoding="utf-8")
    assert versions.ddl_template() == packaged == DDL_PATH.read_text(encoding="utf-8")
    assert versions.SQL_TYPES == SQL_TYPES
    assert versions.MIRROR_TABLES == (*TABLES, "meta")
    assert {table: [(c.name, c.type, c.nullable) for c in columns] for table, columns in versions.TABLE_COLUMNS.items() if table != "meta"} == {
        table: [(c.name, c.type, c.nullable) for c in RESOURCE_COLUMNS[table]] for table in TABLES
    }


def test_ddl_for_substitutes_only_a_checked_name():
    from ggwork_pick.mirror.versions import ddl_for

    script = ddl_for("pickm_v000123")
    assert "__SCHEMA__" not in script
    assert script.count("CREATE TABLE pickm_v000123.") == len(TABLES) + 1
    for bad in BAD_NAMES:
        with pytest.raises(ValueError):
            ddl_for(bad)


def test_schema_name_validation():
    from ggwork_pick.mirror.versions import check_schema_name, schema_for

    assert schema_for(123) == "pickm_v000123"
    assert schema_for(1) == "pickm_v000001" and schema_for(999_999) == "pickm_v999999"
    for bad in (0, -1, 1_000_000, True, 1.0, "123", None):
        with pytest.raises(ValueError):
            schema_for(bad)
    assert check_schema_name("pickm_v000123") == "pickm_v000123"
    for bad in BAD_NAMES:
        with pytest.raises(ValueError):
            check_schema_name(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_NAMES)
async def test_a_bad_schema_name_is_refused_before_any_sql(bad):
    from ggwork_pick.mirror.versions import drop_version_schema

    conn = NoSql()
    with pytest.raises(ValueError):
        await drop_version_schema(conn, bad)
    assert conn.touched == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"as_of": datetime(2026, 9, 23, 10, 15)},
        {"as_of": datetime(2026, 9, 23, 10, 15, 30, tzinfo=UTC)},
        {"as_of": "2026-09-23T10:15:00.000Z"},
        {"fingerprint": "A" * 64},
        {"fingerprint": "a" * 63},
        {"fingerprint": "a" * 64 + "\n"},
        {"counts": {**MANIFEST["counts"], "rs_series_day": 1}},
        {"counts": {key: value for key, value in MANIFEST["counts"].items() if key != "rs_ids"}},
        {"counts": {**MANIFEST["counts"], "rs_ids": -1}},
        {"counts": {**MANIFEST["counts"], "rs_ids": True}},
        {"latest_snapshot": "2026-9-23"},
        {"latest_snapshot": "2026-02-30"},
        {"freshness": ["not", "an", "object"]},
        {"warnings": {"code": "x"}},
        {"freshness": {"rows": float("nan")}},
        {"sync_run_id": 7},
    ],
)
async def test_create_version_checks_its_arguments_before_any_sql(overrides):
    from ggwork_pick.mirror.versions import create_version

    conn = NoSql()
    with pytest.raises(ValueError):
        await create_version(conn, **version_args(**overrides))
    assert conn.touched == []


def test_package_data_in_wheel(tmp_path):
    # The image installs a wheel built from this project (backend/pyproject.toml path dependency), not the source tree
    # importlib.resources reads above. Built offline, so skipped where uv or its cached build backend is missing.
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("没有 uv，无法离线构建 wheel")
    command = [uv, "build", "--wheel", "--offline", "--no-config", "--quiet", "-o", str(tmp_path), str(PROJECT)]
    built = subprocess.run(command, capture_output=True, timeout=120, check=False)
    if built.returncode != 0:
        pytest.skip(f"离线构建 wheel 不可用（uv 退出码 {built.returncode}）")
    (wheel,) = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("ggwork_pick/mirror/ddl.sql") == DDL_PATH.read_bytes()
        assert "ggwork_pick/mirror/pan_rules.json" in archive.namelist()


@pytest.mark.asyncio
async def test_a_missing_ddl_file_stops_create_version_before_any_sql(monkeypatch):
    from ggwork_pick.mirror import versions

    def missing() -> str:
        raise FileNotFoundError("ddl.sql")

    monkeypatch.setattr(versions, "ddl_template", missing)
    conn = NoSql()
    with pytest.raises(FileNotFoundError):
        await versions.create_version(conn, **version_args())
    assert conn.touched == []
