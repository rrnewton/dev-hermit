"""Per-node wall+CPU must make a timed-out node's CAUSE readable from the row.

A timeout that burned a full core for its whole allowance is a LIVELOCK; one
that spent the allowance waiting is CONTENTION. They demand opposite fixes, and
before these fields the ledger recorded only WHOLE-RUN user/sys time, so the two
were indistinguishable — measured 2026-08-07, 76 of 316 ledger fail rows were
timeouts that had to stay red because nothing in the row could separate them.

The bar these tests hold the fields to is the one that matters: a planted
livelock and a planted contention case must produce DIFFERENT rows. Identical
rows would mean the fields are present but not discriminating, which is the
same defect wearing a new name.
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


def test_planted_livelock_and_contention_produce_different_rows(tmp_path):
    """THE load-bearing test: both timed out, and the rows must not match."""
    root = _checkout(tmp_path, [LIVELOCK_ROW, CONTENTION_ROW])
    log = _log(root, ["test.spins", "test.waits"])

    records = classify_failed_substeps(log, commit=COMMIT)
    timing = {r["node"]: r["timing"] for r in records}

    spins = timing["test.spins"]
    waits = timing["test.waits"]

    assert spins["timing_verdict"] == "livelock"
    assert waits["timing_verdict"] == "contention"
    # Not merely different verdicts — the underlying evidence must differ too,
    # otherwise the verdict is an unfalsifiable label.
    assert spins != waits
    assert spins["cpu_per_wall"] >= 0.9
    assert waits["cpu_per_wall"] <= 0.5
    # Both really did time out, so the split is not just "one timed out".
    assert spins["timed_out"] is True and waits["timed_out"] is True
    # Wall alone cannot separate them: that is exactly why CPU had to be added.
    assert abs(spins["wall_seconds"] - waits["wall_seconds"]) < 1.0


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
    """Absent evidence must be distinguishable from evidence measured as zero."""
    root = _checkout(tmp_path, [])
    log = _log(root, ["test.spins"])
    record = classify_failed_substeps(log, commit=COMMIT)[0]["timing"]
    assert record["timing_source"] == "unavailable:no-profile-row"
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
    assert record["timing_source"] == "unavailable:no-profile-row"
    assert record["wall_seconds"] is None


def test_repeat_runs_take_the_latest_and_report_the_count(tmp_path):
    """An earlier run of the same commit in the same slot must not win, and the
    ambiguity must be visible rather than silently resolved."""
    older = dict(LIVELOCK_ROW, elapsed_s="10.0", user_s="1.0", sys_s="0.0")
    rows = [older, LIVELOCK_ROW]
    root = _checkout(tmp_path, rows)
    log = _log(root, ["test.spins"])
    record = classify_failed_substeps(log, commit=COMMIT)[0]["timing"]
    assert record["wall_seconds"] == 600.013
    assert record["profile_rows_matched"] == 2


def test_older_caller_without_commit_still_works(tmp_path):
    """attribute_reds.py calls this without a commit; it must not crash or lie."""
    root = _checkout(tmp_path, [LIVELOCK_ROW])
    log = _log(root, ["test.spins"])
    record = classify_failed_substeps(log)[0]["timing"]
    assert record["timing_source"] == "unavailable:no-profile-row"


def test_node_timing_reports_contention_side_evidence(tmp_path):
    """Throttling, quota and co-tenancy travel with the verdict as raw inputs."""
    timing = node_timing([CONTENTION_ROW], "test.waits", COMMIT)
    assert timing["co_tenants_end"] == 11
    assert timing["quota_utilization_pct"] == 2.1
    assert timing["throttled_seconds"] == 0.0
    assert timing["profile_row_timestamp"] == "2026-08-07T18:00:00"
    assert timing["timing_source"] == "safe-ci-dag-runner-step-profile"
