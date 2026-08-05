#!/usr/bin/env python3
"""Tests for schema-5 coverage plus schema-6 dependency-bound finalization.

Each test states N (the number of synthetic test nodes) and asserts the emitted
coverage{} would SATISFY or REFUSE per the consumer rule:
  satisfied == planned_test_nodes > 0 && zero_executed_nodes == [] && absent_nodes == []
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import finalize_receipt as fr

MODULE = HERE / "finalize_receipt.py"


def _binding() -> dict:
    sha = "9" * 40
    return {
        "repository": "rrnewton/reverie",
        "ref": "refs/heads/main",
        "pinned_sha": sha,
        "resolved_sha": sha,
    }


def _binding_resolver(_checkout: str, shas: list[str]):
    return ({sha: _binding() for sha in shas}, {})


def _satisfied(cov: dict) -> bool:
    return (cov["planned_test_nodes"] > 0
            and cov["executed_test_nodes"] == cov["planned_test_nodes"]
            and cov["zero_executed_nodes"] == []
            and cov["absent_nodes"] == []
            and cov["failed_nodes"] == [])


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


# --- arbitrary-log ledger rewriting is disabled -----------------------------

def test_log_cannot_rewrite_a_ledger(tmp_path):
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps({"schema_version": 3, "commit": "c" * 40}) + "\n")
    proc = subprocess.run(
        [sys.executable, str(MODULE), "--log", "/dev/null",
         "--sha", "d" * 40, "--hermit-checkout", str(tmp_path),
         "--repo", "rrnewton/hermit", "--ledger", str(ledger)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "cannot be combined with --ledger" in proc.stderr


# --- CLI emit-only ----------------------------------------------------------

def test_cli_emit_only_refuses_missing_required_manifests(tmp_path):
    log = tmp_path / "dag.log"
    log.write_text(_passing_node("test.a", 3))
    proc = subprocess.run(
        [sys.executable, str(MODULE), "--log", str(log),
         "--sha", "e" * 40, "--hermit-checkout", str(tmp_path), "--emit-only"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 4
    assert "cannot read required manifest" in proc.stderr


def test_failed_terminal_and_unplanned_banners_cannot_launder_coverage():
    failed = "test.failed"
    unplanned = "test.decoy"
    log = (
        _passing_node(unplanned, 999)
        + f"[{failed}] running 3 tests\n"
        + f"[{failed}] test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
        + f"[{failed}] ✗ FAIL deliberate\n"
    )
    row = fr.build_coverage(log, {failed})
    assert row["executed_tests"] == 3
    assert row["coverage"]["executed_test_nodes"] == 0
    assert row["coverage"]["failed_nodes"] == [failed]
    assert not _satisfied(row["coverage"])


# --- race-safe scan/append minting ------------------------------------------

def _countless_green_row(sha: str, log_path: str) -> dict:
    return {
        "schema_version": 3, "commit": sha, "result": "pass",
        "commit_anchored": True, "tree_dirty": False,
        "selection_mode": "full", "profile": "full",
        "failures": 0,
        "started_at": "2026-08-05T01:00:00Z",
        "finished_at": "2026-08-05T01:10:00Z",
        "executed_tests": None, "filtered_tests": None,
        "log_file": log_path, "real_seconds": 900,
    }


def test_scan_mints_and_is_append_only(tmp_path, monkeypatch):
    """Scan appends a satisfied schema-6 clone WITHOUT rewriting existing rows
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
    results = fr.scan_and_finalize(
        str(ledger), str(tmp_path), binding_resolver=_binding_resolver
    )

    assert len(results) == 1 and results[0]["satisfied"] and results[0]["sha"] == sha
    out_lines = [l for l in ledger.read_text().splitlines() if l.strip()]
    # The two original lines are untouched (append-only); one new line added.
    assert out_lines[0] == original_lines[0]
    assert out_lines[1] == original_lines[1]
    assert len(out_lines) == 3
    minted = json.loads(out_lines[2])
    assert minted["schema_version"] == 6
    assert minted["reverie_binding"] == _binding()
    assert minted["commit"] == sha
    assert minted["executed_tests"] == 6
    assert minted["coverage"]["planned_test_nodes"] == 1
    assert minted["coverage"]["zero_executed_nodes"] == []
    assert minted["coverage"]["absent_nodes"] == []
    assert minted["source_log_sha256"] == fr.hashlib.sha256(log.read_bytes()).hexdigest()
    assert minted["receipt_finalizer"]["id"] == fr.FINALIZER_ID
    # Base fields carried so is_clean_full_coverage still holds.
    assert minted["commit_anchored"] is True and minted["profile"] == "full"


def test_scan_is_idempotent(tmp_path, monkeypatch):
    """A second scan appends nothing: the sha carries the same bound schema-6."""
    sha = "c" * 40
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 4))
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps(_countless_green_row(sha, str(log))) + "\n")
    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: {"test.only"})

    first = fr.scan_and_finalize(
        str(ledger), str(tmp_path), binding_resolver=_binding_resolver
    )
    assert len([r for r in first if r["reason"] == "minted"]) == 1
    n_after_first = len([l for l in ledger.read_text().splitlines() if l.strip()])

    second = fr.scan_and_finalize(
        str(ledger), str(tmp_path), binding_resolver=_binding_resolver
    )
    assert len(second) == 1 and second[0]["reason"] == "already-finalized"
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

    results = fr.scan_and_finalize(
        str(ledger), str(tmp_path), dry_run=True, binding_resolver=_binding_resolver
    )
    assert results[0]["satisfied"]
    assert ledger.read_text() == before  # untouched


def test_source_change_during_derivation_refuses_stale_append(tmp_path, monkeypatch):
    """Simulate a concurrent validate append while the network authority runs.
    The finalizer reselects under the short append lock and refuses its stale
    proposal instead of binding the older run's log to the newer run."""
    sha = "8" * 40
    old_log = tmp_path / "old.log"
    new_log = tmp_path / "new.log"
    old_log.write_text(_passing_node("test.only", 3))
    new_log.write_text(_passing_node("test.only", 7))
    old = _countless_green_row(sha, str(old_log))
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps(old) + "\n")
    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: {"test.only"})

    def append_during_resolution(_checkout: str, shas: list[str]):
        newer = _countless_green_row(sha, str(new_log))
        newer["started_at"] = "2026-08-05T02:00:00Z"
        newer["finished_at"] = "2026-08-05T02:10:00Z"
        with ledger.open("a") as output:
            output.write(json.dumps(newer) + "\n")
        return ({item: _binding() for item in shas}, {})

    results = fr.scan_and_finalize(
        str(ledger), str(tmp_path), binding_resolver=append_during_resolution
    )
    assert results[-1]["reason"] == "source-changed"
    assert not results[-1]["satisfied"]
    assert len(ledger.read_text().splitlines()) == 2


def test_scan_skips_missing_log_and_absent_manifest(tmp_path, monkeypatch):
    gone = _countless_green_row("e" * 40, str(tmp_path / "nope.log"))
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 5))
    no_manifest = _countless_green_row("f" * 40, str(log))
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in (gone, no_manifest)) + "\n")
    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: set())  # empty planned

    results = fr.scan_and_finalize(
        str(ledger), str(tmp_path), binding_resolver=_binding_resolver
    )
    reasons = {r["sha"][:1]: r["reason"] for r in results}
    assert reasons["e"] == "no-log"
    assert reasons["f"] == "no-manifest"
    # Neither fabricated: no schema-6 line appended.
    assert all(json.loads(l).get("schema_version") == 3
               for l in ledger.read_text().splitlines() if l.strip())


def test_scan_refuses_to_mint_without_fresh_reverie_binding(tmp_path):
    sha = "7" * 40
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 5))
    ledger = tmp_path / "l.jsonl"
    original = json.dumps(_countless_green_row(sha, str(log))) + "\n"
    ledger.write_text(original)

    def refused(_checkout: str, _shas: list[str]):
        return {}, {sha: "live Reverie main moved"}

    results = fr.scan_and_finalize(
        str(ledger), str(tmp_path), binding_resolver=refused
    )
    assert results == [{
        "sha": sha,
        "satisfied": False,
        "reason": "reverie-pin",
        "detail": "live Reverie main moved",
    }]
    assert ledger.read_text() == original


def test_superficial_schema6_row_cannot_skip_log_recomputation(tmp_path, monkeypatch):
    """A forged satisfied-looking row is not an idempotency token. The finalizer
    rereads the original row's exact log and appends only the derived clone."""
    sha = "6" * 40
    log = tmp_path / "run.log"
    log.write_text(_passing_node("test.only", 9))
    source = _countless_green_row(sha, str(log))
    forged = dict(source)
    forged.update({
        "schema_version": 6,
        "executed_tests": 999,
        "filtered_tests": 0,
        "coverage": {
            "planned_test_nodes": 100,
            "executed_test_nodes": 1,
            "zero_executed_nodes": [],
            "absent_nodes": [],
        },
        "reverie_binding": _binding(),
        "receipt_finalizer": {"id": "attacker", "source_row_sha256": "0" * 64},
    })
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps(source) + "\n" + json.dumps(forged) + "\n")
    monkeypatch.setattr(fr, "planned_test_nodes", lambda c, s: {"test.only"})

    results = fr.scan_and_finalize(
        str(ledger), str(tmp_path), binding_resolver=_binding_resolver
    )
    assert results[0]["reason"] == "minted"
    minted = json.loads(ledger.read_text().splitlines()[-1])
    assert minted["executed_tests"] == 9
    assert minted["coverage"]["planned_test_nodes"] == 1
    assert minted["coverage"]["executed_test_nodes"] == 1


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
