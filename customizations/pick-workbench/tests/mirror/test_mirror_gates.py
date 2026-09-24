"""The gates before a mirror run publishes, the parts without a database (P2-4; plan 5.3, 1546-1554; implementation
note P2-4, U18, U45, U49): GateResult, the text scanners (G2, G5) and the pure comparisons (G3, G8, G7's kinds).

gate_world builds one consistent version, manifest and v1 pull where every gate passes; each case changes one thing.
The gates that read a built version are tested on PostgreSQL in test_mirror_gates_pg.py.
"""

import asyncio
import json
import threading
from dataclasses import FrozenInstanceError, replace

import gate_world as gw
import pytest
from gate_world import PAN, b64url, baseline, text_scan, v1_row, v1_scan, with_v1
from mirror_rows import TABLES, NoSql, synthetic_row

from ggwork_pick.mirror import gates
from ggwork_pick.mirror.contracts import ODD_KEY
from ggwork_pick.mirror.gate_result import id_part

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
    assert len(scan.rows) == len(gw.V1_CANDIDATES)
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
    assert scan.hits == 1  # details_json's scrub_hits.mirror (P2-5c)
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
    assert result.detail == {"unscanned": {"rs_ids": {"manifest": gw.COUNTS["rs_ids"], "scanned": 0}, "manifest.meta": {"manifest": 1, "scanned": 0}}}


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


# ---------------------------------------------------------------- G3, G7, G8: pure parts


def test_row_counts_gate_names_each_table_off():
    for table in TABLES:
        claimed = {**gw.COUNTS, table: gw.COUNTS[table] + 1}
        result = gates.row_counts_gate(gw.COUNTS, claimed)
        assert not result.ok, table
        assert result.detail == {"tables": {table: {"manifest": gw.COUNTS[table] + 1, "mirror": gw.COUNTS[table]}}, "total": 1}
    assert gates.row_counts_gate(gw.COUNTS, gw.COUNTS).ok
    # A count that is not a plain int is off, and never echoed.
    result = gates.row_counts_gate(gw.COUNTS, {**gw.COUNTS, "rs_ids": "3"})
    assert result.detail["tables"] == {"rs_ids": {"manifest": None, "mirror": gw.COUNTS["rs_ids"]}}


@pytest.mark.parametrize(
    "source_id, row_key",
    [
        (b64url("c-1"), "c-1"),
        (b64url("goodshort-K10JEicNmxOWhQPwdg3zdw=="), "goodshort-K10JEicNmxOWhQPwdg3zdw=="),
        (b64url("剧-1"), "剧-1"),
        ("YS1i", "a-b"),
        ("YS1i=", None),  # RealShort never pads
        ("YS+i", None),  # base64, not base64url
        ("YS1", None),  # does not round-trip
        ("_w", None),  # not UTF-8
        (None, None),
        (12, None),
    ],
)
def test_row_key_of_decodes_base64url_strictly(source_id, row_key):
    assert gates.row_key_of(source_id) == row_key


def test_signal_and_theater_kinds_come_from_the_manifest_rules():
    # request.ts:43, :110-121: basisLabels is a full Record<Basis, ...>; theaters are BASES less clk, bill and gsc.
    manifest = baseline().manifest
    bases = ("kd", "kw", "qc", "qr", "sm", "smd", "mg", "fh", "sh", "gh", "gn", "ghh", "dbn", "clk", "bill", "gsc")
    assert gates.signal_kinds(manifest) == bases
    assert gates.theater_kinds(manifest) == bases[:13]
    grown = gw.replaced(manifest, ("meta", "rules", "basisLabels", "xx"), "新剧场")
    assert gates.theater_kinds(grown) == (*bases[:13], "xx")  # a new theater needs no change here
    with pytest.raises(ValueError):
        gates.signal_kinds(gw.removed(manifest, ("meta", "rules", "basisLabels")))


def _candidates(world) -> tuple:
    """The baseline's candidates as the version gives them (checked against PostgreSQL in the PG tests)."""
    return tuple(gates.Candidate(key, frozenset(kinds)) for key, (kinds, _) in gw.V1_CANDIDATES.items())


PAIRS = (("c-1", "SD-1"), ("reelshort-d-1", "SD-2"), ("c-4", "SD-4"))


@pytest.mark.parametrize(
    "change, path, row_id",
    [
        (lambda rows: [*rows, v1_row("c-3", ("kd",), ())], "rows.only_v1", "c-3"),
        (lambda rows: rows[1:], "rows.only_mirror", "c-1"),
        (lambda rows: [v1_row("c-1", ("kd",), ("SD-1",)), *rows[1:]], "signals[*].kind", "c-1"),
        (lambda rows: [v1_row("c-1", ("kd", "kw"), ("SD-1", "SD-4")), *rows[1:]], "posted.records", "c-1"),
        (lambda rows: [*rows, {**v1_row("c-9"), "source_id": "not base64url!"}], "v1.source_id", "not base64url!"),
        (lambda rows: [*rows, v1_row("c-2", ("sm",), ())], "v1.duplicate", "c-2"),
    ],
    ids=["v1-extra-row", "v1-missing-row", "kind-diff", "sd-diff", "undecodable-source-id", "duplicate-row"],
)
def test_v1_consistency_differences_fail(change, path, row_id):
    world = with_v1(baseline(), change(list(baseline().v1_rows)))
    result = gates.v1_consistency_gate(_candidates(world), PAIRS, v1_scan(world))
    assert not result.ok
    assert result.consequence == "mirror"
    assert path in result.detail["paths"]
    assert row_id in result.detail["rows"]


def test_v1_consistency_baseline_passes_without_the_database():
    world = baseline()
    assert gates.v1_consistency_gate(_candidates(world), PAIRS, v1_scan(world)).as_json() == "pass"


def test_row_ids_never_carry_pan_text():
    # U37: details_json reaches every signed-in user. A row is named by its key (U49), but a key that holds pan text is
    # shown as the replacement, scrubbed before the cut so that no piece of it slips past ID_PART_MAX either.
    world = gw.with_row(baseline(), "catalog_posted", 0, sd=PAN, who=[PAN])
    mirror = gates.mirror_text_gate(text_scan(world), gw.COUNTS).as_json()
    assert mirror["rows"] == [["catalog_posted", gw.REPLACEMENT]]
    v1 = with_v1(baseline(), [*baseline().v1_rows, {**v1_row("c-9"), "source_id": PAN}])
    consistency = gates.v1_consistency_gate(_candidates(v1), PAIRS, v1_scan(v1)).as_json()
    assert consistency["paths"] == {"v1.source_id": 1}
    assert consistency["rows"] == [gw.REPLACEMENT]
    assert "pan.baidu" not in json.dumps([mirror, consistency], ensure_ascii=False)
    assert id_part("x" * 190 + PAN) == gw.REPLACEMENT
    assert id_part("c-1") == "c-1"


@pytest.mark.asyncio
async def test_schema_name_is_checked_before_any_statement():
    world = baseline()
    connection = NoSql()
    with pytest.raises(ValueError):
        await gates.run_mirror_gates(connection, schema_name="public", manifest=world.manifest, v1=v1_scan(world), text=text_scan(world))
    assert connection.touched == []


def test_as_json_of_a_run_outcome_is_plain():
    outcome = gates.MirrorGates(results=(gates.GateResult("row_counts", True),), accept_empty=gates.AcceptEmpty(False, None), measured={"rs_rows": 1})
    assert outcome.as_json() == {"row_counts": "pass"}
    with pytest.raises(FrozenInstanceError):
        outcome.results = ()
    assert replace(outcome, results=()).ok  # no result, nothing failed
    assert asyncio.iscoroutinefunction(gates.run_mirror_gates)
