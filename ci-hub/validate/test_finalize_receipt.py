#!/usr/bin/env python3
"""Tests for finalize_receipt: schema-5 coverage{} + counts finalizer.

Each test states N (the number of synthetic test nodes) and asserts the emitted
coverage{} would SATISFY or REFUSE per the consumer rule:
  satisfied == planned_test_nodes > 0 && zero_executed_nodes == [] && absent_nodes == []
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import finalize_receipt as fr

HERE = Path(__file__).resolve().parent
MODULE = HERE / "finalize_receipt.py"
BASE_FIELDS = {
    "base_sha": "1" * 40,
    "base_tree": "2" * 40,
    "reverie_base_sha": "3" * 40,
    "reverie_base_tree": "4" * 40,
}


def _satisfied(cov: dict) -> bool:
    return fr.qualifying_receipt.coverage_satisfied(cov)


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


def test_writer_records_exact_base_and_reverie_tree(tmp_path):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    }

    def git(repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True, env=env,
        ).stdout.strip()

    reverie = tmp_path / "reverie"
    reverie.mkdir()
    git(reverie, "init", "-q")
    (reverie / "reverie-dbi").mkdir()
    (reverie / "reverie-dbi/lib.rs").write_text("pub fn dbi() {}\n")
    git(reverie, "add", "-A")
    git(reverie, "commit", "-qm", "reverie base")
    reverie_sha = git(reverie, "rev-parse", "HEAD")

    hermit = tmp_path / "hermit"
    hermit.mkdir()
    git(hermit, "init", "-q")
    (hermit / "Cargo.lock").write_text(
        f'source = "git+https://github.com/rrnewton/reverie.git?rev={reverie_sha}#{reverie_sha}"\n'
    )
    (hermit / "src").mkdir()
    (hermit / "src/lib.rs").write_text("pub fn base() {}\n")
    git(hermit, "add", "-A")
    git(hermit, "commit", "-qm", "base")
    base = git(hermit, "rev-parse", "HEAD")
    git(hermit, "update-ref", "refs/remotes/origin/main", base)
    (hermit / "src/lib.rs").write_text("pub fn branch() {}\n")
    git(hermit, "add", "-A")
    git(hermit, "commit", "-qm", "branch")
    head = git(hermit, "rev-parse", "HEAD")

    fields = fr.build_base_evidence(str(hermit), head, str(reverie))
    assert fields["base_sha"] == base
    assert fields["base_tree"] == git(hermit, "rev-parse", f"{base}^{{tree}}")
    assert fields["reverie_base_sha"] == reverie_sha
    assert fields["reverie_base_tree"] == git(
        reverie, "rev-parse", f"{reverie_sha}^{{tree}}"
    )


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

def test_cli_emit_only_refuses_without_replayable_base(tmp_path):
    log = tmp_path / "dag.log"
    log.write_text(_passing_node("test.a", 3))
    proc = subprocess.run(
        [sys.executable, str(MODULE), "--log", str(log),
         "--sha", "e" * 40, "--hermit-checkout", str(tmp_path), "--emit-only"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "cannot record base evidence" in proc.stderr


# --- race-safe scan/append minting ------------------------------------------

def _countless_green_row(sha: str, log_path: str) -> dict:
    return {
        "schema_version": 3, "commit": sha, "result": "pass",
        "commit_anchored": True, "tree_dirty": False,
        "selection_mode": "full", "profile": "full",
        "executed_tests": None, "filtered_tests": None,
        "log_file": log_path, "real_seconds": 900,
        "admission": "ci-hub-validate-lock",
        "concurrent_validates": 0,
        "concurrency_proof": "validate_lock_owner_ancestry",
    }


def _qualifying_schema5_row(sha: str) -> dict:
    row = _countless_green_row(sha, "/unused")
    row.update(
        schema_version=5,
        executed_tests=6,
        filtered_tests=0,
        coverage={
            "planned_test_nodes": 1,
            "executed_test_nodes": 1,
            "zero_executed_nodes": [],
            "absent_nodes": [],
        },
        **BASE_FIELDS,
        producer="ci-hub-finalize-receipt",
        admission="ci-hub-validate-lock",
        concurrent_validates=0,
        concurrency_proof="validate_lock_owner_ancestry",
    )
    return row


def test_schema5_idempotency_guard_uses_canonical_coverage_authority():
    """Positive and absent-list controls bracket the finalizer consumer."""
    row = _qualifying_schema5_row("9" * 40)
    assert fr._has_satisfied_schema5(row) is True

    omitted = json.loads(json.dumps(row))
    del omitted["coverage"]["absent_nodes"]
    assert fr._has_satisfied_schema5(omitted) is False

    tampered = json.loads(json.dumps(row))
    tampered["coverage"]["absent_nodes"] = ["test.missing"]
    assert fr._has_satisfied_schema5(tampered) is False


def test_scan_mints_and_is_append_only(tmp_path, monkeypatch):
    """Scan appends a satisfied schema-5 clone WITHOUT rewriting existing rows
    (race-safe), and leaves the original count-less row byte-for-byte intact."""
    sha = "a" * 40
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 6))
    ledger = tmp_path / "validate-run-ledger.jsonl"
    other = {"schema_version": 3, "commit": "b" * 40, "result": "fail"}
    green = _countless_green_row(sha, str(log))
    original_lines = [json.dumps(other), json.dumps(green)]
    ledger.write_text("\n".join(original_lines) + "\n")

    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: {"test.only"})
    monkeypatch.setattr(fr, "build_base_evidence", lambda *a: dict(BASE_FIELDS))
    results = fr.scan_and_finalize(str(ledger), str(tmp_path))

    assert len(results) == 1 and results[0]["satisfied"] and results[0]["sha"] == sha
    out_lines = [l for l in ledger.read_text().splitlines() if l.strip()]
    # The two original lines are untouched (append-only); one new line added.
    assert out_lines[0] == original_lines[0]
    assert out_lines[1] == original_lines[1]
    assert len(out_lines) == 3
    minted = json.loads(out_lines[2])
    assert minted["schema_version"] == 5
    assert minted["commit"] == sha
    assert minted["executed_tests"] == 6
    assert minted["coverage"]["planned_test_nodes"] == 1
    assert minted["coverage"]["zero_executed_nodes"] == []
    assert minted["coverage"]["absent_nodes"] == []
    # Base fields carried so is_clean_full_coverage still holds.
    assert minted["commit_anchored"] is True and minted["profile"] == "full"


def test_scan_is_idempotent(tmp_path, monkeypatch):
    """A second scan appends nothing: the sha now carries a satisfied schema-5."""
    sha = "c" * 40
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 4))
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps(_countless_green_row(sha, str(log))) + "\n")
    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: {"test.only"})
    monkeypatch.setattr(fr, "build_base_evidence", lambda *a: dict(BASE_FIELDS))

    first = fr.scan_and_finalize(str(ledger), str(tmp_path))
    assert len([r for r in first if r["reason"] == "minted"]) == 1
    n_after_first = len([l for l in ledger.read_text().splitlines() if l.strip()])

    second = fr.scan_and_finalize(str(ledger), str(tmp_path))
    assert second == []  # already satisfied -> not a candidate
    n_after_second = len([l for l in ledger.read_text().splitlines() if l.strip()])
    assert n_after_second == n_after_first  # nothing appended


def test_scan_dry_run_writes_nothing(tmp_path, monkeypatch):
    sha = "d" * 40
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 8))
    ledger = tmp_path / "l.jsonl"
    before = json.dumps(_countless_green_row(sha, str(log))) + "\n"
    ledger.write_text(before)
    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: {"test.only"})
    monkeypatch.setattr(fr, "build_base_evidence", lambda *a: dict(BASE_FIELDS))

    results = fr.scan_and_finalize(str(ledger), str(tmp_path), dry_run=True)
    assert results[0]["satisfied"]
    assert ledger.read_text() == before  # untouched


def test_scan_skips_missing_log_and_absent_manifest(tmp_path, monkeypatch):
    gone = _countless_green_row("e" * 40, str(tmp_path / "nope.log"))
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 5))
    no_manifest = _countless_green_row("f" * 40, str(log))
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in (gone, no_manifest)) + "\n")
    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: set())  # empty planned

    results = fr.scan_and_finalize(str(ledger), str(tmp_path))
    reasons = {r["sha"][:1]: r["reason"] for r in results}
    assert reasons["e"] == "no-log"
    assert reasons["f"] == "no-manifest"
    # Neither fabricated: no schema-5 line appended.
    assert all(json.loads(l).get("schema_version") == 3
               for l in ledger.read_text().splitlines() if l.strip())


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
