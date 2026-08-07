"""Per-node timing fails closed until profiles carry observable run identity.

The profile store is append-only, so ``step`` plus ``git_sha`` can name rows
from several executions of the same commit. File order is not a causal binding
to the validation log. These tests require unavailable evidence rather than a
borrowed wall/CPU verdict until one run identity reaches both artifacts.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from failure_evidence import (  # noqa: E402
    classify_failed_substeps,
    node_timing,
    timing_verdict,
)

# Column order is irrelevant to the reader (csv.DictReader), so only the columns
# the extractor actually consumes are written here.
COLUMNS = [
    "timestamp",
    "git_sha",
    "step",
    "elapsed_s",
    "user_s",
    "sys_s",
    "timed_out",
    "cpu_timed_out",
    "throttled_s",
    "quota_utilization_pct",
    "co_tenants_end",
]

COMMIT = "a" * 40


def _checkout(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    """A checkout whose runner profile store contains exactly ``rows``."""
    profiles = tmp_path / ".safe-ci-dag-runner" / "profiles"
    profiles.mkdir(parents=True)
    target = profiles / "step_profiles_TEST_MACHINE_affinity316_cpu-max-unknown.csv"
    with target.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})
    return tmp_path


def _log(tmp_path: Path, nodes: list[str]) -> str:
    lines = [
        "Hermit validation log",
        f"Root: {tmp_path}",
        "Level: full",
    ]
    for node in nodes:
        lines.append(f"[{node}] ▶ START  {node}")
        lines.append(f"[{node}] ✗ FAIL   {node} (1800s, TIMEOUT >1800s)")
    return "\n".join(lines) + "\n"


# The measured livelock signature from the task: a full core burned for the
# entire allowance (600.013 wall / 599.986 CPU).
LIVELOCK_ROW = {
    "timestamp": "2026-08-07T18:00:00",
    "git_sha": COMMIT,
    "step": "test.spins",
    "elapsed_s": "600.013",
    "user_s": "599.9",
    "sys_s": "0.086",
    "timed_out": "True",
    "cpu_timed_out": "False",
    "throttled_s": "0.0",
    "quota_utilization_pct": "99.8",
    "co_tenants_end": "0",
}

# Contention: the same wall clock, almost none of it on CPU.
CONTENTION_ROW = {
    "timestamp": "2026-08-07T18:00:00",
    "git_sha": COMMIT,
    "step": "test.waits",
    "elapsed_s": "600.0",
    "user_s": "9.2",
    "sys_s": "3.1",
    "timed_out": "True",
    "cpu_timed_out": "False",
    "throttled_s": "0.0",
    "quota_utilization_pct": "2.1",
    "co_tenants_end": "11",
}


def test_planted_cpu_shapes_refuse_without_observable_run_identity(tmp_path):
    """Even discriminating measurements are not evidence for this log yet."""
    root = _checkout(tmp_path, [LIVELOCK_ROW, CONTENTION_ROW])
    log = _log(root, ["test.spins", "test.waits"])

    records = classify_failed_substeps(log, commit=COMMIT)
    timing = {r["node"]: r["timing"] for r in records}

    for record in timing.values():
        assert record["timing_source"] == "unavailable:no-observable-run-identity"
        assert record["cpu_source"] == "unavailable:no-run-bound-cgroup-usage"
        assert record["profile_candidate_rows"] == 1
        assert record["profile_rows_matched"] == 0
        assert record["wall_seconds"] is None
        assert record["cpu_seconds"] is None
        assert record["cpu_usage_usec"] is None
        assert record["cpu_per_wall"] is None
        assert record["timing_verdict"] is None


def test_throttled_step_reads_as_contention_even_when_busy():
    """cgroup throttling is measured, so it outranks a busy-looking ratio.

    A step can be throttled into its timeout while still showing high CPU. The
    ratio alone would call that a livelock and send someone hunting a spin loop.
    """
    assert (
        timing_verdict(timed_out=True, cpu_per_wall=0.97, throttled_s=250.0)
        == "contention"
    )
    assert (
        timing_verdict(timed_out=True, cpu_per_wall=0.97, throttled_s=0.0)
        == "livelock"
    )


def test_no_verdict_for_a_node_that_did_not_time_out():
    """A node that failed for another reason gets no timing verdict, not a guess."""
    assert timing_verdict(timed_out=False, cpu_per_wall=0.99, throttled_s=0.0) is None
    assert timing_verdict(timed_out=None, cpu_per_wall=0.01, throttled_s=0.0) is None


def test_ambiguous_ratio_is_inconclusive_rather_than_forced():
    assert (
        timing_verdict(timed_out=True, cpu_per_wall=0.7, throttled_s=0.0)
        == "inconclusive"
    )
    assert (
        timing_verdict(timed_out=True, cpu_per_wall=None, throttled_s=None)
        == "inconclusive"
    )


def test_missing_profile_row_fails_closed_as_typed_unavailable(tmp_path):
    """Missing identity refuses even when the profile store is empty."""
    root = _checkout(tmp_path, [])
    log = _log(root, ["test.spins"])
    record = classify_failed_substeps(log, commit=COMMIT)[0]["timing"]
    assert record["timing_source"] == "unavailable:no-observable-run-identity"
    assert record["profile_candidate_rows"] == 0
    assert record["profile_rows_matched"] == 0
    assert record["wall_seconds"] is None
    assert record["cpu_seconds"] is None
    assert record["cpu_per_wall"] is None
    assert record["timing_verdict"] is None


def test_row_for_a_different_commit_is_not_borrowed(tmp_path):
    """Timing must bind to THIS commit, not to whatever ran here last."""
    stale = dict(LIVELOCK_ROW, git_sha="b" * 40)
    root = _checkout(tmp_path, [stale])
    log = _log(root, ["test.spins"])
    record = classify_failed_substeps(log, commit=COMMIT)[0]["timing"]
    assert record["timing_source"] == "unavailable:no-observable-run-identity"
    assert record["profile_candidate_rows"] == 0
    assert record["wall_seconds"] is None


def test_repeat_same_sha_runs_are_refused_not_resolved_by_latest(tmp_path):
    """Two same-SHA candidates cannot lend either run's timing to this log."""
    older = dict(LIVELOCK_ROW, elapsed_s="10.0", user_s="1.0", sys_s="0.0")
    rows = [older, LIVELOCK_ROW]
    root = _checkout(tmp_path, rows)
    log = _log(root, ["test.spins"])
    record = classify_failed_substeps(log, commit=COMMIT)[0]["timing"]
    assert record["timing_source"] == "unavailable:no-observable-run-identity"
    assert record["profile_candidate_rows"] == 2
    assert record["profile_rows_matched"] == 0
    assert record["wall_seconds"] is None
    assert record["cpu_seconds"] is None
    assert record["timing_verdict"] is None


def test_older_caller_without_commit_still_works(tmp_path):
    """attribute_reds.py calls this without a commit; it must not crash or lie."""
    root = _checkout(tmp_path, [LIVELOCK_ROW])
    log = _log(root, ["test.spins"])
    record = classify_failed_substeps(log)[0]["timing"]
    assert record["timing_source"] == "unavailable:no-observable-run-identity"


def test_node_timing_does_not_claim_cgroup_side_evidence_without_identity(tmp_path):
    """A row's cgroup-derived fields are not attributed without identity."""
    timing = node_timing([CONTENTION_ROW], "test.waits", COMMIT)
    assert timing["co_tenants_end"] is None
    assert timing["quota_utilization_pct"] is None
    assert timing["throttled_seconds"] is None
    assert timing["profile_row_timestamp"] is None
    assert timing["profile_candidate_rows"] == 1
    assert timing["timing_source"] == "unavailable:no-observable-run-identity"
