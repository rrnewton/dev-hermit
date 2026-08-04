#!/usr/bin/env python3
"""Evidence extraction controls; verdicts remain validate_status's job."""

from __future__ import annotations

import importlib.util
from pathlib import Path


PATH = Path(__file__).with_name("failure_evidence.py")
SPEC = importlib.util.spec_from_file_location("failure_evidence", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


LOG = "[test.command_strict_verify] ✗ FAIL   command strict verification\n"
COMMIT = "3a4048791b8035375bf7e90b0ced149dfaa3adf1"


def prior(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "commit": COMMIT,
        "dag_jobs": 16,
        "concurrent_validates": 0,
        "known_flaky_failure": True,
        "gates": [
            {
                "failure_origin": "lane_substep",
                "failed_substeps": ["test.command_strict_verify"],
            }
        ],
    }
    row.update(updates)
    return row


def test_measured_flake_is_bound_to_failed_cell() -> None:
    evidence = MODULE.build_evidence(
        log_text=LOG,
        registry={"test.command_strict_verify"},
        prior=[],
        commit=COMMIT,
        dag_jobs=16,
        concurrent_validates=0,
    )
    assert evidence == {
        "failed_substeps": ["test.command_strict_verify"],
        "known_flaky_failure": True,
        "solo_rerun_confirmation": False,
    }


def test_only_matching_solo_j4_run_confirms() -> None:
    base = dict(
        log_text=LOG,
        registry={"test.command_strict_verify"},
        prior=[prior()],
        commit=COMMIT,
    )
    assert MODULE.build_evidence(
        **base, dag_jobs=4, concurrent_validates=0
    )["solo_rerun_confirmation"] is True
    assert MODULE.build_evidence(
        **base, dag_jobs=16, concurrent_validates=0
    )["solo_rerun_confirmation"] is False
    assert MODULE.build_evidence(
        **base, dag_jobs=4, concurrent_validates=1
    )["solo_rerun_confirmation"] is False


def test_different_commit_or_cell_never_confirms() -> None:
    rows = [prior(commit="b" * 40), prior(gates=[{"failed_substeps": ["test.other"]}])]
    evidence = MODULE.build_evidence(
        log_text=LOG,
        registry={"test.command_strict_verify"},
        prior=rows,
        commit=COMMIT,
        dag_jobs=4,
        concurrent_validates=0,
    )
    assert evidence["solo_rerun_confirmation"] is False
