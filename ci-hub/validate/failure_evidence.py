#!/usr/bin/env python3
"""Extract validate failure evidence without deciding the verdict.

The shared Rust ``validate_status`` reader is the sole verdict authority. This
producer helper only binds a raw ledger row to observable facts: failed DAG
substeps, membership in the measured-flake registry, and whether this exact
commit/cell is a solo ``-j 4`` reproduction of an earlier rerun-required red.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping


CI_HUB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_HUB / "remediation"))
from nonzero_result import per_node_counts  # noqa: E402


DEFAULT_REGISTRY = Path(__file__).with_name("flaky-cells.json")


def flaky_cells(path: Path) -> set[str]:
    value = json.loads(path.read_text())
    rows = value.get("cells") if isinstance(value, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("flake registry cells must be a list")
    names = set()
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("cell"):
            continue
        name = str(row["cell"])
        names.add(name if "." in name else f"test.{name}")
    return names


def ledger_rows(path: Path | None) -> list[dict[str, object]]:
    if path is None or not path.is_file():
        return []
    rows = []
    with path.open(errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def failed_substeps(log_text: str) -> list[str]:
    return sorted(
        name
        for name, details in per_node_counts(log_text).items()
        if details.get("terminal") == "fail"
    )


def row_failed_substeps(row: Mapping[str, object]) -> set[str]:
    result = set()
    gates = row.get("gates")
    if not isinstance(gates, list):
        return result
    for gate in gates:
        if not isinstance(gate, Mapping):
            continue
        substeps = gate.get("failed_substeps")
        if isinstance(substeps, list):
            result.update(str(value) for value in substeps if value)
    return result


def prior_requires_rerun(row: Mapping[str, object]) -> bool:
    concurrent = row.get("concurrent_validates")
    jobs = row.get("dag_jobs")
    return (
        row.get("known_flaky_failure") is True
        or isinstance(concurrent, int) and not isinstance(concurrent, bool) and concurrent > 0
        or isinstance(jobs, int) and not isinstance(jobs, bool) and jobs > 4
    )


def build_evidence(
    *,
    log_text: str,
    registry: set[str],
    prior: list[dict[str, object]],
    commit: str,
    dag_jobs: int,
    concurrent_validates: int | None,
) -> dict[str, object]:
    cells = failed_substeps(log_text)
    cell_set = set(cells)
    flaky_failed = sorted(cell for cell in cells if cell in registry)
    confirmation_row: dict[str, object] | None = None
    if dag_jobs == 4 and concurrent_validates == 0 and cell_set:
        candidates = [
            row
            for row in prior
            if str(row.get("commit") or "") == commit
            and prior_requires_rerun(row)
            and not cell_set.isdisjoint(row_failed_substeps(row))
        ]
        if candidates:
            confirmation_row = max(
                candidates, key=lambda row: str(row.get("finished_at") or "")
            )
    return {
        "failed_substeps": cells,
        "flaky_failed_substeps": flaky_failed,
        "known_flaky_failure": bool(flaky_failed),
        "solo_rerun_confirmation": confirmation_row is not None,
        "solo_rerun_of": (
            {
                "finished_at": confirmation_row.get("finished_at"),
                "log_file": confirmation_row.get("log_file"),
            }
            if confirmation_row is not None
            else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--dag-jobs", required=True, type=int)
    parser.add_argument("--concurrent-validates", required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args(argv)
    if args.concurrent_validates == "null":
        concurrent = None
    else:
        try:
            concurrent = int(args.concurrent_validates)
        except ValueError:
            print("failure_evidence: --concurrent-validates must be integer or null", file=sys.stderr)
            return 2
    try:
        log_text = args.log.read_text(errors="replace")
        registry = flaky_cells(args.registry)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"failure_evidence: cannot load evidence: {error}", file=sys.stderr)
        return 2
    evidence = build_evidence(
        log_text=log_text,
        registry=registry,
        prior=ledger_rows(args.ledger),
        commit=args.commit,
        dag_jobs=args.dag_jobs,
        concurrent_validates=concurrent,
    )
    print(json.dumps(evidence, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
