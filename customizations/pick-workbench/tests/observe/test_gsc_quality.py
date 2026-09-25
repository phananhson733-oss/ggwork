"""TR-09: the quality note beside the 7-day rising evaluation (plan D38; design 5.8).

An overdispersed rate test and a Benjamini-Hochberg adjustment over every tested 7-day evaluation of one GSC set, both
only a note, never a gate. The expected numbers below were computed by hand (exact fractions for the dispersion, then
the D38 formulas) outside the module under test.
"""

import math
from dataclasses import replace

import pytest

from ggwork_pick.observe.contract_rows import QualityNote
from ggwork_pick.observe.gsc import quality
from ggwork_pick.observe.gsc.params import GSC_RULES_V1 as V1

CASES = {
    "a": ((30, 42, 35, 50, 44, 38, 61), (20, 25, 18, 30, 22, 27, 24)),
    "b": ((12, 15, 9, 14, 11, 13, 16), (10, 12, 11, 9, 13, 10, 12)),  # underdispersed: phi floors at 1
    "c": ((200, 20, 210, 15, 190, 25, 205), (100, 110, 95, 105, 98, 102, 101)),  # overdispersed
}
HAND = {  # rate ratio, dispersion, z, one-sided p
    "a": (1.8072289156626506, 1.5869812583668006, 4.85631529453596, 5.979511421587087e-07),
    "b": (1.1688311688311688, 1.0, 1.0049508165973544, 0.15746026666233487),
    "c": (1.2165963431786218, 38.27619109561014, 0.6260123689422039, 0.2656534142135236),
}
HAND_BH = {"a": 1.7938534264761262e-06, "b": 0.2361903999935023, "c": 0.2656534142135236}


def _tests(**extra):
    return {**{key: quality.rate_test(w0, w1, params=V1) for key, (w0, w1) in CASES.items()}, **extra}


def test_quality_note_values():
    for key, (w0, w1) in CASES.items():
        got = quality.rate_test(w0, w1, params=V1)
        assert got.tested
        for actual, expected in zip((got.rate_ratio, got.dispersion, got.z, got.p_value), HAND[key]):
            assert actual == pytest.approx(expected, abs=1e-9, rel=0), key
    notes = quality.quality_notes(_tests(), params=V1)
    for key, note in notes.items():
        assert isinstance(note, QualityNote) and note.tested
        assert note.bh_adjusted == pytest.approx(HAND_BH[key], abs=1e-9, rel=0) and note.bh_q == 0.1
        assert note.bh_passed is (HAND_BH[key] <= 0.1)
        assert note.note == quality.NOTE_TESTED
    assert [notes[k].bh_passed for k in "abc"] == [True, False, False]


def test_bh_family_per_set():
    """The family is every tested 7-day evaluation of one set: untested ones do not count, another set's never mix in."""
    untested = quality.rate_test((5,) * 7, (None,) * 7, params=V1)
    with_untested = quality.quality_notes(_tests(d=untested), params=V1)
    assert {k: with_untested[k].bh_adjusted for k in "abc"} == {k: quality.quality_notes(_tests(), params=V1)[k].bh_adjusted for k in "abc"}
    assert with_untested["d"].bh_adjusted is None and with_untested["d"].bh_passed is None

    set_one = quality.quality_notes({"a": quality.rate_test(*CASES["a"], params=V1), "b": quality.rate_test(*CASES["b"], params=V1)}, params=V1)
    set_two = quality.quality_notes({"c": quality.rate_test(*CASES["c"], params=V1)}, params=V1)
    # Alone in its set, c is adjusted by a family of one; a and b by a family of two.
    assert set_two["c"].bh_adjusted == pytest.approx(HAND["c"][3], abs=1e-12)
    assert set_one["b"].bh_adjusted == pytest.approx(min(1.0, HAND["b"][3] * 2 / 2), abs=1e-12)
    assert set_one["a"].bh_adjusted == pytest.approx(min(HAND["a"][3] * 2 / 1, HAND["b"][3]), abs=1e-12)
    assert set_one["b"].bh_adjusted != with_untested["b"].bh_adjusted


def test_bh_adjust_is_monotone_and_capped():
    assert quality.bh_adjust(()) == ()
    assert quality.bh_adjust((0.5,)) == (0.5,)
    got = quality.bh_adjust((0.04, 0.01, 0.03, 0.9))
    assert got == pytest.approx((0.04 * 4 / 3, 0.01 * 4, 0.04 * 4 / 3, 0.9))
    assert quality.bh_adjust((0.6, 0.7, 0.8)) == pytest.approx((0.8, 0.8, 0.8))
    assert max(quality.bh_adjust((0.9, 0.95))) <= 1.0


def test_quality_skipped_without_baseline():
    no_rows = quality.rate_test((30,) * 7, (None,) * 7, params=V1)
    zero_rows = quality.rate_test((30,) * 7, (0,) * 7, params=V1)
    for got in (no_rows, zero_rows):
        assert (got.tested, got.rate_ratio, got.dispersion, got.z, got.p_value) == (False, None, None, None, None)
        assert got.note == quality.NOTE_NO_BASELINE == "基线未观测，不检验"
    empty_now = quality.rate_test((None,) * 7, (30,) * 7, params=V1)
    assert (empty_now.tested, empty_now.note) == (False, quality.NOTE_NO_CURRENT)
    # Days without a row count as 0 inside an observed window.
    partial = quality.rate_test((30, None, 30, 30, 30, 30, 30), (20,) * 7, params=V1)
    assert partial.tested and partial.rate_ratio == pytest.approx(180 / 140)
    note = quality.quality_notes({"x": no_rows}, params=V1)["x"]
    assert (note.tested, note.p_value, note.bh_adjusted, note.bh_passed, note.note) == (False, None, None, None, "基线未观测，不检验")
    assert QualityNote.model_validate(note.model_dump()) == note
    with pytest.raises(ValueError):
        quality.rate_test((1,) * 6, (1,) * 7, params=V1)
    with pytest.raises(ValueError):
        quality.rate_test((1,) * 7, (1, 1, 1, 1, 1, 1, -1), params=V1)


def test_quality_reads_its_parameters():
    """The dispersion's degrees of freedom and the BH q come from the params the set records, not a copy of their own."""
    halved = replace(V1, dispersion_dof=6)
    a12, a6 = quality.rate_test(*CASES["a"], params=V1), quality.rate_test(*CASES["a"], params=halved)
    assert a6.dispersion == pytest.approx(2 * a12.dispersion) and a6.p_value > a12.p_value
    loose = replace(V1, bh_q=0.3)
    notes = quality.quality_notes(_tests(), params=loose)
    assert (notes["b"].bh_q, notes["b"].bh_passed) == (0.3, True)  # 0.236 <= 0.3; with q = 0.10 it does not pass
    assert quality.quality_notes(_tests(), params=V1)["b"].bh_passed is False


def test_quality_note_numbers_are_finite():
    extreme = quality.rate_test((10**6,) * 7, (1, None, None, None, None, None, None), params=V1)
    assert extreme.tested and all(math.isfinite(x) for x in (extreme.rate_ratio, extreme.dispersion, extreme.z, extreme.p_value))
    assert 0.0 <= extreme.p_value <= 1.0
    assert quality.quality_notes({"x": extreme}, params=V1)["x"].p_value == extreme.p_value
