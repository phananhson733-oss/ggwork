"""TR-10: the shared wording, premise 1 (never call "not observed" zero) and premise 2 (agreement is admission, not proof).

test_wording_forbidden_terms reads every output template the radar has: the wording constants, the contract's labels,
the banner and exclusion texts, the evidence fixtures' labels and notes, every string literal of the observe package,
and the agent's prompt.
"""

import ast
import json
from pathlib import Path

import pytest

from ggwork_pick import middleware
from ggwork_pick.observe import contract, eligibility, leadtime, market_map, status_rules, wording
from ggwork_pick.observe.wording import (
    ADMISSION_AGREED,
    ADMISSION_FORBIDDEN_TERMS,
    FORBIDDEN_TERMS,
    FROM_ZERO_NOTE,
    GSC_LABEL_TEXT,
    LINK_ACTION_TEXT,
    LINK_TRENDS_LOW,
    admission_forbidden_in,
    forbidden_in,
)

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
ROOT = Path(__file__).resolve().parents[4]
OBSERVE = SOURCE / "ggwork_pick" / "observe"
FIXTURES = SOURCE / "tests" / "fixtures"


def _strings(value) -> list[str]:
    """Every string inside a constant: a str, or the keys and values of mappings and tuples, recursively."""
    if isinstance(value, str):
        return [value]
    if hasattr(value, "items"):
        return [text for key, item in value.items() for text in (*_strings(key), *_strings(item))]
    if isinstance(value, tuple | list | frozenset | set):
        return [text for item in value for text in _strings(item)]
    if hasattr(value, "__dataclass_fields__"):
        return [text for name in value.__dataclass_fields__ for text in _strings(getattr(value, name))]
    return []


def _module_texts(module) -> list[str]:
    return [text for name, value in vars(module).items() if not name.startswith("_") and name.isupper() for text in _strings(value)]


def _literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _fixture_texts() -> list[str]:
    texts = []
    for name in ("evidence.json", "result_obs.json"):
        fixture = json.loads((FIXTURES / "obs_contract" / name).read_text(encoding="utf-8"))
        texts += [text for case in fixture["valid"] for text in _evidence_texts(case["value"])]
    return texts + list(json.loads((FIXTURES / "obs_status_cases.json").read_text(encoding="utf-8"))["texts"].values())


def _evidence_texts(value) -> list[str]:
    if isinstance(value, dict):
        own = [value[key] for key in ("label", "note") if isinstance(value.get(key), str)]
        return own + [text for item in value.values() for text in _evidence_texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in _evidence_texts(item)]
    return []


def test_wording_forbidden_terms():
    """Premise 1: no template, evidence label or note, banner or prompt says 0 impressions, zero impressions, is zero, or
    no impressions. The forbidden list itself is the only place these words appear."""
    templates = {
        "wording": [t for t in _module_texts(wording) if t not in FORBIDDEN_TERMS],
        "contract": [contract.UNOBSERVED, contract.UNOBSERVED_TRENDS, contract.UNOBSERVED_GSC, *contract.LINK_LABEL_TEXT.values()]
        + list(contract.OBS_CONDITION_LABELS.values()),
        "status_rules": _module_texts(status_rules),
        "eligibility": _module_texts(eligibility),
        "leadtime": _module_texts(leadtime),
        "market_map": _module_texts(market_map),
        "fixtures": _fixture_texts(),
        "prompt": [middleware.PICK_INSTRUCTIONS, (ROOT / "skills/public/pick-drama/SKILL.md").read_text(encoding="utf-8")],
    }
    assert all(templates.values())  # each named source really contributed text
    for path in sorted(OBSERVE.rglob("*.py")):
        if path.name != "wording.py":
            templates[str(path.relative_to(OBSERVE))] = _literals(path)
    found = {where: hits for where, texts in templates.items() if (hits := [hit for text in texts for hit in forbidden_in(text)])}
    assert found == {}


@pytest.mark.parametrize("term", FORBIDDEN_TERMS)
def test_every_forbidden_term_is_caught(term):
    assert forbidden_in(f"前一窗口{term}") != ()


@pytest.mark.parametrize(
    "text",
    ["零次曝光", "曝光为 0", "点击数为0", "0 次点击", "没有任何曝光", "曝光：0 次"],
)
def test_forbidden_variants_are_caught(text):
    assert forbidden_in(text) != ()


@pytest.mark.parametrize(
    "text",
    [
        "W0 曝光 325，W−1 曝光 82",
        "W−1 ≥20 次曝光",
        "从零起量（基线未观测到）",
        "W0 曝光 ≥2000，或 W0 对 W−1 ≥+50%",
        "CTR 为 0.05",
        "非零小时 ≥12",
        contract.UNOBSERVED_GSC,
    ],
)
def test_forbidden_patterns_leave_legitimate_text_alone(text):
    """The list is about claiming zero: a window named W0, a threshold of 20, a ratio or a nonzero-hour count is not."""
    assert forbidden_in(text) == ()


def test_from_zero_semantics():
    """Premise 1: from_zero means the baseline was not observed, never that it was zero."""
    assert FROM_ZERO_NOTE == "基线未观测到"
    assert FROM_ZERO_NOTE in GSC_LABEL_TEXT["from_zero"] and contract.UNOBSERVED in FROM_ZERO_NOTE
    assert not forbidden_in(GSC_LABEL_TEXT["from_zero"])
    assert tuple(GSC_LABEL_TEXT) == ("surge", "from_zero", "high_ctr", "rank_push", "rising")


def test_link_unobserved_label():
    """Design 6.1's "not observed or low" column never says zero; it says not observed."""
    assert contract.UNOBSERVED in LINK_TRENDS_LOW and "零" not in LINK_TRENDS_LOW and "0" not in LINK_TRENDS_LOW
    assert tuple(LINK_ACTION_TEXT) == contract.LINK_STATES
    assert all("零" not in text for text in LINK_ACTION_TEXT.values())


def test_admission_wording():
    """Premise 2: two lower bounds agreeing is an admission rule; it proves neither completeness nor independence."""
    assert ADMISSION_AGREED == "两份下界一致（准入）"
    assert admission_forbidden_in(ADMISSION_AGREED) == ()
    for term in ADMISSION_FORBIDDEN_TERMS:
        assert admission_forbidden_in(f"两份下界一致，数据{term}") == (term,)


def test_wording_reuses_contract_constants():
    """The unobserved phrases live in the contract (TR-33); wording.py re-exports them, it does not define its own."""
    assert wording.UNOBSERVED is contract.UNOBSERVED
    assert wording.UNOBSERVED_TRENDS is contract.UNOBSERVED_TRENDS
    assert wording.UNOBSERVED_GSC is contract.UNOBSERVED_GSC
