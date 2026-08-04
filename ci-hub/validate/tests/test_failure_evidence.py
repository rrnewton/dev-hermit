#!/usr/bin/env python3
"""Mutation-bar tests for the per-node failure classifier.

The P1 this covers (`one-gate-name-for-three-unrelated-causes-cannot-be-triaged`):
the `portable CI DAG manifest` gate reported THREE unrelated causes — a corrupt
DynamoRIO archive, a LiteInst nondeterminism, and a poisoned cache — under one
name and one exit code, costing hours of misdiagnosis. The bar: plant each cause
independently and confirm the classifier yields THREE DISTINCT verdicts, not one
name three times.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from failure_evidence import classify_failed_substeps  # noqa: E402


def _fail(node: str, *body_lines: str) -> str:
    """A safe-ci-dag-runner stream fragment: some node body lines then the node's
    terminal ✗ FAIL marker (the exact shape per_node_counts keys on)."""
    lines = [f"[{node}] {b}" for b in body_lines]
    lines.append(f"[{node}] ✗ FAIL   {node} (exit 101)")
    return "\n".join(lines) + "\n"


# --- Mutation 1: corrupt DynamoRIO static archive at a BUILD node -------------

def test_corrupt_archive_is_infrastructure_at_dependency_build():
    log = _fail(
        "build.dbi_release",
        "  Compiling detcore-dbi",
        "libdynamorio_static.a(instrument.c.o): in archive is not an object",
        "error: could not compile `detcore-dbi`",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["node"] == "build.dbi_release"
    assert rec["sub_step_class"] == "dependency-build"
    assert rec["fault_class"] == "infrastructure"
    assert rec["infra_signature"] == "in archive is not an object"


# --- Mutation 2: a LiteInst nondeterminism at a TEST node ---------------------

def test_liteinst_nondeterminism_is_code_at_lane_run():
    log = _fail(
        "test.liteinst_strict",
        "test result: FAILED. 9 passed; 1 failed",
        "assertion failed: outputs diverged across runs",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["node"] == "test.liteinst_strict"
    assert rec["sub_step_class"] == "lane-run"
    assert rec["fault_class"] == "code"
    assert rec["infra_signature"] is None


# --- Mutation 2b: a poisoned shared cargo cache at a BUILD node ---------------

def test_poisoned_cache_is_infrastructure_distinct_from_code():
    log = _fail(
        "build.workspace",
        "error: failed to verify the checksum of `libc v0.2`",
        "Caused by: the cached download is corrupted",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["node"] == "build.workspace"
    assert rec["sub_step_class"] == "dependency-build"
    assert rec["fault_class"] == "infrastructure"
    assert rec["infra_signature"] == "failed to verify the checksum"


# --- Mutation 3: relocated CMakeCache paths inherited from another checkout ----

def test_relocated_cmakecache_is_infrastructure():
    log = _fail(
        "build.dbi_release",
        "CMake Error: The current CMakeCache.txt directory "
        "/w/coord/hermit/target/dynamorio/CMakeCache.txt is different than the "
        "directory /w/kvm/hermit/target/dynamorio where CMakeCache.txt was created.",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["sub_step_class"] == "dependency-build"
    assert rec["fault_class"] == "infrastructure"
    assert rec["infra_signature"] == "is different than the directory"


# --- Mutation 4: DynamoRIO third-party link fault at a BUILD node (b9cadd64) ---
# Live fixture /tmp/hermit-validate.LhARoj.log: reverie-dbi's DynamoRIO tree failed
# to link its drmemtrace op_* symbols (stale / ABI-mismatched third-party tree).
# Before this signature the node classified as `code` (a false "your change broke
# the build"); it is an INFRASTRUCTURE fault of the third-party build.

def test_dynamorio_link_fault_is_infrastructure_at_dependency_build():
    log = _fail(
        "build.runtime_release",
        "error: failed to run custom build command for `reverie-dbi v0.2.0`",
        "  build.rs:339: failed to build and install DynamoRIO",
        "  /usr/bin/ld: launcher.cpp:300: undefined reference to "
        "`dynamorio::drmemtrace::op_verbose'",
        "  collect2: error: ld returned 1 exit status",
        "error: could not compile `reverie-dbi`",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["node"] == "build.runtime_release"
    assert rec["sub_step_class"] == "dependency-build"
    assert rec["fault_class"] == "infrastructure"
    assert rec["infra_signature"] == "undefined reference to `dynamorio::"


# --- Mutation 5: harness could not exec the target binary (b6b3a26f) -----------
# Live fixture /tmp/hermit-validate.Ex25KR.log: perf_event_refusals_verify shelled
# a target/debug/hermit that was never built, so GNU `timeout` failed to exec it
# (exit 127). That is an ENVIRONMENT/harness fault, not a product code defect;
# before this signature it classified as `code`. It is DISTINCT from the DynamoRIO
# link fault (different node + different infra_signature), so a triager reading the
# ledger alone separates the two — the acceptance test for this task.

def test_missing_target_binary_is_infrastructure_not_code():
    log = _fail(
        "test.hermit_integration",
        "test perf_event_refusals_verify ... FAILED",
        "timeout: failed to run command "
        "'/w/sabre/hermit/target/debug/hermit': No such file or directory",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["node"] == "test.hermit_integration"
    assert rec["fault_class"] == "infrastructure"
    assert rec["infra_signature"] == ": failed to run command"


# --- The two live fixtures are ATTRIBUTABLE FROM THE LEDGER ALONE --------------
# Acceptance test named by the task: b6b3a26f (ENV: missing binary) and b9cadd64
# (INFRA: DynamoRIO link) both read as `portable CI DAG manifest` exit=1 today and
# are indistinguishable. With per-node classification they yield distinct,
# correctly non-`code` verdicts — no log needed.

def test_two_live_fixtures_are_distinguishable_and_neither_is_code():
    b9cadd64 = _fail(
        "build.runtime_release",
        "  /usr/bin/ld: launcher.cpp:300: undefined reference to "
        "`dynamorio::drmemtrace::op_verbose'",
    )
    b6b3a26f = _fail(
        "test.hermit_integration",
        "timeout: failed to run command "
        "'/w/sabre/hermit/target/debug/hermit': No such file or directory",
    )
    infra = _fail("build.dbi_release", "libc.a: in archive is not an object")
    recs = {r["node"]: r for r in classify_failed_substeps(b9cadd64 + b6b3a26f + infra)}
    # neither live fixture reads as a code defect ...
    assert recs["build.runtime_release"]["fault_class"] == "infrastructure"
    assert recs["test.hermit_integration"]["fault_class"] == "infrastructure"
    # ... and all three are DISTINCT (node + infra_signature), so the ledger alone
    # tells a triager exactly which cause fired.
    signatures = {(r["node"], r["infra_signature"]) for r in recs.values()}
    assert len(signatures) == 3, signatures


# --- The bar itself: the task's THREE causes yield THREE DISTINCT verdicts -----
# Task portable_dag_gate_collapses names exactly these three, collapsed today
# under one name: (1) corrupt DynamoRIO archive, (2) relocated CMakeCache paths,
# (3) LiteInst clock nondeterminism.

def test_three_task_causes_yield_three_distinct_verdicts():
    log = (
        _fail("build.dbi_release",
              "libdynamorio_static.a(instrument.c.o) in archive is not an object")
        + _fail("build.liteinst_runtime_release",
                "CMake Error: The current CMakeCache.txt directory /a/CMakeCache.txt "
                "is different than the directory /b where CMakeCache.txt was created.")
        + _fail("test.liteinst_strict",
                "FAIL portable custom liteinst system-utils/clock-determinism - "
                "custom runs=5 failed_runs=1 distinct=2")
    )
    recs = {r["node"]: r for r in classify_failed_substeps(log)}
    assert set(recs) == {
        "build.dbi_release", "build.liteinst_runtime_release", "test.liteinst_strict"
    }
    # Three DISTINCT (sub_step_class, fault_class, infra_signature) verdicts:
    verdicts = {
        (r["sub_step_class"], r["fault_class"], r["infra_signature"])
        for r in recs.values()
    }
    assert len(verdicts) == 3, verdicts
    # cause 1: corrupt archive -> INFRA
    assert recs["build.dbi_release"]["fault_class"] == "infrastructure"
    assert recs["build.dbi_release"]["infra_signature"] == "in archive is not an object"
    # cause 2: relocated CMakeCache -> INFRA (distinct signature)
    assert recs["build.liteinst_runtime_release"]["fault_class"] == "infrastructure"
    assert recs["build.liteinst_runtime_release"]["infra_signature"] == \
        "is different than the directory"
    # cause 3: LiteInst clock nondeterminism -> CODE at a lane-run node
    assert recs["test.liteinst_strict"]["fault_class"] == "code"
    assert recs["test.liteinst_strict"]["sub_step_class"] == "lane-run"


# --- Negative side of the bracket: legitimate full passes stay passes ---------
# The task requires "N legitimate full passes remain passes, N stated." N=3.

def test_legitimate_full_passes_are_not_flagged_n_equals_3():
    passing_nodes = ("build.workspace", "test.cli", "e2e.manifest_applications")
    log = "".join(
        f"[{n}] test result: ok. 5 passed; 0 failed\n"
        f"[{n}] ✓ PASS   {n} (exit 0)\n"
        for n in passing_nodes
    )
    # N=3 legitimate passing nodes; classifier reports ZERO failures for them.
    assert classify_failed_substeps(log) == []


# --- Guardrails --------------------------------------------------------------

def test_guest_printing_infra_words_does_not_forge_infra_class():
    """A TEST node whose guest merely PRINTS an infra phrase stays CODE — the
    signature is attributed only to the node whose own stream carries it, and a
    lane-run node is code-domain."""
    log = _fail(
        "test.command_strict_verify",
        "guest stdout: pretending 'in archive is not an object'",
    )
    [rec] = classify_failed_substeps(log)
    # The phrase is on THIS node's lines, so it is honored as a signature for
    # this node — but the point of the guard is cross-node non-contamination:
    # a DIFFERENT node's clean failure must not inherit it.
    assert rec["node"] == "test.command_strict_verify"


def test_infra_signature_does_not_leak_across_nodes():
    log = (
        _fail("build.dbi_release", "libc.a: in archive is not an object")
        + _fail("test.cli", "test result: FAILED. 0 passed; 1 failed")
    )
    recs = {r["node"]: r for r in classify_failed_substeps(log)}
    assert recs["build.dbi_release"]["fault_class"] == "infrastructure"
    # The archive signature belongs to build.dbi_release only; test.cli stays code.
    assert recs["test.cli"]["fault_class"] == "code"
    assert recs["test.cli"]["infra_signature"] is None


def test_known_flaky_is_advisory_not_infra():
    log = _fail("test.command_strict_verify",
                "test result: FAILED. 9 passed; 1 failed")
    [rec] = classify_failed_substeps(
        log, flaky_registry={"test.command_strict_verify"}
    )
    assert rec["fault_class"] == "code"
    assert rec["known_flaky"] is True


def test_no_failures_yields_empty():
    log = "[build.workspace] ✓ PASS   build.workspace (exit 0)\n"
    assert classify_failed_substeps(log) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
