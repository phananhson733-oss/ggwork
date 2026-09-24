"""Strict row and manifest contracts of feed v2 against RealShort 816ca2e (export-v2-map.ts RESOURCE_SPECS, MANIFEST_SHAPE).

export_v2_contract.json was generated from RealShort itself (export_v2_contract.gen.mjs): its column lists, its manifest
shape, the manifest its tests build, and one row per resource as toExportRow writes it.
"""

import copy
import json
import re
import types
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from ggwork_pick.mirror import contracts, errors

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CONTRACT = json.loads((FIXTURES / "export_v2_contract.json").read_text(encoding="utf-8"))
# The generated rs_clicks14 sample puts a pan string in day to exercise the scrub exemption; RealShort's day there is
# to_char(..., 'YYYY-MM-DD') (export-v2.ts:242), which the contract holds it to.
REAL_DAYS = {"rs_clicks14": {"day": "2026-09-01"}}
ROWS = {resource: {**sample["output"], **REAL_DAYS.get(resource, {})} for resource, sample in CONTRACT["v2_rows"].items()}
MANIFEST = CONTRACT["manifest"]
SECRET = "SECRETVALUE"
NAN, INF = float("nan"), float("inf")


def page(resource, rows):
    return {"ok": True, "version": "pick-export-v2", "resource": resource, "asOf": MANIFEST["asOf"], "fingerprint": "a" * 64, "rows": rows, "nextCursor": None}


def row(resource, **changes):
    return {**copy.deepcopy(ROWS[resource]), **changes}


def rejected(resource, body, *secrets):
    """The page is refused with a PageContractError that names where, never what, and chains no pydantic error."""
    with pytest.raises(contracts.PageContractError) as caught:
        contracts.parse_page(resource, body)
    error = caught.value
    for secret in secrets:
        assert secret not in str(error) and secret not in repr(error)
    assert error.__cause__ is None and error.__context__ is None
    assert error.resource == resource
    return error


def test_resource_columns_match_realshort():
    specs = CONTRACT["resource_specs"]
    assert list(contracts.RESOURCE_COLUMNS) == list(specs) == list(contracts.ROW_RESOURCES)
    for resource, spec in specs.items():
        columns = [{"name": c.name, "type": c.type, "nullable": c.nullable} for c in contracts.RESOURCE_COLUMNS[resource]]
        assert columns == spec["columns"], resource
        assert list(contracts.RESOURCE_KEYS[resource]) == spec["key"], resource
    assert list(contracts.SIGNAL_PAYLOAD_KEYS) == CONTRACT["signal_payload_keys"]
    assert list(contracts.POSTED_POST_KEYS) == CONTRACT["posted_post_keys"]


def test_resource_columns_spots_easy_to_copy_wrong():
    types_of = {resource: {c.name: c for c in columns} for resource, columns in contracts.RESOURCE_COLUMNS.items()}
    assert types_of["catalog_rows"]["tags"].type == "text"
    assert "imported_at" not in types_of["rs_rows"]
    assert contracts.RESOURCE_KEYS["rs_rows"] == ("drama_id",)
    assert [c.name for c in contracts.RESOURCE_COLUMNS["catalog_rows"]][-2:] == ["imported_at", "has_pan"]
    assert types_of["rs_rows"]["publish_at"] == contracts.Column("publish_at", "ts", True)
    assert contracts.EXPORT_RESOURCES == ("manifest", *contracts.ROW_RESOURCES)


def test_resource_columns_are_immutable():
    with pytest.raises(TypeError):
        contracts.RESOURCE_COLUMNS["extra"] = ()
    assert isinstance(contracts.RESOURCE_COLUMNS["rs_ids"], tuple)
    with pytest.raises(AttributeError):
        contracts.RESOURCE_COLUMNS["rs_ids"][0].name = "x"


@pytest.mark.parametrize("resource", contracts.ROW_RESOURCES)
def test_rows_realshort_writes_parse(resource):
    parsed = contracts.parse_page(resource, page(resource, [ROWS[resource], ROWS[resource]]))
    assert isinstance(parsed, tuple) and len(parsed) == 2
    names = [c.name for c in contracts.RESOURCE_COLUMNS[resource]]
    assert contracts.row_values(parsed[0]) == tuple(ROWS[resource][name] for name in names)
    assert type(parsed[0]).resource == resource


def test_empty_page_is_fine():
    assert contracts.parse_page("rs_ids", page("rs_ids", [])) == ()


def test_text_is_kept_verbatim():
    # Unlike StrictInput, nothing strips or reshapes text; day columns are RealShort's own text (feed-map.ts feedDate).
    parsed = contracts.parse_page("catalog_posted", page("catalog_posted", [row("catalog_posted", title=" 剧名 \n", online_on="2026-09-01 12:00")]))
    assert parsed[0].title == " 剧名 \n"
    assert parsed[0].online_on == "2026-09-01 12:00"


def test_unknown_column_rejects_page():
    rows = [row("catalog_rows"), row("catalog_rows", surprise=SECRET)]
    error = rejected("catalog_rows", page("catalog_rows", rows), SECRET)
    assert error.row == 1 and error.path == "surprise"
    assert "catalog_rows" in str(error) and "surprise" in str(error)


def test_missing_column_rejects_page():
    body = row("rs_ids")
    del body["pay_start"]
    error = rejected("rs_ids", page("rs_ids", [body]))
    assert error.row == 0 and error.path == "pay_start"


def test_page_contract_error_is_a_feed_contract_error():
    # The sync sorts failures by the client's FeedError classes (errors.py): a row that breaks the contract is a ContractError.
    error = rejected("rs_ids", page("rs_ids", [row("rs_ids", surprise=SECRET)]), SECRET)
    assert isinstance(error, errors.ContractError) and isinstance(error, errors.FeedError)
    assert (error.resource, error.row, error.path, error.status) == ("rs_ids", 0, "surprise", None)


@pytest.mark.parametrize(
    ("resource", "changes", "where"),
    [
        ("catalog_rows", {"pan_url": SECRET}, "pan_url"),
        ("catalog_signals", {"payload": {"d": "2026-09-01", "bill_usd": SECRET}}, "payload.bill_usd"),
        ("catalog_posted", {"posts": [{"md": {"x": [{"Revenue_USD": SECRET}]}}]}, "posts.0.md.x.0.Revenue_USD"),
        ("rs_rows", {"tag_list": ["ok"], "promotion_link": SECRET}, "promotion_link"),
        # The fragment anywhere in the name, as RealShort's FORBIDDEN_NAME.test finds it, not only at the start.
        ("catalog_rows", {"has_pan_url": SECRET}, "has_pan_url"),
        ("catalog_signals", {"payload": {"h": [{"matchedUsd": 1}]}}, "payload.h.0.matchedUsd"),
    ],
)
def test_forbidden_key_any_depth(resource, changes, where):
    error = rejected(resource, page(resource, [row(resource, **changes)]), SECRET)
    assert error.row == 0 and error.path == where


def test_forbidden_key_in_the_manifest():
    with pytest.raises(contracts.PageContractError) as caught:
        contracts.parse_manifest(_with(MANIFEST, "meta.rules.ruleHints.x_revenue_usd", SECRET))
    assert caught.value.path == "meta.rules.ruleHints.x_revenue_usd" and SECRET not in str(caught.value)


def test_forbidden_key_in_the_envelope():
    error = rejected("rs_ids", {**page("rs_ids", []), "usd": SECRET}, SECRET)
    assert error.row is None and error.path == "usd"


@pytest.mark.parametrize(
    ("resource", "changes", "path"),
    [
        ("catalog_signals", {"payload": {"d": "x", "extra": SECRET}}, "payload.extra"),
        ("catalog_signals", {"payload": [SECRET]}, "payload"),
        ("catalog_posted", {"posts": [{"d": "x", "views2": SECRET}]}, "posts.0.views2"),
        ("catalog_posted", {"posts": {"d": SECRET}}, "posts"),
        ("catalog_posted", {"posts": [SECRET]}, "posts.0"),
    ],
)
def test_payload_posts_whitelist(resource, changes, path):
    error = rejected(resource, page(resource, [row(resource, **changes)]), SECRET)
    assert error.row == 0 and error.path == path


def test_payload_and_posts_keys_are_optional_and_values_loose():
    payload = {"h": [["2026-09-01", 3, {"n": None}]], "qy": 1.5}
    posts = [{}, {"md": {"n": 1}, "views": None, "note": ["a"]}]
    signal = contracts.parse_page("catalog_signals", page("catalog_signals", [row("catalog_signals", payload=payload)]))[0]
    posted = contracts.parse_page("catalog_posted", page("catalog_posted", [row("catalog_posted", posts=posts)]))[0]
    names = [c.name for c in contracts.RESOURCE_COLUMNS["catalog_signals"]]
    assert contracts.row_values(signal)[names.index("payload")] == payload
    names = [c.name for c in contracts.RESOURCE_COLUMNS["catalog_posted"]]
    assert contracts.row_values(posted)[names.index("posts")] == posts


@pytest.mark.parametrize(
    ("resource", "column", "value"),
    [
        ("catalog_rows", "imported_at", "2026-09-01T00:00:00Z"),
        ("catalog_rows", "imported_at", "2026-09-01T00:00:00.000+00:00"),
        ("catalog_rows", "imported_at", "2026-02-30T00:00:00.000Z"),
        ("catalog_rows", "imported_at", "２026-09-01T00:00:00.000Z"),
        ("catalog_rows", "imported_at", "2026-09-01T00:00:00.83Z"),
        ("catalog_rows", "episodes", "5"),
        ("catalog_rows", "episodes", True),
        ("catalog_rows", "episodes", 5.0),
        ("catalog_rows", "merged_rows", 2**53),
        ("catalog_rows", "merged_rows", -(2**53)),
        ("catalog_rows", "youtube", "t"),
        ("catalog_rows", "youtube", 1),
        ("catalog_rows", "title", None),
        ("catalog_rows", "title", 5),
        ("catalog_rows", "in_site_ids", "a,b"),
        ("catalog_rows", "in_site_ids", [1]),
        ("catalog_rows", "in_site_ids", [None]),
        ("rs_rows", "rr", "1.5"),
        ("rs_rows", "rr", None),
        ("rs_rows", "rr", True),
        # json.loads reads NaN and Infinity; RealShort never sends them (isJsonValue, toFloat), and PG float8/jsonb must not get them.
        ("rs_rows", "rr", NAN),
        ("rs_rows", "rr7", INF),
        ("catalog_signals", "payload", {"qy": NAN}),
        ("catalog_posted", "posts", [{"views": INF}]),
        ("rs_clicks14", "day", None),
        # rs_clicks14.day is SQL-formatted (export-v2.ts:242): a real YYYY-MM-DD in ASCII digits, nothing else.
        ("rs_clicks14", "day", "2026-09-01 00:00"),
        ("rs_clicks14", "day", "2026-02-30"),
        ("rs_clicks14", "day", "２026-09-01"),
        ("rs_ids", "canonical_id", 7),
    ],
)
def test_strict_types(resource, column, value):
    error = rejected(resource, page(resource, [row(resource, **{column: value})]))
    assert error.row == 0 and error.path.split(".")[0] == column


def test_parsed_rows_are_frozen():
    parsed = contracts.parse_page("rs_ids", page("rs_ids", [row("rs_ids")]))[0]
    with pytest.raises(ValidationError):
        parsed.title = "x"
    with pytest.raises(ValidationError):
        contracts.parse_manifest(MANIFEST).meta.control.ledger.rows = 0


def test_error_path_parts_are_cut():
    # Key names are not values, but nothing bounds their length: each piece of a path is cut to PATH_PART_MAX.
    long_key = "k" * 200
    error = rejected("rs_ids", page("rs_ids", [row("rs_ids", **{long_key: SECRET})]), SECRET)
    assert error.path == "k" * contracts.PATH_PART_MAX and long_key not in str(error)


def test_float_takes_an_int_and_nullable_takes_null():
    parsed = contracts.parse_page("rs_rows", page("rs_rows", [row("rs_rows", rr=2, rr7=None, publish_at=None, metrics_valid=None)]))[0]
    assert parsed.rr == 2.0 and parsed.rr7 is None and parsed.publish_at is None


@pytest.mark.parametrize(("changes", "where"), [({"title": "a\x00b"}, "title"), ({"\ud800x": 1}, "?x")])
def test_nul_and_lone_surrogate_page(changes, where):
    error = rejected("rs_ids", page("rs_ids", [row("rs_ids", **changes)]))
    assert error.row == 0 and error.path == where
    assert "\x00" not in str(error) and "\ud800" not in str(error)


def test_page_shape_is_checked():
    rejected("rs_ids", [])
    rejected("rs_ids", {"rows": {"id": SECRET}}, SECRET)
    assert rejected("rs_ids", page("rs_ids", [row("rs_ids"), SECRET]), SECRET).row == 1
    with pytest.raises(ValueError):
        contracts.parse_page("rs_nothing", page("rs_nothing", []))


def test_manifest_model():
    manifest = contracts.parse_manifest(MANIFEST)
    # Not "Manifest": that is the client's checked manifest (feed_shape.Manifest, P2-2a), a different object.
    assert isinstance(manifest, contracts.ManifestModel) and not hasattr(contracts, "Manifest")
    assert manifest.counts.rs_rows == 1 and manifest.snapshotDays[0].rows == 30000
    assert manifest.meta.scrub == {"catalog_signals.payload.h[*][*]": 2}
    assert contracts.parse_page("manifest", page("manifest", [MANIFEST])) == (manifest,)
    meta = {**MANIFEST["meta"], "debug": {"n": 1}}
    with pytest.raises(contracts.PageContractError) as caught:
        contracts.parse_manifest({**MANIFEST, "meta": meta})
    assert caught.value.path == "meta.debug" and caught.value.resource == "manifest" and caught.value.row is None


def _with(value, path, leaf):
    """A deep copy of value with leaf set at a dotted path (list indexes as numbers)."""
    head, _, rest = path.partition(".")
    key = int(head) if isinstance(value, list) else head
    inner = leaf if not rest else _with(value[key], rest, leaf)
    if isinstance(value, list):
        return [inner if i == key else copy.deepcopy(item) for i, item in enumerate(value)]
    return {**copy.deepcopy(value), key: inner}


@pytest.mark.parametrize(
    "path",
    [
        "debug",
        "counts.rs_series_day",
        "snapshotDays.0.extra",
        "meta.freshness.extra",
        "meta.growthBaseline.30",
        "meta.sources.mystery",
        "meta.sources.bill.details.token",
        "meta.rules.extra",
        "meta.rules.platformRules.newtheater",
        "meta.rules.glossary.0.items.0.extra",
        "meta.control.extra",
        "meta.control.rankCounts.newkind",
        "meta.control.facetsPick.posted.extra",
        "meta.warnings.0.extra",
    ],
)
def test_manifest_rejects_a_key_at_every_level(path):
    with pytest.raises(contracts.PageContractError) as caught:
        contracts.parse_manifest(_with(MANIFEST, path, SECRET))
    assert SECRET not in str(caught.value)
    assert caught.value.path == path


@pytest.mark.parametrize("path", ["version", "counts.rs_rows", "meta.rsCounts.ledger", "meta.control.ledger.orders", "meta.growthBaseline.1.baselineDay"])
def test_manifest_refuses_a_missing_required_key(path):
    head, _, leaf = path.rpartition(".")
    parent = MANIFEST
    for part in head.split(".") if head else []:
        parent = parent[part]
    without = {k: v for k, v in parent.items() if k != leaf}
    body = _with(MANIFEST, head, without) if head else without
    with pytest.raises(contracts.PageContractError):
        contracts.parse_manifest(body)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("version", "pick-export-v3"),
        ("asOf", "2026-09-23T10:15:00Z"),
        ("fingerprint", "A" * 64),
        ("sourceRevision", 5),
        ("counts.rs_ids", -1),
        ("counts.rs_ids", "1"),
        ("snapshotDays.0.rows", -1),
        # snapshotDays days are what rs_series_day's day parameter must be (export-v2-page.ts:49-54), as P2-2a checks them.
        ("snapshotDays.0.day", "2026-09-23T00:00"),
        ("snapshotDays.0.day", "2026-02-30"),
        ("meta.scrub", {"x": "1"}),
        ("meta.freshness.rows", NAN),
        ("meta.control.rankCounts.kd", INF),
    ],
)
def test_manifest_identity_fields_are_typed(path, value):
    with pytest.raises(contracts.PageContractError) as caught:
        contracts.parse_manifest(_with(MANIFEST, path, value))
    assert caught.value.path.startswith(path)


def test_manifest_page_needs_exactly_one_row():
    assert rejected("manifest", page("manifest", [])).path == "rows"
    assert rejected("manifest", page("manifest", [MANIFEST, MANIFEST])).path == "rows"


@pytest.mark.parametrize(("path", "leaf"), [("meta.rules.postedPoolUrl", "a" + chr(0)), ("meta.control.extra", SECRET)])
def test_manifest_page_errors_have_no_row_index(path, leaf):
    # The manifest page's one row is the manifest itself: errors name a path from the manifest, never a row index.
    error = rejected("manifest", page("manifest", [_with(MANIFEST, path, leaf)]), SECRET)
    assert error.row is None and error.path == path


def _shape_of(annotation):
    """RealShort's Shape vocabulary for a model annotation: obj (fields by alias), arr, map (any key) or scalar."""
    args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return _shape_of(args[0]) if len(args) == 1 else {"kind": "scalar"}
    if typing.get_origin(annotation) is list:
        return {"kind": "array", "of": _shape_of(args[0])}
    if typing.get_origin(annotation) is dict:
        return {"kind": "map", "of": _shape_of(args[1])}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {"kind": "object", "fields": {field.alias or name: _shape_of(field.annotation) for name, field in annotation.model_fields.items()}}
    return {"kind": "scalar"}


def _realshort_shape(shape):
    """The same vocabulary for MANIFEST_SHAPE as RealShort serialized it: a record with fixed keys is an object with optional fields."""
    kind = shape["kind"]
    if kind == "object":
        return {"kind": "object", "fields": {key: _realshort_shape(child) for key, child in shape["fields"].items()}}
    if kind == "array":
        return {"kind": "array", "of": _realshort_shape(shape["of"])}
    if kind == "record" and shape["keys"] is not None:
        return {"kind": "object", "fields": {key: _realshort_shape(shape["of"]) for key in shape["keys"]}}
    if kind == "record":
        return {"kind": "map", "of": _realshort_shape(shape["of"])}
    return {"kind": "scalar"}


def test_manifest_shape_matches_realshort():
    assert _shape_of(contracts.ManifestModel) == _realshort_shape(CONTRACT["manifest_shape"])


def test_forbidden_name_matches_realshort():
    assert contracts.FORBIDDEN_NAME.pattern == CONTRACT["forbidden_name"]["source"]
    assert CONTRACT["forbidden_name"]["flags"] == "i"
    assert contracts.FORBIDDEN_NAME.flags & (re.IGNORECASE | re.ASCII) == re.IGNORECASE | re.ASCII
    # JS /i without u folds ASCII only: the Kelvin sign is not a k, the long s is not an s.
    assert contracts.FORBIDDEN_NAME.search("uſd") is None and contracts.FORBIDDEN_NAME.search("has_pan") is None
    assert contracts.FORBIDDEN_NAME.search("Bill_USD") is not None


def test_timestamps_become_aware_datetimes():
    assert contracts.ts_datetime("2026-09-01T06:54:13.830Z") == datetime(2026, 9, 1, 6, 54, 13, 830000, tzinfo=UTC)
    with pytest.raises(ValueError):
        contracts.ts_datetime("2026-09-01T06:54:13Z")
