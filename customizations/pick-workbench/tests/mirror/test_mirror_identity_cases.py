"""The identity -> row_key cases shared with the frontend card link (P4-1, critique B12).

identity_row_key_cases.json is read here and by frontend/tests/unit/core/pick/identity.test.ts, so the browser's decoder and
gates.row_key_of agree case by case: base64url alphabet first, then strict UTF-8, then the re-encoding round trip.
"""

import json
from pathlib import Path

import pytest

from ggwork_pick.contracts import DramaInput
from ggwork_pick.mirror import gates

CASES_FILE = Path(__file__).resolve().parents[1] / "fixtures" / "identity_row_key_cases.json"
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
IDS = [case["name"] for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_row_key_of_matches_the_shared_case(case):
    assert gates.row_key_of(case["source_id"]) == case["row_key"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_identity_is_the_one_the_backend_builds(case):
    # model_construct skips field validation: the empty and malformed source_ids never pass DramaInput, but the frontend still
    # has to read their identities the way the backend would have written them.
    drama = DramaInput.model_construct(source="realshort-pick", source_id=case["source_id"], language="en")
    assert drama.identity == case["identity"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_card_key_is_the_row_key_or_nothing(case):
    assert case["card_row_key"] in (case["row_key"], None)


def test_the_cases_cover_each_outcome():
    names = set(IDS)
    assert len(names) == len(CASES)
    outcomes = {(case["row_key"] is None, case["card_row_key"] is None) for case in CASES}
    # decodes and links; decodes but is no row key the page accepts; does not decode at all
    assert outcomes == {(False, False), (False, True), (True, True)}
    for name in ("non-zero-trailing-bits", "standard-plus", "padded", "utf8-surrogate", "leading-bom", "trailing-space"):
        assert name in names, name
