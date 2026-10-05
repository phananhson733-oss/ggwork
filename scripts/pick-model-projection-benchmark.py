"""Report UTF-8 byte reduction against the frozen original ordinary samples."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "customizations/pick-workbench/tests/fixtures/model_projection"
SAMPLES = (
    (10, 17416, "b5ef5eedf316e61b79111f4c9ad28dc386c8f798a5c669e1d158f2059400fa73"),
    (20, 34116, "35ae2e023cdbc5d6c7298fcaba3929c8cd69060dad52e294a515a9e560d457ac"),
)


def benchmark_report():
    # Read the current checkout, not a possibly older installed extension.
    spec = importlib.util.spec_from_file_location(
        "pick_benchmark_subject",
        ROOT / "customizations/pick-workbench/ggwork_pick/model_projection.py",
    )
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)

    samples = []
    for count, expected_bytes, expected_hash in SAMPLES:
        raw = (FIXTURES / f"ordinary-{count}.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != expected_bytes or digest != expected_hash:
            raise ValueError(f"Original ordinary-{count} benchmark bytes changed")
        original = json.loads(raw)
        compact_before = json.dumps(
            original, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        projected = json.dumps(
            subject.model_payload(original), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        samples.append(
            {
                "count": count,
                "baseline_bytes": len(raw),
                "baseline_sha256": digest,
                "model_bytes": len(projected),
                "whitespace_bytes": len(raw) - len(compact_before),
                "structural_bytes": len(compact_before) - len(projected),
                "reduction_percent": 100 * (len(raw) - len(projected)) / len(raw),
                "target_met": len(projected) * 100 <= len(raw) * 80,
            }
        )
    return {
        "sample_set": "original-ordinary-2026-10-05",
        "target_percent": 20,
        "measurement": "UTF-8 JSON bytes; not tokens, billing, fact preservation or model comprehension",
        "samples": samples,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-target",
        action="store_true",
        help="Exit 2 unless both original samples reach at least 20 percent",
    )
    args = parser.parse_args(argv)
    report = benchmark_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (
        2
        if args.require_target
        and not all(sample["target_met"] for sample in report["samples"])
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
