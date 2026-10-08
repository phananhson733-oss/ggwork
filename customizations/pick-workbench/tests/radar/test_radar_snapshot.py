import copy
import hashlib
import json
from datetime import date, timedelta

import pytest

SHA = "c359719b80840c2fc27e2255631a83780f60c8c62b19dca3dbe48e2de1eb314c"


def functions():
    from ggwork_pick.radar.snapshot import load_snapshot, normalize_record

    return load_snapshot, normalize_record


def record(values=None):
    values = values if values is not None else [20] * 14
    return dict(
        id=1,
        clean_title="sample",
        original_title="Sample",
        platforms='["ReelShort"]',
        genres="[]",
        promo_tags='["Hot"]',
        platform_urls="{}",
        episodes=10,
        platform_count=1,
        has_promo_tag=1,
        geo="US",
        timeframe="today 1-m",
        avg_heat=20,
        peak_heat=20,
        momentum=0,
        fetched_at=1791440000,
        timeline_data=json.dumps([{"date": str(date(2026, 9, 1) + timedelta(days=i)), "value": v} for i, v in enumerate(values)]),
    )


@pytest.mark.parametrize(
    "change,status",
    [
        (
            {"timeline_data": "[]", "avg_heat": 5, "peak_heat": 10, "momentum": 0},
            "fallback_unverified",
        ),
        ({"timeline_data": None}, "missing_series"),
        ({"timeline_data": "[]"}, "missing_series"),
        ({"timeline_data": "{oops"}, "invalid_series"),
        ({"timeline_data": "{}"}, "invalid_series"),
        ({"timeline_data": "[null]"}, "invalid_series"),
        ({"timeline_data": '[{"date":"wrong","value":20}]'}, "invalid_series"),
        ({"timeline_data": '[{"date":"2026-09-01","value":true}]'}, "invalid_series"),
        ({"timeline_data": '[{"date":"2026-09-01","value":NaN}]'}, "invalid_series"),
        (
            {"timeline_data": '[{"date":"2026-09-01","value":Infinity}]'},
            "invalid_series",
        ),
        ({"timeline_data": '[{"date":"2026-09-01","value":-1}]'}, "invalid_series"),
        ({"timeline_data": '[{"date":"2026-09-01","value":101}]'}, "invalid_series"),
    ],
)
def test_states_and_null_scores(change, status):
    _, normalize = functions()
    row = record()
    row.update(change)
    result = normalize(row, None)
    assert result["series_status"] == status
    assert result["pilot"]["score"] is None
    assert result["pilot"]["tier"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("days", [[0, 0], [1, 0], [0, 2]])
def test_duplicate_reverse_missing_dates(days):
    _, normalize = functions()
    row = record()
    row["timeline_data"] = json.dumps([{"date": str(date(2026, 9, 1) + timedelta(days=i)), "value": 10} for i in days])
    assert normalize(row, None)["series_status"] == "invalid_series"


def test_bad_metadata_isolated_without_destroying_valid_curve():
    _, normalize = functions()
    good = record()
    bad = record()
    bad["promo_tags"] = "{bad"
    bad["avg_heat"] = float("inf")
    a, b = normalize(good, None), normalize(bad, {"score": float("nan")})
    assert a["pilot"]["score"] is not None
    assert b["series_status"] == "historical_data"
    assert b["record_errors"]
    assert b["legacy_replay"] is None
    assert b["pilot"]["score"] is None
    assert b["original"]["score"] is None
    json.dumps(b, allow_nan=False)


def test_valid_zero_and_growth_do_not_turn_missing_into_zero():
    _, normalize = functions()
    zero = normalize(record([0] * 14), None)
    assert zero["series_status"] == "historical_zero"
    assert len(zero["timeline_data"]) == 14
    assert zero["pilot"]["growth_pct"] is None
    assert zero["pilot"]["growth_state"] == "zero_window"
    new = normalize(record([0] * 7 + [10] * 7), None)
    assert new["pilot"]["growth_pct"] is None
    assert new["pilot"]["growth_state"] == "from_zero"
    assert normalize(record([10] * 7 + [20] * 7), None)["pilot"]["growth_pct"] == 100
    assert normalize(record([10] * 13), None)["pilot"]["score"] is None


def test_load_rejects_missing_hash_and_corruption(tmp_path):
    load, _ = functions()
    with pytest.raises((ValueError, FileNotFoundError)):
        load(tmp_path / "absent.db", expected_hash=SHA)
    assert not (tmp_path / "absent.db").exists()
    wrong = tmp_path / "wrong.db"
    wrong.write_bytes(b"synthetic wrong input")
    with pytest.raises(ValueError, match="hash"):
        load(wrong, expected_hash="0" * 64)
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not sqlite")
    with pytest.raises(ValueError):
        load(broken, expected_hash=hashlib.sha256(broken.read_bytes()).hexdigest())


def test_determinism_and_no_mutation():
    _, normalize = functions()
    row = record()
    original = copy.deepcopy(row)
    assert normalize(row, None) == normalize(row, None)
    assert row == original


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_huge_json_integer_is_a_bad_row_not_an_exception(value):
    _, normalize = functions()
    row = record([value] * 14)
    result = normalize(row, None)
    assert result["series_status"] == "invalid_series"
    assert result["pilot"]["score"] is None


def test_derived_percentage_overflow_does_not_break_json():
    _, normalize = functions()
    result = normalize(record([1e-310] * 7 + [100] * 7), None)
    json.dumps(result, allow_nan=False)
    assert result["pilot"]["growth_pct"] is None
    assert any(e["field"] == "growth_pct" for e in result["record_errors"])


def test_sqlite_blobs_cannot_escape_score_adapters():
    _, normalize = functions()
    row = record()
    row["clean_title"] = b"\xff"
    result = normalize(row, {"score": b"\xff"})
    json.dumps(result, allow_nan=False)
    assert result["clean_title"] is None
    assert result["original"]["score"] is None
    assert result["legacy_replay"].get("clean_title") is None


@pytest.mark.parametrize("field,value", [("avg_heat", -1), ("peak_heat", 101), ("momentum", 201)])
def test_invalid_cache_domain_not_given_legacy_score(field, value):
    _, normalize = functions()
    row = record()
    row[field] = value
    result = normalize(row, None)
    assert result["legacy_replay"] is None
    assert result["pilot"]["score"] is not None  # valid curve can independently recompute metrics
    assert any(e["field"] == field for e in result["record_errors"])


@pytest.mark.parametrize("field", ["timeline_data", "genres", "promo_tags"])
def test_deep_json_is_isolated(field):
    _, normalize = functions()
    row = record()
    row[field] = "[" * 1200 + "0" + "]" * 1200
    result = normalize(row, None)
    assert any(e["field"] == field for e in result["record_errors"])
    assert normalize(record(), None)["pilot"]["score"] is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("genres", '["\\ud800"]'),
        ("platforms", '["\\ud800"]'),
        ("platform_urls", '{"\\ud800":"https://example.com"}'),
        ("original_title", "\ud800"),
    ],
)
def test_unencodable_text_is_isolated(field, value):
    _, normalize = functions()
    row = record()
    row[field] = value
    result = normalize(row, {"tier": "\ud800"})
    json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert any(e["field"] == field for e in result["record_errors"])
    assert result["pilot"]["score"] is not None
