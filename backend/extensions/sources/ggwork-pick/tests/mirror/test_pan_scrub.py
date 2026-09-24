"""The pan scrub port against RealShort 816ca2e: the shared fixture case by case, the exemptions, and scanning leaf by leaf.

pan_scrub_cases.json is a byte copy of RealShort's tests/fixtures/pan-scrub-cases.json (SOURCE.json); export_v2_contract.json
holds what RealShort's own scrubAllText counted for sample rows (export_v2_contract.gen.mjs), so paths are checked against it.
"""

import copy
import dataclasses
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from importlib import resources
from pathlib import Path

import pytest

from ggwork_pick.mirror import pan

PROJECT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT / "tests" / "fixtures"
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


# Local cases the shared fixture lacks. Each hits count is what RealShort 816ca2e's own scrubPanText returned for the same
# input (node --import tsx in a clean checkout, 2026-09-24), so they pin agreement, not a guess.
@pytest.mark.parametrize(
    ("text", "hits"),
    [
        # %7E is the top of normalize.printable (a closed range): it decodes to ~, which the next pass's markup step removes.
        ("pan%7E.baidu.com/s/1abc", 1),
        ("pan%7F.baidu.com/s/1abc", 0),
        # Only copy keep, which leaves tags alone, reads the code in the attribute once %3A is decoded.
        ('<b data-x="提取码%3A ab12">y</b>', 1),
    ],
)
def test_local_cases_agree_with_realshort(text, hits):
    assert pan.scrub_text(text) == ((pan.RULES.replacement, 1) if hits else (text, 0))


def _case(name):
    return next(case["input"] for case in CASES["cases"] if case["name"] == name)


# Per copy, an input no other copy (and neither raw pattern) recognises. Copy value has none: in the fixture and in
# about ten thousand generated tag, keyword and value combinations, whatever value recognises another copy does too.
ONLY_ONE_COPY = {
    "delete": _case("url_value_tag_inside_domain"),
    "keep": '<b data-x="提取码%3A ab12">y</b>',
    "keyless": _case("code_text_value_in_container_next_to_link"),
}


@pytest.mark.parametrize("name", sorted(ONLY_ONE_COPY))
def test_each_copy_is_needed(name):
    text = ONLY_ONE_COPY[name]
    assert pan.RULES.url.search(text) is None and pan.RULES.code.search(text) is None
    detected = {copy_name for copy_name, steps in zip(pan.COPIES, pan._copies(pan.RULES), strict=True) if pan._probe(text, steps)[0]}
    assert detected == {name}
    assert pan.scrub_text(text) == (pan.RULES.replacement, 1)


@pytest.mark.parametrize(
    ("text", "decoded"),
    [
        ("pan&#46baidu", "pan.baidu"),
        ("&#x10FFFF;", chr(0x10FFFF)),
        ("&#1114111;", chr(0x10FFFF)),
        ("&#00000065;", "A"),
        ("&#xD800;", "&#xD800;"),
        ("&#57343;", "&#57343;"),
        ("&#x110000;", "&#x110000;"),
        ("&#10000000;", "&#10000000;"),
        ("&#0;", "&#0;"),
        ("&amp;&AMP;&nosuch;", "&&AMP;&nosuch;"),
    ],
)
def test_entities_step(text, decoded):
    # Fixture about: leading zeros dropped, at most max_digits (dec 7, hex 6), a scalar value 1-0x10FFFF, names case-sensitive.
    assert (pan.RULES.entity_max_dec, pan.RULES.entity_max_hex) == (7, 6)
    assert dict(pan._copies(pan.RULES)[0])["entities"](text) == decoded


@pytest.mark.parametrize(
    ("copy_name", "text", "after"),
    [
        ("value", '<input value="ab12">', " ab12 "),
        ("value", "<b>x</b>", "x"),
        ("delete", '<input value="ab12">', ""),
        ("keyless", '<span title="复制提取码">x</span>', '<span title="复制' + chr(0xFFFD) * 3 + '">x</span>'),
    ],
)
def test_tags_step_per_copy(copy_name, text, after):
    steps = dict(pan._copies(pan.RULES)[pan.COPIES.index(copy_name)])
    assert steps["tags"](text) == after
    assert "tags" not in dict(pan._copies(pan.RULES)[pan.COPIES.index("keep")])


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


def test_wheel_ships_the_rules(tmp_path):
    # The image installs a wheel built from this project (a path dependency of backend/pyproject.toml), not the source
    # tree that importlib.resources reads above. Built offline, so it is skipped where uv or its cached backend is missing.
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("没有 uv，无法离线构建 wheel")
    command = [uv, "build", "--wheel", "--offline", "--no-config", "--quiet", "-o", str(tmp_path), str(PROJECT)]
    built = subprocess.run(command, capture_output=True, timeout=120, check=False)
    if built.returncode != 0:
        pytest.skip(f"离线构建 wheel 不可用（uv 退出码 {built.returncode}）")
    (wheel,) = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert hashlib.sha256(archive.read("ggwork_pick/mirror/pan_rules.json")).hexdigest() == FIXTURE_SHA256
        assert not [name for name in archive.namelist() if name.startswith("tests/")]


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


def test_v1_page_scan_prefixes_rows_and_counts_the_rules_apart():
    # The brief's --scan: v1 rows[*] by v1's exemptions, the first page's rules Markdown as v1.rules (U45).
    row = CONTRACT["v1_row"]
    page = {"rows": [row["input"], row["input"]], "rules": f"# 规则\n{PAN}", "scope": PAN}
    expected = {f"v1.rows[*].{path}": hits * 2 for path, hits in row["hits"].items()}
    assert pan.scan_v1_page(page) == {**expected, "v1.rules": 1}
    # Later pages carry no rules; a row that is not an object is still scanned as a leaf.
    assert pan.scan_v1_page({"rows": [PAN], "rules": None}) == {"v1.rows[*]": 1}
    assert pan.scan_v1_page({"rows": [], "rules": "# 规则"}) == {}


@pytest.mark.parametrize("resource", sorted(CONTRACT["v2_rows"]))
def test_v2_page_scan_adds_up_its_rows(resource):
    sample = CONTRACT["v2_rows"][resource]
    page = {"rows": [sample["input"], sample["output"], sample["input"]], "fingerprint": PAN}
    assert pan.scan_v2_page(resource, page) == {path: hits * 2 for path, hits in sample["hits"].items()}


def test_add_hits_returns_a_new_mapping():
    first = {"a": 1}
    assert pan.add_hits(first, {"a": 2, "b": 1}) == {"a": 3, "b": 1} and first == {"a": 1}


def test_scan_leaves_its_input_alone():
    row = {"title": PAN, "tag_list": [PAN, "ok"], "payload": {"h": [[PAN]]}}
    before = copy.deepcopy(row)
    first = pan.scan_row("rs_rows", row)
    assert row == before
    assert first == {"rs_rows.title": 1, "rs_rows.tag_list[*]": 1, "rs_rows.payload.h[*][*]": 1}
    assert pan.scan_row("rs_rows", row) is not first


def test_scan_counts_every_leaf():
    assert pan.scan_row("rs_rows", {"tag_list": [PAN, PAN, "ok"], "flag": True, "n": 3, "none": None}) == {"rs_rows.tag_list[*]": 2}


@pytest.mark.parametrize("key", ["https://pan.baidu.com/s/1AbCdEf", "提取码ab12", "a.b", "a b", "", "k" * 65])
def test_a_key_that_is_not_a_plain_name_is_not_written_into_the_path(key):
    # Paths are what --scan prints; a key can carry the very text the scan looks for. Keys meta.scrub could hold (SCRUB_PATH:
    # letters, digits, underscore) are kept as they are; any other is written <非常规键名>, like contracts' error paths.
    from ggwork_pick.mirror.contracts import ODD_KEY

    assert pan.scan_value({"rules": {key: PAN, "plain_1": PAN}}, "manifest.meta") == {
        f"manifest.meta.rules.{ODD_KEY}": 1,
        "manifest.meta.rules.plain_1": 1,
    }
    # Two such keys add up under the one masked path; exemptions still go by the key itself.
    assert pan.scan_row("rs_rows", {"md": {key: PAN, f"{key}/2": [PAN]}, "source_id": PAN}) == {f"rs_rows.md.{ODD_KEY}": 1, f"rs_rows.md.{ODD_KEY}[*]": 1}
    assert pan.scan_v1_row({"signals": [{key: PAN, "source_ref": PAN}]}) == {f"signals[*].{ODD_KEY}": 1}


def test_misuse_is_refused():
    with pytest.raises(ValueError):
        pan.scan_row("manifest", {"title": PAN})
    with pytest.raises(ValueError):
        pan.scan_row("rows", {"title": PAN})
    with pytest.raises(TypeError):
        pan.scrub_text(b"pan.baidu.com")
