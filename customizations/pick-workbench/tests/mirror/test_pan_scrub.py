"""The pan scrub port against RealShort 816ca2e: the shared fixture case by case, the exemptions, and scanning leaf by leaf.

pan_scrub_cases.json is a byte copy of RealShort's tests/fixtures/pan-scrub-cases.json (SOURCE.json); export_v2_contract.json
holds what RealShort's own scrubAllText counted for sample rows (export_v2_contract.gen.mjs), so paths are checked against it.
"""

import copy
import dataclasses
import hashlib
import json
import re
from importlib import resources
from pathlib import Path

import pytest

from ggwork_pick.mirror import pan

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASES_FILE = FIXTURES / "pan_scrub_cases.json"
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))
SOURCE = json.loads((FIXTURES / "SOURCE.json").read_text(encoding="utf-8"))
CONTRACT = json.loads((FIXTURES / "export_v2_contract.json").read_text(encoding="utf-8"))
# sha256 of RealShort 816ca2e:tests/fixtures/pan-scrub-cases.json, measured when it was copied (implementation note P2-2b).
FIXTURE_SHA256 = "40420e25aa29b8cec02268d0495cdd092529d53914b0091acc39663e27037d41"
PAN = "资源 https://pan.baidu.com/s/1AbCdEf 提取码：ab12"

# RealShort 816ca2e, src/lib/pick/export-v2-map.ts:213-216 (IDENTITY_KEYS), written out by hand.
IDENTITY_KEYS = (
    "row_key", "sd", "feishu_record", "id", "drama_id", "canonical_id", "book_id", "slug", "title_key",
    "in_site_ids", "row_keys", "drama_ids", "source_id", "detail_url", "source_ref",
)  # fmt: skip
# Same file, :217-222 (DATE_KEYS).
DATE_KEYS = (
    "listed_on", "off_on", "latest_evidence_on", "imported_at", "evidence_on", "online_on", "last_post_on", "created_on",
    "updated_on", "first_post_on", "metric_at", "as_of", "rs_clk_on", "rs_bill_on", "rs_gsc_on", "publish_at", "synced_at",
    "search_data_at", "detail_synced_at", "baseline1_at", "baseline7_at", "baseline15_at", "last_click_on", "last_bill_on",
    "day", "bill_date", "listed_at", "observed_at",
)  # fmt: skip
# Same file, :230 (SCRUB_EXEMPT_PATHS): nested exemptions, only v1 rows have them.
EXEMPT_PATHS = ("signals[*].source_ref", "signals[*].observed_at", "posted.last_post_on")


@pytest.mark.parametrize("case", CASES["cases"], ids=[case["name"] for case in CASES["cases"]])
def test_fixture_cases(case):
    assert pan.scrub_text(case["input"]) == (case["expected"], case["hits"])
    assert pan.scrub_text(case["expected"]) == (case["expected"], 0)


def test_fixture_copies_identical():
    packaged = resources.files("ggwork_pick.mirror").joinpath("pan_rules.json").read_bytes()
    assert SOURCE == {"commit": "816ca2e", "path": "tests/fixtures/pan-scrub-cases.json", "sha256": FIXTURE_SHA256}
    assert hashlib.sha256(packaged).hexdigest() == FIXTURE_SHA256
    assert hashlib.sha256(CASES_FILE.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert pan.RULES_SHA256 == FIXTURE_SHA256
    assert CASES["normalize"]["copies"] == ["delete", "keep", "value", "keyless"]
    assert CASES["normalize"]["order"] == ["entities", "form", "dots", "strip", "markup", "tags", "percent"]
    assert CASES["limits"]["probe_passes"] == 16
    assert len(CASES["cases"]) == 330


def test_rules_come_from_the_packaged_copy_and_cannot_change():
    assert pan.RULES.replacement == CASES["replacement"] == CONTRACT["replacement"]
    assert pan.RULES.probe_passes == CASES["limits"]["probe_passes"]
    assert pan.RULES.url.pattern == CASES["patterns"]["url"]["source"]
    assert pan.RULES.code.pattern == CASES["patterns"]["code"]["source"]
    # python_flags: without re.ASCII, Python folds U+212A and U+017F into ASCII letters where JS does not.
    assert CASES["patterns"]["url"]["python_flags"] == "re.IGNORECASE | re.ASCII" and CASES["patterns"]["code"]["python_flags"] == "0"
    for pattern in (pan.RULES.url, pan.RULES.detect_url):
        assert pattern.flags & (re.IGNORECASE | re.ASCII) == re.IGNORECASE | re.ASCII
    for pattern in (pan.RULES.code, pan.RULES.detect_code):
        assert pattern.flags & (re.IGNORECASE | re.ASCII) == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        pan.RULES.replacement = "x"
    with pytest.raises(TypeError):
        pan.RULES.entity_names["amp"] = "x"


@pytest.mark.parametrize(
    "change",
    [
        lambda rules: rules["patterns"]["url"].update(python_flags="re.IGNORECASE | re.UNICODE"),
        lambda rules: rules["normalize"].update(order=rules["normalize"]["order"][:-1]),
        lambda rules: rules["normalize"].update(copies=["delete", "keep"]),
        lambda rules: rules["normalize"].update(repeat=False),
        lambda rules: rules["normalize"].update(on_hit_hits=2),
        lambda rules: rules["limits"].update(on_limit_hits=0),
    ],
    ids=["flags", "order", "copies", "repeat", "hit_count", "limit_count"],
)
def test_rules_this_port_does_not_follow_fail_at_load(change):
    rules = copy.deepcopy(CASES)
    change(rules)
    with pytest.raises(ValueError):
        pan.load_rules(json.dumps(rules, ensure_ascii=False))


def test_exempt_lists_literal():
    assert pan.IDENTITY_KEYS == IDENTITY_KEYS
    assert pan.DATE_KEYS == DATE_KEYS
    assert pan.SCRUB_EXEMPT_KEYS == frozenset(IDENTITY_KEYS + DATE_KEYS)
    assert pan.SCRUB_EXEMPT_PATHS == frozenset(EXEMPT_PATHS)
    # The generated fixture read the same sets out of RealShort itself.
    assert frozenset(CONTRACT["scrub_exempt_keys"]) == pan.SCRUB_EXEMPT_KEYS
    assert frozenset(CONTRACT["scrub_exempt_paths"]) == pan.SCRUB_EXEMPT_PATHS


def test_scan_nested_leaves_not_concatenated():
    leaves = ["2026-09-01", "提取码：", "2026-09-02"]
    row = {"payload": {"h": [leaves]}}
    assert pan.scan_row("catalog_signals", row) == {}
    # Joined, the leaves read as a code; so does the jsonb::text of the row.
    assert pan.scrub_text("".join(leaves))[1] == 1
    assert pan.scrub_text(json.dumps(row, ensure_ascii=False))[1] == 1


def test_scan_skips_identity_top_level_only():
    row = {"row_key": PAN, "sd": PAN, "in_site_ids": [PAN], "evidence_on": PAN, "payload": {"d": PAN, "row_key": PAN, "day": PAN}}
    assert pan.scan_row("catalog_signals", row) == {
        "catalog_signals.payload.d": 1,
        "catalog_signals.payload.row_key": 1,
        "catalog_signals.payload.day": 1,
    }


@pytest.mark.parametrize("text", ["Code Name Reaper II", "goodshort-K10JEicNmxOWhQPwdg3zdw==", "A spy finds the secret code that opens the vault."])
def test_known_negatives(text):
    assert pan.scrub_text(text) == (text, 0)
    assert pan.scan_row("catalog_rows", {"title": text, "title_cn": text, "row_key": text, "description": text}) == {}


@pytest.mark.parametrize("resource", sorted(CONTRACT["v2_rows"]))
def test_scan_paths_match_realshort(resource):
    sample = CONTRACT["v2_rows"][resource]
    assert pan.scan_row(resource, sample["input"]) == sample["hits"]
    # RealShort's output is already scrubbed: the gate sees nothing.
    assert pan.scan_row(resource, sample["output"]) == {}


def test_v1_scan_uses_v1_exemptions():
    sample = CONTRACT["v1_row"]
    assert pan.scan_v1_row(sample["input"]) == sample["hits"]
    exempt = {"source_id", "listed_at", "detail_url", "signals[*].source_ref", "signals[*].observed_at", "posted.last_post_on"}
    assert exempt.isdisjoint(sample["hits"])


def test_manifest_meta_scan_matches_realshort():
    sample = CONTRACT["manifest_meta_scan"]
    assert pan.scan_manifest_meta(sample["input"]) == sample["hits"]
    # meta.scrub is counts, never scanned; its keys are paths.
    assert pan.scan_manifest_meta({"scrub": {PAN: 1}}) == {}


def test_scan_leaves_its_input_alone():
    row = {"title": PAN, "tag_list": [PAN, "ok"], "payload": {"h": [[PAN]]}}
    before = copy.deepcopy(row)
    first = pan.scan_row("rs_rows", row)
    assert row == before
    assert first == {"rs_rows.title": 1, "rs_rows.tag_list[*]": 1, "rs_rows.payload.h[*][*]": 1}
    assert pan.scan_row("rs_rows", row) is not first


def test_scan_counts_every_leaf():
    assert pan.scan_row("rs_rows", {"tag_list": [PAN, PAN, "ok"], "flag": True, "n": 3, "none": None}) == {"rs_rows.tag_list[*]": 2}


def test_misuse_is_refused():
    with pytest.raises(ValueError):
        pan.scan_row("manifest", {"title": PAN})
    with pytest.raises(ValueError):
        pan.scan_row("rows", {"title": PAN})
    with pytest.raises(TypeError):
        pan.scrub_text(b"pan.baidu.com")
