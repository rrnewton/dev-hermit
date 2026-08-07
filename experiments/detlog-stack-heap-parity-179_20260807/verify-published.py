#!/usr/bin/env python3
"""Fail-closed self-audit of the compact fixed-179 parity artifact."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_SHA256 = {
    "denominator-179.txt": "1dd6b79c57f790eb2585206fccdb2228ee003623c66ed2079efa0fb890a6bb10",
    "results.csv": "4a631d5f1d903282b4e2620aea74c631ff603f985ff7c7c30c67fd3d651e8902",
    "summary.json": "3cc24b5850f100742310df2643ae18f0ced7662a7d4c74654fc08a4bbea5c5b4",
    "comparator-bracket.json": "8f8b75aa36f19025e8bf08893eca4158d3d93810e06bb02e8584d8b9bd1d5d19",
    "prepare.sh": "0e5831c852e232d9e12dadbcfb9f3e671cfb812ad5c4a8061b90af100d555b00",
    "probe.sh": "296d55807906d090c82d630f69d307f0b8ebd1ef064f1e91f3b82bc5c73bba37",
    "run-one.sh": "f07cc7215da5acb630bee702b3382b2126cf39ff83957ec43f96c9172932ac4d",
    "generate-dag.sh": "bf3aad38b74c3fbca16d000e92278a014ca7c16c170ade28f0358e5e00e4022f",
    "generate-repair-dag.sh": "047a926226b3bc79c1a9dae3700724bfaf3c25b47b7bec83f07f1c24afff27d2",
    "compare.py": "23ef9cdcaefb3e73c164396a21dd389947e51b2977fea477ff46f19097bfd745",
    "analyze-raw-corpus.py": "19efe604681b4ca30cfebe3641bd932730e3dc8fe0e593248550b985608d702a",
}
BACKENDS = ("ptrace-control", "dbi", "kvm", "sabre", "liteinst", "e9patch")
CANDIDATES = BACKENDS[1:]
DIMENSIONS = ("info", "stack", "heap")
STRICT_DIMENSIONS = {"info": "info_all_on", "stack": "stack", "heap": "heap"}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main() -> int:
    for name, expected in EXPECTED_SHA256.items():
        actual = digest(ROOT / name)
        if actual != expected:
            raise SystemExit(f"SHA256 mismatch for {name}: {actual} != {expected}")

    ids = tuple(
        line.strip()
        for line in (ROOT / "denominator-179.txt").read_text().splitlines()
        if line.strip()
    )
    if len(ids) != 179 or len(set(ids)) != 179:
        raise SystemExit(f"denominator is not 179 distinct IDs: {len(ids)}/{len(set(ids))}")

    with (ROOT / "results.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 179 * 6 * 3:
        raise SystemExit(f"unexpected row count: {len(rows)}")
    keys = [(row["test_id"], row["backend"], row["dimension"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate test/backend/dimension key")
    expected_keys = {
        (test_id, backend, dimension)
        for test_id in ids
        for backend in BACKENDS
        for dimension in DIMENSIONS
    }
    if set(keys) != expected_keys:
        missing = len(expected_keys - set(keys))
        extra = len(set(keys) - expected_keys)
        raise SystemExit(f"key-space mismatch: missing={missing} extra={extra}")

    summary = json.loads((ROOT / "summary.json").read_text())
    recomputed: dict[str, dict[str, int]] = {}
    for backend in BACKENDS:
        for dimension in DIMENSIONS:
            counter = Counter(
                row["result"]
                for row in rows
                if row["backend"] == backend and row["dimension"] == dimension
            )
            if sum(counter.values()) != 179:
                raise SystemExit(f"bad denominator for {backend}:{dimension}: {sum(counter.values())}")
            recomputed[f"{backend}:{dimension}"] = dict(sorted(counter.items()))
    if recomputed != summary["verdicts"]:
        raise SystemExit("summary verdicts do not recompute from results.csv")

    strict = json.loads((ROOT / "strict-verdict.json").read_text())
    for backend in CANDIDATES:
        for dimension, strict_dimension in STRICT_DIMENSIONS.items():
            counter = strict[backend][strict_dimension]
            if sum(counter.values()) != 179:
                raise SystemExit(
                    f"strict denominator mismatch for {backend}:{dimension}: {sum(counter.values())}"
                )
            if counter.get("PASS", 0) != 0:
                raise SystemExit(f"unexpected strict PASS for {backend}:{dimension}")

    bracket = json.loads((ROOT / "comparator-bracket.json").read_text())
    for dimension in DIMENSIONS:
        positive = bracket["positive"][dimension]
        negative = bracket["tampered_negative"][dimension]
        if not (
            positive["accepted"]
            and positive["records_left"] > 0
            and positive["records_left"] == positive["records_right"]
        ):
            raise SystemExit(f"qualifying positive not accepted for {dimension}")
        if not (negative["refused"] and negative["first_difference"] == 0):
            raise SystemExit(f"tampered negative not refused for {dimension}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "denominator": len(ids),
                "comparison_rows": len(rows),
                "candidate_dimension_passes": 0,
                "candidate_dimension_cells": len(CANDIDATES) * len(DIMENSIONS) * len(ids),
                "comparator_positive_dimensions": 3,
                "comparator_tampered_negative_dimensions": 3,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
