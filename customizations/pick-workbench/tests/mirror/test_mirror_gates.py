"""The gates before a mirror run publishes, the parts without a database (P2-4; plan 5.3, 1546-1554; implementation
note P2-4, U18, U45, U49): GateResult and the text scanners (G2, G5).

gate_world builds one consistent version, manifest and v1 pull where every gate passes; each case changes one thing.
"""

import json
import threading
from dataclasses import FrozenInstanceError

import gate_world as gw
import pytest
from gate_world import PAN, b64url, baseline, text_scan, v1_row, v1_scan, with_v1
from mirror_rows import TABLES, synthetic_row

from ggwork_pick.mirror import gates
from ggwork_pick.mirror.contracts import ODD_KEY

NUL = "\x00"
LONE_SURROGATE = chr(0xD83D)


def _signal_note(world, index: int, note: str):
    """The index-th v1 row with its first signal's note changed."""
    row = world.v1_rows[index]
    signals = [{**row["signals"][0], "note": note}, *row["signals"][1:]]
    return gw.with_v1_row(world, index, signals=signals)


# ---------------------------------------------------------------- GateResult


def test_gate_names_follow_the_plan_and_say_who_publishes():
    assert gates.MIRROR_GATES == ("row_counts", "forbidden_columns", "mirror_text", "references", "control_totals", "v1_consistency", "empty_tables")
    # v1: neither side publishes; mirror: the version fails, the agent batches go out alone (plan 5.3, 2.5).
    assert dict(gates.CONSEQUENCES) == {"v1_text": "v1", **{name: "mirror" for name in gates.MIRROR_GATES}}


def test_gate_result_is_immutable_and_serializes_to_plain_json():
    result = gates.GateResult("row_counts", False, {"tables": {"rs_rows": {"manifest": 3, "mirror": 2}}, "total": 1})
    with pytest.raises(FrozenInstanceError):
        result.ok = True
    with pytest.raises(TypeError):
        result.detail["total"] = 2
    with pytest.raises(TypeError):
        result.detail["tables"]["rs_rows"]["mirror"] = 3
    assert result.consequence == "mirror"
    assert result.as_json() == {"ok": False, "consequence": "mirror", "tables": {"rs_rows": {"manifest": 3, "mirror": 2}}, "total": 1}
    assert gates.GateResult("v1_text", True).as_json() == "pass"
    with pytest.raises(ValueError):
        gates.GateResult("drift", True)


def test_findings_keep_twenty_row_ids_and_the_total():
    found = gates.Findings()
    for n in range(30):
        found = found.add({"catalog_signals.note": 1}, ("catalog_signals", f"c-{n}", "kd", 0))
    assert found.total == 30
    assert len(found.rows) == gates.ROW_ID_CAP == 20
    assert found.paths == {"catalog_signals.note": 30}
    assert found.add({}, "ignored") is found


# ---------------------------------------------------------------- G2: v1 text


def test_v1_baseline_passes():
    world = baseline()
    scan = v1_scan(world)
    assert scan.pages == 2
    assert len(scan.rows) == 4
    result = gates.v1_text_gate(scan)
    assert result.ok
    assert result.as_json() == "pass"


@pytest.mark.parametrize(
    "change, category, path",
    [
        (lambda w: _signal_note(w, 0, PAN), "pan", "v1.rows[*].signals[*].note"),
        (lambda w: gw.with_v1_row(w, 0, title="剧名" + NUL), "nul", "v1.rows[*].title"),
        # NUL is looked for everywhere, exempt fields too: PostgreSQL cannot store it in any of them.
        (lambda w: gw.with_v1_row(w, 0, detail_url=gw.REF + NUL), "nul", "v1.rows[*].detail_url"),
        (lambda w: gw.with_v1_row(w, 0, channel_rules={"youtube" + NUL: "unknown"}), "nul", f"v1.rows[*].channel_rules.{ODD_KEY}"),
    ],
    ids=["pan-in-signal-note", "nul-in-title", "nul-in-exempt-field", "nul-in-a-key"],
)
def test_v1_pan_or_nul_blocks_both(change, category, path):
    world = change(baseline())
    result = gates.v1_text_gate(v1_scan(world))
    assert not result.ok
    assert result.consequence == "v1"
    assert result.detail[category]["paths"] == {path: 1}
    assert list(result.detail[category]["rows"]) == [b64url("c-1")]
    assert result.detail[category]["total"] == 1


def test_v1_lone_surrogate_is_counted_but_does_not_fail():
    # U18: the import replaces it with ? and goes on, as sync._encode_rows does today.
    world = gw.with_v1_row(baseline(), 1, title="剧名" + LONE_SURROGATE)
    result = gates.v1_text_gate(v1_scan(world))
    assert result.ok
    assert result.detail["surrogates"]["paths"] == {"v1.rows[*].title": 1}
    assert list(result.detail["surrogates"]["rows"]) == [b64url("c-2")]
    assert "pan" not in result.detail and "nul" not in result.detail


def test_v1_rules_scanned_separately():
    # U45: the rules Markdown is not scrubbed by RealShort (feed.ts:63); counted on its own, a hit stops both sides.
    scan = v1_scan(baseline(), rules=gw.RULES + PAN)
    assert scan.row_hits == 0
    assert scan.rules_hits == 1
    result = gates.v1_text_gate(scan)
    assert not result.ok
    assert result.consequence == "v1"
    assert result.detail == {"rules": {"v1.rules": 1}}


def test_v1_exempt_fields_are_not_scanned_but_the_same_names_nested_are():
    # Top-level keys exempt by name, nested ones only by path (export-v2-map.ts:213-230).
    row = v1_row("c-1", ("kd", "kw"), ("SD-1",), source_id=PAN, detail_url=PAN, listed_at=PAN)
    row = {
        **row,
        "signals": [{**signal, "source_ref": PAN, "observed_at": PAN} for signal in row["signals"]],
        "posted": {**row["posted"], "last_post_on": PAN},
    }
    assert gates.v1_text_gate(v1_scan(with_v1(baseline(), [row]))).ok
    nested = {**row, "posted": {**row["posted"], "source_id": PAN}}
    result = gates.v1_text_gate(v1_scan(with_v1(baseline(), [nested])))
    assert result.detail["pan"]["paths"] == {"v1.rows[*].posted.source_id": 1}


def test_v1_scan_reads_the_raw_rows_not_what_drama_input_keeps():
    # G2 runs on the page JSON before DramaInput (which strips whitespace and refuses unknown keys): anything there counts.
    world = gw.with_v1_row(baseline(), 2, extra=[" " + PAN + " "])
    result = gates.v1_text_gate(v1_scan(world))
    assert result.detail["pan"]["paths"] == {"v1.rows[*].extra[*]": 1}
    assert list(result.detail["pan"]["rows"]) == [b64url("reelshort-d-1")]


def test_v1_page_fields_beside_rows_and_rules_are_not_scanned():
    pages = baseline().v1_pages()
    first = {**pages[0], "scope": PAN, "freshness": {"note": PAN}}
    scan = gates.scan_v1_page(gates.V1Scan(), first)
    assert scan.row_hits == 0 and scan.rules_hits == 0


def test_v1_gate_without_any_page_fails():
    result = gates.v1_text_gate(gates.V1Scan())
    assert result.as_json() == {"ok": False, "consequence": "v1", "unscanned": ["v1.rows"]}


def test_v1_rows_carry_what_g8_compares():
    scan = v1_scan(baseline())
    rows = {row.source_id: row for row in scan.rows}
    assert rows[b64url("c-1")].kinds == frozenset({"kd", "kw"})
    assert rows[b64url("reelshort-d-1")].records == frozenset({"SD-2"})


# ---------------------------------------------------------------- G5: mirror text


def test_mirror_text_baseline_passes():
    assert gates.mirror_text_gate(text_scan(baseline()), gw.COUNTS).as_json() == "pass"


def _payload_with(text: str) -> dict:
    return {"d": "2026-09-01", "h": [["2026-09-01", 3, text], ["2026-09-02", 4, ""]]}


@pytest.mark.parametrize(
    "table, index, changes, path, row_id",
    [
        ("catalog_signals", 0, {"note": PAN}, "catalog_signals.note", ["catalog_signals", "c-1", "kd", 0]),
        ("catalog_signals", 3, {"payload": _payload_with(PAN)}, "catalog_signals.payload.h[*][*]", ["catalog_signals", "c-2", "sm", 0]),
        ("catalog_posted", 0, {"posts": [{"d": "2026-09-01", "url": PAN}]}, "catalog_posted.posts[*].url", ["catalog_posted", "SD-1"]),
        ("catalog_posted", 1, {"who": ["甲", PAN]}, "catalog_posted.who[*]", ["catalog_posted", "SD-2"]),
    ],
    ids=["signal-note", "payload-h", "posts-url", "who"],
)
def test_pan_text_positions(table, index, changes, path, row_id):
    world = gw.with_row(baseline(), table, index, **changes)
    result = gates.mirror_text_gate(text_scan(world), gw.COUNTS)
    assert not result.ok
    assert result.consequence == "mirror"
    assert result.as_json() == {"ok": False, "consequence": "mirror", "paths": {path: 1}, "rows": [row_id], "total": 1}
    assert "pan.baidu" not in json.dumps(result.as_json(), ensure_ascii=False)


def test_manifest_meta_is_scanned_except_scrub():
    meta = {**baseline().manifest["meta"], "rules": {"ruleHints": {"hint": PAN}}, "scrub": {"catalog_rows.title": 2}}
    scan = gates.scan_mirror_meta(gates.MirrorTextScan(), meta)
    assert scan.found.paths == {"manifest.meta.rules.ruleHints.hint": 1}
    assert scan.found.rows == (("manifest",),)
    assert gates.scan_mirror_meta(gates.MirrorTextScan(), {"scrub": {"x": PAN}}).found.total == 0


def test_identity_columns_not_scanned():
    # plan 394: ids and derived keys keep their text; scrubbing them would break keys and links.
    world = gw.with_row(baseline(), "catalog_rows", 0, row_key="pan.baidu.com/s/1AbCdEf", in_site_ids=[PAN], title_key=PAN)
    world = gw.with_row(world, "catalog_posted", 0, sd=PAN, row_keys=[PAN], feishu_record=PAN)
    world = gw.with_row(world, "rs_rows", 0, slug=PAN, drama_id="d-1")
    assert gates.mirror_text_gate(text_scan(world), gw.COUNTS).ok


def test_real_title_negatives_pass():
    # P2-2b case 12 (plan 1554): real titles, keys and synopses that look like pan text to a loose pattern.
    key = "goodshort-K10JEicNmxOWhQPwdg3zdw=="
    synopsis = "When Ava cracks the secret code that guards her family's vault, the billionaire who framed her father comes knocking."
    world = gw.with_row(baseline(), "catalog_rows", 0, row_key=key, title="Code Name Reaper II", title_cn="Code Name Reaper II")
    world = gw.with_row(world, "rs_rows", 0, description=synopsis, title="Code Name Reaper II")
    world = with_v1(world, [v1_row(key, ("kd",), (), title="Code Name Reaper II", detail_url=gw.REF + key)])
    assert gates.mirror_text_gate(text_scan(world), gw.COUNTS).ok
    assert gates.v1_text_gate(v1_scan(world)).ok


def test_gate_detail_row_ids_capped():
    # U49: at most twenty row identifiers, and the total beside them.
    signals = [synthetic_row("catalog_signals", n, row_key=f"c-{n:02d}", kind="kd", ord=0, note=PAN) for n in range(30)]
    scan = gates.scan_mirror_page(gates.MirrorTextScan(), "catalog_signals", signals)
    result = gates.mirror_text_gate(scan, {**gw.COUNTS, "catalog_signals": 30})
    assert result.detail["total"] == 30
    assert result.detail["paths"] == {"catalog_signals.note": 30}
    assert [row[1] for row in result.detail["rows"]] == [f"c-{n:02d}" for n in range(20)]
    v1 = with_v1(baseline(), [v1_row(f"c-{n:02d}", ("kd",), (), title=PAN) for n in range(30)])
    found = gates.v1_text_gate(v1_scan(v1, per_page=7)).detail["pan"]
    assert (found["total"], len(found["rows"]), found["paths"]) == (30, 20, {"v1.rows[*].title": 30})


def test_mirror_text_needs_every_row_and_the_meta_scanned():
    # A scan that missed a resource (or the manifest's meta) would pass with nothing found; it must not.
    world = baseline()
    scan = gates.MirrorTextScan()
    for table in TABLES:
        if table != "rs_ids":
            scan = gates.scan_mirror_page(scan, table, list(world.tables[table]))
    result = gates.mirror_text_gate(scan, gw.COUNTS)
    assert not result.ok
    assert result.detail == {"unscanned": {"rs_ids": {"manifest": 3, "scanned": 0}, "manifest.meta": {"manifest": 1, "scanned": 0}}}


def test_scans_return_new_objects():
    world = baseline()
    empty_v1 = gates.V1Scan()
    first = gates.scan_v1_page(empty_v1, world.v1_pages()[0])
    assert (empty_v1.pages, empty_v1.rows) == (0, ())
    assert first.pages == 1 and first is not empty_v1
    empty_text = gates.MirrorTextScan()
    scanned = gates.scan_mirror_page(empty_text, "catalog_rows", list(world.tables["catalog_rows"]))
    assert dict(empty_text.scanned) == {} and dict(scanned.scanned) == {"catalog_rows": 4}
    with pytest.raises(FrozenInstanceError):
        scanned.meta_scanned = True
    with pytest.raises(TypeError):
        scanned.scanned["rs_rows"] = 1


def test_scan_mirror_page_refuses_an_unknown_resource():
    with pytest.raises(ValueError):
        gates.scan_mirror_page(gates.MirrorTextScan(), "meta", [])


@pytest.mark.asyncio
async def test_scanned_pages_run_in_a_worker_thread(monkeypatch):
    loop_thread = threading.get_ident()
    seen = []

    def probe(real):
        def run(*args):
            seen.append(threading.get_ident())
            return real(*args)

        return run

    monkeypatch.setattr(gates, "scan_mirror_page", probe(gates.scan_mirror_page))
    monkeypatch.setattr(gates, "scan_v1_page", probe(gates.scan_v1_page))
    world = baseline()
    text = await gates.scanned_mirror_page(gates.MirrorTextScan(), "catalog_rows", list(world.tables["catalog_rows"]))
    v1 = await gates.scanned_v1_page(gates.V1Scan(), world.v1_pages()[0])
    assert text.scanned["catalog_rows"] == 4 and v1.pages == 1
    assert len(seen) == 2 and loop_thread not in seen
