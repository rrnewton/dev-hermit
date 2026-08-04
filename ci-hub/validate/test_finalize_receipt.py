#!/usr/bin/env python3
"""Tests for finalize_receipt: schema-5 coverage{} + counts finalizer.

Each test states N (the number of synthetic test nodes) and asserts the emitted
coverage{} would SATISFY or REFUSE per the consumer rule:
  satisfied == planned_test_nodes > 0 && zero_executed_nodes == [] && absent_nodes == []
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import finalize_receipt as fr

HERE = Path(__file__).resolve().parent
MODULE = HERE / "finalize_receipt.py"


def _satisfied(cov: dict) -> bool:
    return (cov["planned_test_nodes"] > 0
            and cov["zero_executed_nodes"] == []
            and cov["absent_nodes"] == [])


def _passing_node(tag: str, n: int) -> str:
    return (
        f"[{tag}] running {n} tests\n"
        f"[{tag}] test result: ok. {n} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.1s\n"
        f"[{tag}] ✓ PASS   {tag} ({n}s)  [test result: ok. {n} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out]\n"
    )


# --- N=13 all-passing -> SATISFIES ------------------------------------------

def test_thirteen_nodes_all_pass_satisfies():
    tags = [f"test.node{i:02d}" for i in range(13)]  # N = 13
    log = "".join(_passing_node(t, i + 1) for i, t in enumerate(tags))
    row = fr.build_coverage(log, set(tags))
    cov = row["coverage"]
    assert cov["planned_test_nodes"] == 13
    assert cov["executed_test_nodes"] == 13
    assert cov["zero_executed_nodes"] == []
    assert cov["absent_nodes"] == []
    assert _satisfied(cov)
    assert row["schema_version"] == 5
    assert row["executed_tests"] == sum(range(1, 14))  # 91 passed aggregate


# --- banner-node-zero -> REFUSES (the most important clause) -----------------

def test_banner_node_zero_refuses():
    """N=2 planned nodes; one emits a `0 passed; 5 filtered out` banner + terminal
    PASS. Its passed-sum is 0 with a banner present -> zero_executed_nodes ->
    coverage NOT satisfied. This is the positive proof the guard FIRES."""
    good = "test.good"
    zero = "test.zero"
    log = (
        _passing_node(good, 7)
        + f"[{zero}] running 0 tests\n"
        + f"[{zero}] test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 5 filtered out; finished in 0.0s\n"
        + f"[{zero}] ✓ PASS   {zero} (1s)  [test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 5 filtered out]\n"
    )
    row = fr.build_coverage(log, {good, zero})
    cov = row["coverage"]
    assert cov["zero_executed_nodes"] == [zero]
    assert cov["absent_nodes"] == []
    assert cov["planned_test_nodes"] == 2
    assert cov["executed_test_nodes"] == 1
    assert not _satisfied(cov)


# --- multi-banner node aggregates -> not over-refused ------------------------

def test_multibanner_node_not_over_refused():
    """A node with a `0 passed; 213 filtered out` banner AND a `40 passed` banner
    aggregates to 40 -> NOT zero_executed. Proves node-level aggregation."""
    tag = "test.multi"
    log = (
        f"[{tag}] test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 213 filtered out; finished in 0.0s\n"
        f"[{tag}] test result: ok. 40 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.5s\n"
        f"[{tag}] ✓ PASS   {tag} (1s)\n"
    )
    row = fr.build_coverage(log, {tag})  # N = 1
    cov = row["coverage"]
    assert cov["zero_executed_nodes"] == []
    assert cov["absent_nodes"] == []
    assert _satisfied(cov)
    assert row["executed_tests"] == 40
    assert row["filtered_tests"] == 213


# --- absent planned node -> REFUSES -----------------------------------------

def test_absent_node_refuses():
    """N=2 planned; the log only mentions one -> the other is absent -> REFUSES."""
    present = "test.present"
    missing = "test.missing"
    log = _passing_node(present, 4)
    row = fr.build_coverage(log, {present, missing})
    cov = row["coverage"]
    assert cov["absent_nodes"] == [missing]
    assert cov["zero_executed_nodes"] == []
    assert not _satisfied(cov)


# --- no-banner node -> EXEMPT, SATISFIES ------------------------------------

def test_no_banner_node_exempt_satisfies():
    """A planned shell/e2e test node with terminal PASS and ZERO banners is
    EXEMPT: not zero_executed, not absent -> SATISFIES."""
    tag = "test.shell"
    log = f"[{tag}] ✓ PASS   shell/e2e node, ran real work, no libtest banner (3s)\n"
    row = fr.build_coverage(log, {tag})  # N = 1
    cov = row["coverage"]
    assert cov["zero_executed_nodes"] == []
    assert cov["absent_nodes"] == []
    assert cov["executed_test_nodes"] == 1
    assert _satisfied(cov)
    # No banner anywhere in the log -> aggregate counts are UNKNOWN (null).
    assert row["executed_tests"] is None
    assert row["filtered_tests"] is None


# --- executed_tests 0 vs null (distinguishable) -----------------------------

def test_executed_tests_zero_vs_null_distinguishable():
    # Banners present, all summing to 0 -> Some(0), NOT null.
    zero_log = (
        "[test.a] test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 9 filtered out\n"
        "[test.a] ✓ PASS a\n"
    )
    row_zero = fr.build_coverage(zero_log, {"test.a"})
    assert row_zero["executed_tests"] == 0
    assert row_zero["filtered_tests"] == 9

    # No banner at all -> null (UNKNOWN), distinct from 0.
    none_log = "[test.a] ✓ PASS a (no libtest banner)\n"
    row_none = fr.build_coverage(none_log, {"test.a"})
    assert row_none["executed_tests"] is None
    assert row_none["filtered_tests"] is None

    # And the JSON serialization keeps 0 vs null distinct.
    assert json.dumps(row_zero["executed_tests"]) == "0"
    assert json.dumps(row_none["executed_tests"]) == "null"


def test_realistic_run_executed_positive():
    tags = {f"test.n{i}" for i in range(5)}
    log = "".join(_passing_node(t, 12) for t in sorted(tags))
    row = fr.build_coverage(log, tags)
    assert row["executed_tests"] == 60  # Some(n>0)
    assert _satisfied(row["coverage"])


# --- ledger upgrade path ----------------------------------------------------

def test_ledger_upgrade_in_place(tmp_path):
    sha = "a" * 40
    ledger = tmp_path / "validate-run-ledger.jsonl"
    rows = [
        {"schema_version": 3, "commit": "b" * 40, "result": "pass", "keep": 1},
        {"schema_version": 3, "commit": sha, "result": "pass", "keep": 2, "profile": "full"},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    log = _passing_node("test.only", 5)
    fields = fr.build_coverage(log, {"test.only"})
    upgraded = fr.upgrade_ledger(str(ledger), sha, fields)
    assert upgraded == 1

    out = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    # Untouched row preserved verbatim.
    assert out[0] == rows[0]
    # Target row upgraded, other fields preserved.
    assert out[1]["schema_version"] == 5
    assert out[1]["keep"] == 2
    assert out[1]["profile"] == "full"
    assert out[1]["coverage"]["planned_test_nodes"] == 1
    assert out[1]["executed_tests"] == 5


def test_ledger_upgrade_missing_row_errors(tmp_path):
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps({"schema_version": 3, "commit": "c" * 40}) + "\n")
    proc = subprocess.run(
        [sys.executable, str(MODULE), "--log", "/dev/null",
         "--sha", "d" * 40, "--hermit-checkout", str(tmp_path),
         "--ledger", str(ledger)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "no ledger row" in proc.stderr


# --- CLI emit-only ----------------------------------------------------------

def test_cli_emit_only(tmp_path):
    log = tmp_path / "dag.log"
    log.write_text(_passing_node("test.a", 3))
    proc = subprocess.run(
        [sys.executable, str(MODULE), "--log", str(log),
         "--sha", "e" * 40, "--hermit-checkout", str(tmp_path), "--emit-only"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    obj = json.loads(proc.stdout)
    assert obj["commit"] == "e" * 40
    assert obj["schema_version"] == 5
    assert "coverage" in obj
    # tmp_path is not a git repo -> planned set empty -> planned_test_nodes 0
    # (an empty planned set NEVER satisfies; the finalizer never fabricates one).
    assert obj["coverage"]["planned_test_nodes"] == 0


# --- planned_test_nodes reads real manifests at a real SHA ------------------

def test_planned_from_real_hermit_manifest_at_head():
    """Positive control that planned_test_nodes dereferences the manifests via
    `git show <sha>:...`. Skips cleanly if the primary hermit checkout is absent.
    """
    hermit = HERE.parent.parent / "hermit"
    if not (hermit / ".git").exists() and not (hermit / ".git").is_file():
        import pytest
        pytest.skip("no hermit checkout")
    head = subprocess.run(["git", "-C", str(hermit), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode != 0:
        import pytest
        pytest.skip("hermit checkout has no HEAD")
    sha = head.stdout.strip()
    planned = fr.planned_test_nodes(str(hermit), sha)
    # Every planned tag is a test.* tag; the portable lane defines many.
    assert all(t.startswith("test.") for t in planned)
    assert "test.detcore_unit" in planned
    print(f"\n[planned-control] {len(planned)} planned test.* nodes at hermit HEAD {sha[:12]}")
