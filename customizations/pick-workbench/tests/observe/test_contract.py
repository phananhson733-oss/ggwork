"""The observation radar's shared data contract (plan TR-33): ggwork_pick/observe/contract*.py, the fixtures in
tests/fixtures/obs_contract/ and docs/pick-workbench/observe-contract.md say the same thing.

Every fixture file is read here. Files naming a model hold valid cases that must parse and invalid ones that must be
refused; the eligibility truth table and the link cases are checked against small reference evaluators written from the
fixtures' own "about" text, so a hand-written expectation that contradicts the stated rule turns this red before TR-10 or
TR-24 builds on it. The frontend (TR-16, TR-24) reads the same files.
"""

import ast
import json
import re
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from ggwork_pick.contracts import PickConditions
from ggwork_pick.observe import contract, contract_api, contract_rows, contract_views
from ggwork_pick.selection import unmappable_conditions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract"
DOC = Path(__file__).resolve().parents[4] / "docs/pick-workbench/observe-contract.md"
MODEL_FILES = sorted(path.name for path in FIXTURES.glob("*.json") if "model" in json.loads(path.read_text(encoding="utf-8")))
OTHER_FILES = {"views.json", "status_codes.json", "result_obs.json", "unmappable_cases.json", "obs_eligibility_cases.json", "obs_link_cases.json"}
CONTRACT_MODULES = (contract, contract_rows, contract_api, contract_views)
# Every model the document gives a field table, in the document's order.
MODELS = (
    contract.ObsConditionFields, contract.TrendsSetRef, contract.GscSetRef, contract.ObsCoverage, contract.ObsSummary, contract.Observations,
    contract.ObsAsOf, contract.ObsEvidence, contract.RulesRef, contract.SliceRef, contract.TotalsRef, contract.VcheckSummary,
    contract.FrozenInputsTrends, contract.FrozenInputsGsc, contract_rows.LabelHit, contract_rows.QualityNote, contract_rows.PasteRow,
    contract_rows.StateRow, contract_rows.SliceDay, contract_rows.VcheckRow, contract_rows.TotalsRow, contract_rows.LinkFact,
    contract_rows.LinkRow, contract_rows.AlertRow, contract_rows.UncoveredUnit, contract_rows.SetSummaryTrends, contract_rows.CoverageLayers,
    contract_rows.Unknowable, contract_rows.SetSummaryGsc, *contract_api.DECISION_MODELS, contract_api.ObsBanner, contract_api.ObsChannelStatus,
    contract_api.ObsSyncStatus, contract_api.ObsSyncError,
)  # fmt: skip
# The shapes the fixture files name in their "model" key (the frontend's schemas use the same names).
SHAPES = {
    "ObsConditionFields": contract.ObsConditionFields, "Observations": contract.Observations, "ObsAsOf": contract.ObsAsOf,
    "ObsEvidence": contract.ObsEvidence, "FrozenInputs": contract.FrozenInputs, "StateRow": contract_rows.StateRow,
    "VcheckRow": contract_rows.VcheckRow, "TotalsRow": contract_rows.TotalsRow, "LinkRow": contract_rows.LinkRow,
    "AlertRow": contract_rows.AlertRow, "SetSummary": contract_rows.SetSummary, "Decision": contract_api.Decision,
    "SyncObs": contract_api.SyncObs,
}  # fmt: skip


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _adapter(name: str) -> TypeAdapter:
    return TypeAdapter(SHAPES[name])


def test_every_fixture_file_is_checked():
    assert set(MODEL_FILES) | OTHER_FILES == {path.name for path in FIXTURES.glob("*.json")}
    assert not set(MODEL_FILES) & OTHER_FILES


@pytest.mark.parametrize("name", MODEL_FILES)
def test_contract_fixtures_valid(name):
    fixture = _load(name)
    adapter = _adapter(fixture["model"])
    assert fixture["valid"] and fixture["invalid"]
    for case in fixture["valid"]:
        adapter.validate_python(case["value"])
    for case in fixture["invalid"]:
        with pytest.raises(ValidationError):
            adapter.validate_python(case["value"])
            pytest.fail(f"{name}: {case['name']} was accepted ({case['why']})")


def test_obs_as_of_projects_to_observations():
    """D28: result_view's observations is obs_as_of_json's four reference keys followed by its inner object's keys."""
    for case in _load("obs_as_of.json")["valid"]:
        stored = case["value"]
        projected = {**{key: stored[key] for key in contract.OBS_AS_OF_REF_KEYS}, **stored["observations"]}
        assert projected == case["wire"], case["name"]
        assert list(contract.Observations.model_validate(projected).model_dump()) == list(projected), case["name"]


def test_observations_keys_are_ref_keys_then_summary_keys():
    assert list(contract.Observations.model_fields) == [*contract.OBS_AS_OF_REF_KEYS, *contract.ObsSummary.model_fields]
    assert list(contract.ObsAsOf.model_fields) == [*contract.OBS_AS_OF_REF_KEYS, "observations"]


# ---- views -----------------------------------------------------------------------------------------------------


def test_views_fixture_rows():
    fixture = _load("views.json")
    assert {case["view"] for case in fixture["valid"]} == set(contract_views.VIEW_NAMES)
    for case in fixture["valid"]:
        contract_views.VIEW_ROW_MODELS[case["view"]].model_validate(case["row"])
    for case in fixture["invalid"]:
        model = contract_views.VIEW_ROW_MODELS.get(case["view"])
        if model is None:
            continue
        with pytest.raises(ValidationError):
            model.model_validate(case["row"])
            pytest.fail(f"views.json: {case['why']} was accepted")


def test_views_follow_the_row_models():
    """The views that expose a stored row expose all of it, in the model's order; the eight names are fixed."""
    assert contract_views.VIEW_NAMES == ("sets", "states", "links", "discoveries", "alias_queue", "confirm_queue", "alerts", "run_status")
    for view, model in (("states", contract_rows.StateRow), ("links", contract_rows.LinkRow), ("alerts", contract_rows.AlertRow)):
        assert [column.name for column in contract_views.VIEW_COLUMNS[view]] == list(model.model_fields), view
    for view, columns in contract_views.VIEW_COLUMNS.items():
        assert "mode" in {column.name for column in columns} or view == "alias_queue", view
        assert {column.type for column in columns} <= set(contract_views.VIEW_PG_TYPES), view


def test_views_never_expose_runtime_or_reserved_names():
    # design 3.4: the cookie jar, the request log and the lease stay out of pick_obs. SQL keywords would need quoting.
    runtime = re.compile(r"cookie|lease|user_agent|ua_|egress|request_id|jar", re.IGNORECASE)
    reserved = {"window", "values", "check", "user", "order", "group", "limit", "offset", "table", "select", "from", "where", "default"}
    for view, columns in contract_views.VIEW_COLUMNS.items():
        for column in columns:
            assert not runtime.search(column.name), (view, column.name)
            assert column.name not in reserved, (view, column.name)
    for model in MODELS:
        assert not set(model.model_fields) & reserved, model.__name__


# ---- status codes, conditions, unmappable order -------------------------------------------------------------------


def test_status_codes_fixture():
    fixture = _load("status_codes.json")
    assert tuple(fixture["codes"]) == contract.STATUS_CODES
    assert tuple(fixture["banner_levels"]) == contract.BANNER_LEVELS
    assert not set(fixture["invalid_codes"]) & set(contract.STATUS_CODES)
    # TR-10's list, legacy_snapshot_missing included (plan TR-10, TR-23b), and TR-14's weekly parse_error.
    assert "legacy_snapshot_missing" in contract.STATUS_CODES and "parse_error" in contract.STATUS_CODES


def test_condition_fields_extend_pick_conditions():
    """TR-27 builds PickConditionsObs(PickConditions, ObsConditionFields): the seven names are new and sort gains obs."""
    assert tuple(contract.ObsConditionFields.model_fields) == contract.OBS_CONDITION_FIELDS
    assert not set(contract.OBS_CONDITION_FIELDS) & set(PickConditions.model_fields)
    existing = set(get_args(PickConditions.model_fields["sort"].annotation))
    assert set(contract.SORTS) - existing == {contract.OBS_SORT}
    combined = type("Combined", (PickConditions, contract.ObsConditionFields), {})
    parsed = combined.model_validate({"trend_state": "rising", "gsc_countries": [" USA "], "limit": 3})
    assert parsed.gsc_countries == ["USA"] and parsed.limit == 3
    for name in ("trend_state", "gsc_state", "link_state"):
        assert get_args(get_args(contract.ObsConditionFields.model_fields[name].annotation)[0])
    assert contract.TREND_STATES == ("rising", "emerging")
    assert contract.GSC_STATES == ("rising", "surge", "from_zero", "high_ctr", "rank_push", "present")
    assert contract.LINK_STATES == ("both_rising", "trends_lead_page", "trends_lead_distribution", "site_only", "cooling")


def test_unmappable_order_cases():
    fixture = _load("unmappable_cases.json")
    assert tuple(fixture["labels"]) == contract.OBS_CONDITION_FIELDS == contract.UNMAPPABLE_OBS_ORDER
    assert fixture["labels"] == dict(contract.OBS_CONDITION_LABELS)
    base_keys = set(PickConditions.model_fields)
    for case in fixture["cases"]:
        conditions = case["conditions"]
        base = PickConditions.model_validate({k: v for k, v in conditions.items() if k in base_keys})
        obs = contract.ObsConditionFields.model_validate({k: v for k, v in conditions.items() if k not in base_keys})
        present = [name for name in contract.UNMAPPABLE_OBS_ORDER if getattr(obs, name)]
        assert unmappable_conditions(base) + present == case["expected"], case["name"]
    assert fixture["base_order"] == unmappable_conditions(
        PickConditions(tags=["x"], posted_account="a", channel="youtube", query="q", exclude_selected=True, exclude_previous=True)
    )


# ---- a whole result ---------------------------------------------------------------------------------------------


def _check_result(result: dict) -> None:
    conditions = dict(result["conditions"])
    obs = {key: conditions.pop(key) for key in list(conditions) if key in contract.OBS_CONDITION_FIELDS}
    contract.ObsConditionFields.model_validate(obs)
    assert conditions.get("sort", "evidence_date") in contract.SORTS
    PickConditions.model_validate({**conditions, "sort": "evidence_date"})
    for item in result["items"]:
        for evidence in item["evidence"]:
            if evidence["kind"].startswith("obs_"):
                contract.ObsEvidence.model_validate(evidence)
    contract.Observations.model_validate(result["observations"])
    # D28: the observation moments never go into data_as_of.
    assert "observations" not in (result.get("data_as_of") or {})


def test_result_fixture():
    fixture = _load("result_obs.json")
    for case in fixture["valid"]:
        _check_result(case["value"])
        kinds = [e["kind"] for item in case["value"]["items"] for e in item["evidence"]]
        assert set(contract.EVIDENCE_KINDS) <= set(kinds)
    for case in fixture["invalid"]:
        with pytest.raises((ValidationError, AssertionError)):
            _check_result(case["value"])
            pytest.fail(f"result_obs.json: {case['name']} was accepted ({case['why']})")


# ---- eligibility truth table ------------------------------------------------------------------------------------


def _base_rows() -> dict:
    rows = {case["name"]: case["value"] for case in _load("state_rows.json")["valid"]}
    return {"trends": rows["trends_confirmed"], "gsc": rows["gsc_surge"]}


def _eligibility(row: dict, conditions: dict) -> list[str]:
    """Reference for the truth table, from obs_eligibility_cases.json's about text."""
    unconfirmed = row["correspondence"] != "confirmed"
    strong = row["id_evidence"] == "strong"
    checks = {
        "trend_first_only": row["confirmation"] == "first" and not conditions.get("trend_include_first"),
        "emerging_not_requested": row["state"] == "emerging" and conditions.get("trend_state") != "emerging",
        "title_ambiguous": row["ambiguity"] != "clear",
        "shared_title": "shared_title" in row["flags"],
        "correspondence_unconfirmed": unconfirmed and not strong,
        "correspondence_presumed": unconfirmed and strong and not conditions.get("trend_include_presumed"),
        "stale": row["stale"],
        "carried_over": row["carried_over"],
        "b_tier": row["tier"] == "B",
        "unstable": "unstable" in row["flags"],
        "control_unavailable": "control_unavailable" in row["flags"],
    }
    if row["channel"] == "gsc":
        checks = {
            "gsc_descriptive_only": row["admission"] != "formal",
            "migration_suspect": "migration_suspect" in row["flags"],
            "mapping_changed": "mapping_changed" in row["flags"],
        }
    return [reason for reason in contract.EXCLUSION_REASONS if checks.get(reason)]


def test_eligibility_cases_fixture():
    fixture = _load("obs_eligibility_cases.json")
    assert tuple(fixture["reasons_order"]) == contract.EXCLUSION_REASONS
    bases = _base_rows()
    seen = set()
    for case in fixture["cases"]:
        row = contract_rows.StateRow.model_validate({**bases[case["row"]["channel"]], **case["row"]}).model_dump()
        assert row["channel"] == "gsc" or row["state"] in contract.TREND_STATES, case["name"]
        contract.ObsConditionFields.model_validate(case["conditions"])
        expected = case["expected"]
        assert expected["eligible"] is (expected["reasons"] == []), case["name"]
        assert expected["reasons"] == _eligibility(row, case["conditions"]), case["name"]
        seen.update(expected["reasons"])
    assert seen == set(contract.EXCLUSION_REASONS) - {"set_batch_mismatch"}


# ---- link cases -------------------------------------------------------------------------------------------------


def _minutes(a: str, b: str) -> int:
    return int(abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) // 60)


def _fact(country, geo, label, t, ref, pair, params) -> dict:
    trends_anchor = t["latest_block_end"] if t else pair["trends"]["latest_block_end"]
    gsc_anchor = ref["window_end"] if ref else pair["gsc"]["cutoff"]
    gap = _minutes(gsc_anchor, trends_anchor)
    return {
        "country": country, "trends_geo": geo, "label": label, "trends_row_id": t["row_id"] if t else None,
        "gsc_row_id": ref["row_id"] if ref else None, "trends_anchor": trends_anchor, "gsc_anchor": gsc_anchor, "pair_gap_minutes": gap,
        "timely": gap <= params["pair_max_gap_hours"] * 60, "published_gap_minutes": _minutes(pair["gsc"]["published_at"], pair["trends"]["published_at"]),
        "stale": bool(t and (t["carried_over"] or t["stale"])),
    }  # fmt: skip


def _sides(t, rows, params):
    """(t_up, t_low, kept GSC rows 24h first, the gsc_up ones, gsc_low), or None when t voids the country."""
    excluded = set(params["excluded_flags"])
    if t is not None and set(t["flags"]) & excluded:
        return None
    kept = sorted((r for r in rows if not set(r["flags"]) & excluded), key=lambda r: r["window_kind"] != "24h")
    ups = [r for r in kept if r["admission"] == "formal" and any(h["formal"] and h["label"] in params["up_labels"][r["window_kind"]] for h in r["labels"])]
    t_up = t is not None and t["state"] == "rising" and t["confirmation"] == "confirmed"
    t_low = t is None or t["state"] in params["low_states"]
    g_low = len(kept) == len(rows) and not any(r["labels"] for r in kept)
    return t_up, t_low, kept, ups, g_low


def _country_label(t, sides, page) -> str | None:
    t_up, t_low, _, ups, g_low = sides
    rules = (
        ("cooling", t is not None and t["state"] == "cooling"),
        ("both_rising", t_up and bool(ups)),
        ("trends_lead_page", t_up and g_low and page),
        ("trends_lead_distribution", t_up and not page),
        ("site_only", t_low and bool(ups)),
    )
    return next((label for label, hit in rules if hit), None)


def _link_facts(given: dict, params: dict, pair: dict) -> list[dict]:
    """Reference for link_facts, from obs_link_cases.json's about text."""
    geo_of = {country: geo for geo, country in params["geo_country"].items() if country != "ALL"}
    trends = {row["scope"]: row for row in given["trends"]}
    degraded = pair["gsc"]["degraded_no_country_24h"]
    countries = sorted(({params["geo_country"].get(g) for g in trends} | {r["scope"] for r in given["gsc"]}) & set(geo_of))
    facts, ups_in, trends_up_in = [], set(), set()
    for country in countries:
        t = trends.get(geo_of[country])
        rows = [r for r in given["gsc"] if r["scope"] == country and not (degraded and r["window_kind"] == "24h")]
        sides = _sides(t, rows, params)
        if sides is None:
            continue
        trends_up_in.update([country] if sides[0] else [])
        ups_in.update([country] if sides[3] else [])
        label = _country_label(t, sides, given["has_site_page"])
        if label:
            ref = (sides[3] or sides[2] or [None])[0]
            facts.append(_fact(country, geo_of[country], label, t, ref, pair, params))
    t_ww = trends.get("WW")
    sides = _sides(t_ww, [r for r in given["gsc"] if r["scope"] == "ALL"], params)
    if sides and sides[0] and sides[3]:
        facts.append(_fact("ALL", "WW", "global_parallel", t_ww, sides[3][0], pair, params))
    both = any(f["label"] == "both_rising" for f in facts)
    if not both and any(a != b for a in trends_up_in for b in ups_in):
        facts.append(_fact(None, None, "different_markets", None, None, pair, params))
    return sorted(facts, key=lambda f: (f["country"] is None, f["country"] or ""))


def _actionable(case: dict, params: dict) -> list[str]:
    fact, now = case["fact"], datetime.fromisoformat(case["now"])
    trends_age = (now - datetime.fromisoformat(case["trends_published_at"])).total_seconds()
    gsc_age = (now - datetime.fromisoformat(case["gsc_published_at"])).total_seconds()
    checks = {
        "label_not_actionable": fact["label"] not in contract.LINK_STATES,
        "untimely_pair": not fact["timely"],
        "stale_row": fact["stale"],
        "trends_set_too_old": trends_age > params["trends_max_age_hours"] * 3600,
        "gsc_set_too_old": gsc_age > params["gsc_max_age_hours"] * 3600,
    }
    return [reason for reason in contract.LINK_ACTIONABILITY_REASONS if checks[reason]]


def test_link_cases_fixture():
    fixture = _load("obs_link_cases.json")
    params, bases = fixture["params"], _base_rows()
    assert tuple(params["excluded_flags"]) == contract.LINK_EXCLUDED_FLAGS
    for case in fixture["facts_cases"]:
        given = case["input"]
        for row in given["trends"] + given["gsc"]:
            contract_rows.StateRow.model_validate({**bases["trends" if len(row["scope"]) == 2 else "gsc"], **row})
        facts = [contract_rows.LinkFact.model_validate(fact).model_dump() for fact in case["facts"]]
        assert facts == _link_facts(given, params, given.get("pair", fixture["default_pair"])), case["name"]
    labels = {fact["label"] for case in fixture["facts_cases"] for fact in case["facts"]}
    assert labels == set(contract.LINK_LABELS)
    for case in fixture["actionable_cases"]:
        reasons = case["expected"]["reasons"]
        assert case["expected"]["actionable"] is (reasons == []), case["name"]
        assert reasons == _actionable(case, params), case["name"]


# ---- the module and the document --------------------------------------------------------------------------------


def test_contract_module_is_data_only():
    """Shapes and constants only (plan section 5): nothing that reaches a database, the network or the host runtime."""
    allowed = {"dataclasses", "json", "re", "types", "typing", "pydantic", "pydantic_core", "ggwork_pick.contracts", contract.__name__}
    for module in CONTRACT_MODULES:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert imported <= allowed, module.__name__
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)], module.__name__
    assert {Path(module.__file__).name for module in CONTRACT_MODULES} == {path.name for path in Path(contract.__file__).parent.glob("contract*.py")}


def test_models_list_every_public_model():
    """A model added to a contract module without a row in MODELS would have no field table in the document."""
    defined = {
        value
        for module in CONTRACT_MODULES
        for name, value in vars(module).items()
        if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == module.__name__ and not name.startswith("_")
    }
    assert defined - {contract.Frozen} == set(MODELS)
    assert len(MODELS) == len(set(MODELS))


def _doc_sections() -> dict[str, list[str]]:
    """Every '#### `Name`' heading of the document with the lines under it, up to the next heading."""
    sections, current = {}, None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^#{2,4} .*?`([^`]+)`\s*$", line)
        if line.startswith("#"):
            current = heading.group(1) if heading and line.startswith("####") else None
            if current is not None:
                sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _first_cells(lines: list[str]) -> list[str]:
    return [m.group(1) for line in lines if (m := re.match(r"^\|\s*`([^`]+)`\s*\|", line))]


def test_contract_doc_lists_every_field():
    sections = _doc_sections()
    for model in MODELS:
        assert _first_cells(sections.get(model.__name__, [])) == list(model.model_fields), model.__name__
    for view, columns in contract_views.VIEW_COLUMNS.items():
        rows = [re.split(r"\s*\|\s*", line.strip("| ")) for line in sections.get(f"pick_obs.{view}", []) if line.startswith("| `")]
        documented = [(name.strip("`"), kind.strip("`"), nullable == "是") for name, kind, nullable, *_ in rows]
        assert documented == [(c.name, c.type, c.nullable) for c in columns], view
    for name, values in contract.ENUMS.items():
        listed = re.findall(r"`([^`]+)`", " ".join(line for line in sections.get(name, []) if line.startswith("取值")))
        assert tuple(listed) == values, name
    listed_files = set(re.findall(r"`([a-z_]+\.json)`", "\n".join(sections.get("obs_contract/", []))))
    assert listed_files == {path.name for path in FIXTURES.glob("*.json")}


def test_enums_are_the_literals():
    """Each enum tuple is its Literal's arguments, so a value added to one is added to the other."""
    for name, values in contract.ENUMS.items():
        assert len(values) == len(set(values)) and values, name
    assert contract.ENUMS["EXCLUSION_REASONS"] == get_args(contract.ExclusionReason)
    assert contract.ENUMS["STATUS_CODES"] == get_args(contract.StatusCode)
    assert set(contract.LINK_STATES) < set(contract.LINK_LABELS)
    assert set(contract.LINK_EXCLUDED_FLAGS) <= set(contract.TRENDS_FLAGS) | set(contract.GSC_FLAGS)
    assert contract.DECISION_KINDS == tuple(get_args(model.model_fields["kind"].annotation)[0] for model in contract_api.DECISION_MODELS)
