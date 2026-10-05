"""The original ordinary denominator cannot be replaced by repeated-link fixtures."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def benchmark_module():
    root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/pick-model-projection-benchmark.py").is_file())
    spec = importlib.util.spec_from_file_location("pick_projection_benchmark", root / "scripts/pick-model-projection-benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_pins_original_ordinary_denominators_and_measures_bytes_only():
    report = benchmark_module().benchmark_report()
    assert report["target_percent"] == 20
    assert [(sample["count"], sample["baseline_bytes"], sample["baseline_sha256"]) for sample in report["samples"]] == [
        (10, 17416, "b5ef5eedf316e61b79111f4c9ad28dc386c8f798a5c669e1d158f2059400fa73"),
        (20, 34116, "35ae2e023cdbc5d6c7298fcaba3929c8cd69060dad52e294a515a9e560d457ac"),
    ]
    assert report["measurement"] == "UTF-8 JSON bytes; not tokens, billing, fact preservation or model comprehension"
    for sample in report["samples"]:
        assert sample["whitespace_bytes"] == (1356 if sample["count"] == 10 else 2656)
        assert sample["reduction_percent"] == pytest.approx(100 * (sample["baseline_bytes"] - sample["model_bytes"]) / sample["baseline_bytes"])
        assert sample["target_met"] is (sample["model_bytes"] * 100 <= sample["baseline_bytes"] * 80)


@pytest.mark.parametrize("ten_met, twenty_met, expected_exit", [(False, False, 2), (True, False, 2), (False, True, 2), (True, True, 0)])
def test_required_target_exit_tracks_both_samples_without_changing_target(monkeypatch, capsys, ten_met, twenty_met, expected_exit):
    module = benchmark_module()
    result = {"target_percent": 20, "samples": [{"count": 10, "target_met": ten_met}, {"count": 20, "target_met": twenty_met}]}
    monkeypatch.setattr(module, "benchmark_report", lambda: result)
    assert module.main(["--require-target"]) == expected_exit
    assert json.loads(capsys.readouterr().out) == result


def test_default_report_does_not_claim_the_required_gate_passed(monkeypatch, capsys):
    module = benchmark_module()
    result = {"target_percent": 20, "samples": [{"count": 10, "target_met": False}]}
    monkeypatch.setattr(module, "benchmark_report", lambda: result)
    assert module.main([]) == 0
    assert json.loads(capsys.readouterr().out)["samples"][0]["target_met"] is False


@pytest.mark.parametrize("count", [10, 20])
def test_changed_baseline_fails_instead_of_silently_measuring_another_sample(monkeypatch, tmp_path, count):
    module = benchmark_module()
    for n in (10, 20):
        source = module.FIXTURES / f"ordinary-{n}.json"
        (tmp_path / source.name).write_bytes(source.read_bytes() + (b" " if n == count else b""))
    monkeypatch.setattr(module, "FIXTURES", tmp_path)
    with pytest.raises(ValueError, match="benchmark bytes changed"):
        module.benchmark_report()


def test_cached_installed_projection_cannot_replace_current_checkout(monkeypatch):
    module = benchmark_module()
    expected = module.benchmark_report()
    stale = ModuleType("ggwork_pick.model_projection")
    stale.model_payload = lambda payload: {}
    package = ModuleType("ggwork_pick")
    package.model_projection = stale
    monkeypatch.setitem(sys.modules, "ggwork_pick", package)
    monkeypatch.setitem(sys.modules, "ggwork_pick.model_projection", stale)
    paths_before = sys.path.copy()
    assert module.benchmark_report() == expected
    assert sys.modules["ggwork_pick.model_projection"] is stale
    assert sys.modules["ggwork_pick"] is package
    assert sys.path == paths_before
