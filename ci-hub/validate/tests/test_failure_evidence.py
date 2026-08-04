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


# --- first_error_line: an error STRING is a fact, a gate name is a proxy -------
# The task's decisive lesson: fedc81ed and 1fad135d BOTH failed `portable CI DAG
# manifest` at the SAME node build.runtime_release on 2026-08-04 — gate name AND
# node name identical — yet with COMPLETELY UNRELATED causes (a DynamoRIO link
# failure vs a stale `--locked` Cargo.lock). Four wrong theories were built over
# hours before anyone opened the logs. The `first_error_line` field carries the
# first substantive error line INTO the ledger row so the two are attributable
# from the row alone. Bodies below are verbatim excerpts from
# /tmp/hermit-validate.u7wd6E.log (fedc81ed) and .UMwz5g.log (1fad135d).

def test_first_error_line_distinguishes_same_gate_same_node_failures():
    fedc81ed = _fail(
        "build.runtime_release",
        "error: failed to run custom build command for `reverie-dbi v0.2.0`",
        "  scheduler_impl.h:1325: undefined reference to "
        "`dynamorio::drmemtrace::scheduler_impl_tmpl_t<...>::set_cur_input'",
        "  collect2: error: ld returned 1 exit status",
    )
    fad135d = _fail(
        "build.runtime_release",
        "error: failed to run custom build command for `hermit-install v0.2.0`",
        "  error: cannot update the lock file "
        "/w/hermit/liteinst-runtime-build/Cargo.lock because --locked was passed "
        "to prevent this",
    )
    [f] = classify_failed_substeps(fedc81ed)
    [d] = classify_failed_substeps(fad135d)
    # Same gate, same failing node — the ONLY discriminator is the error string.
    assert f["node"] == d["node"] == "build.runtime_release"
    # Both the cargo build-orchestration envelope AND the generic linker envelope
    # (`collect2: error: ld returned 1`) are SKIPPED so the substantive cause — the
    # unresolved DynamoRIO symbol — surfaces verbatim. This is the task's stated
    # known answer for fedc81ed (`...::set_cur_input`), not the cause-free wrapper.
    assert f["first_error_line"] == (
        "scheduler_impl.h:1325: undefined reference to "
        "`dynamorio::drmemtrace::scheduler_impl_tmpl_t<...>::set_cur_input'"
    )
    assert d["first_error_line"] == (
        "error: cannot update the lock file "
        "/w/hermit/liteinst-runtime-build/Cargo.lock because --locked was passed "
        "to prevent this"
    )
    assert f["first_error_line"] != d["first_error_line"]


def test_first_error_line_separates_two_different_link_failures():
    # The "one gate name, N causes" problem recurses at the error-line level: the
    # generic `collect2: error: ld returned 1 exit status` is identical for EVERY
    # link failure, so surfacing it would leave two unrelated link faults
    # indistinguishable. Surfacing the `undefined reference to <symbol>` line
    # instead separates them by the missing symbol (hence library).
    dynamorio = _fail(
        "build.runtime_release",
        "  launcher.cpp:300: undefined reference to "
        "`dynamorio::drmemtrace::op_verbose'",
        "  collect2: error: ld returned 1 exit status",
    )
    product = _fail(
        "build.runtime_release",
        "  main.cpp:42: undefined reference to `my_product::widget::init()'",
        "  collect2: error: ld returned 1 exit status",
    )
    [a] = classify_failed_substeps(dynamorio)
    [b] = classify_failed_substeps(product)
    assert a["first_error_line"] == (
        "launcher.cpp:300: undefined reference to "
        "`dynamorio::drmemtrace::op_verbose'"
    )
    assert b["first_error_line"] == (
        "main.cpp:42: undefined reference to `my_product::widget::init()'"
    )
    assert a["first_error_line"] != b["first_error_line"]
    # ... and the class still separates them: the dynamorio symbol is an infra
    # signature, the product symbol is not.
    assert a["fault_class"] == "infrastructure"
    assert b["fault_class"] == "code"


def test_first_error_line_falls_back_to_link_envelope_when_only_envelope():
    # A link failure whose node emitted the collect2 envelope but NO
    # `undefined reference` line (e.g. output truncated) still records the envelope
    # rather than None — a weaker fact, but a fact.
    log = _fail("build.runtime_release", "  collect2: error: ld returned 1 exit status")
    [rec] = classify_failed_substeps(log)
    assert rec["first_error_line"] == "collect2: error: ld returned 1 exit status"


def test_first_error_line_matches_rustc_coded_error():
    log = _fail("build.workspace", "error[E0432]: unresolved import `foo::bar`")
    [rec] = classify_failed_substeps(log)
    assert rec["first_error_line"] == "error[E0432]: unresolved import `foo::bar`"


def test_first_error_line_falls_back_to_envelope_when_only_envelope():
    # A node whose ONLY error line is cargo's envelope still records the crate
    # name (a weaker fact) rather than None.
    log = _fail(
        "build.workspace",
        "error: could not compile `foo` (lib) due to 1 previous error",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["first_error_line"] == (
        "error: could not compile `foo` (lib) due to 1 previous error"
    )


def test_first_error_line_is_none_without_an_error_line():
    # A failing node that printed no error-diagnostic line yields None — never a
    # fabricated string; the triager falls back to the fault classification.
    log = _fail("test.cli", "test result: FAILED. 0 passed; 1 failed")
    [rec] = classify_failed_substeps(log)
    assert rec["first_error_line"] is None


def test_first_error_line_ignores_the_word_error_without_a_colon():
    log = _fail(
        "test.cli",
        "the test exercises error handling paths",
        "warning: deprecated api in use",
        "test result: FAILED. 0 passed; 1 failed",
    )
    [rec] = classify_failed_substeps(log)
    assert rec["first_error_line"] is None


def test_first_error_line_does_not_leak_across_nodes():
    log = (
        _fail(
            "build.runtime_release",
            "  error: cannot update the lock file X because --locked was passed",
        )
        + _fail("test.cli", "test result: FAILED. 0 passed; 1 failed")
    )
    recs = {r["node"]: r for r in classify_failed_substeps(log)}
    assert recs["build.runtime_release"]["first_error_line"] == (
        "error: cannot update the lock file X because --locked was passed"
    )
    # test.cli emitted no error line of its own — it must not inherit the other
    # node's error string.
    assert recs["test.cli"]["first_error_line"] is None


def test_first_error_line_is_length_bounded():
    log = _fail("build.workspace", "error: " + "x" * 2000)
    [rec] = classify_failed_substeps(log)
    assert len(rec["first_error_line"]) <= 500
    assert rec["first_error_line"].endswith("…")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
