# Frozen ordinary model-input benchmark

These two files preserve the exact original UTF-8 byte denominators from the
2026-10-05 synthetic benchmark: 17,416 bytes for 10 items and 34,116 for 20.
Their hashes match the original captured baseline. No real titles, accounts,
credentials or production rows are included. Do not reformat them or replace
them with the repeated-long-link examples.

They contain historical synthetic evidence, including the old simplified
`obs_trends` shape. They are byte-measurement inputs, not fixtures for current
HTTP/ingestion observation-schema validation. Current observation-contract
examples remain under `../obs_contract`.

From the repository root:

```sh
backend/.venv/bin/python scripts/pick-model-projection-benchmark.py
backend/.venv/bin/python scripts/pick-model-projection-benchmark.py --require-target
```

The report separates whitespace from structural reductions. The strict command
returns 2 when either original sample misses the 20% byte target; ordinary
test success does not turn that missing target into a pass. It does not measure
token savings, provider charges, fact preservation or model understanding.
Those invariants and live-model acceptance remain separate required gates.
