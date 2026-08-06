#!/usr/bin/env python3
"""Rebase-wrapper: soft-green is a confidence LEVEL; an absent judgement is REFUSED.

Verifies BOTH directions the owner named: a zero-conflict rebase is soft-greened
mechanically; a conflicted rebase WITHOUT a risk judgement is refused, never
defaulted to green. Also that the base's floor status is carried, that hard green
at X is inherited without requiring a redundant receipt at Z, that exact hard
green at Z upgrades the basis, and that known red/disagreement remains visible.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PATH = Path(__file__).with_name("rebase_wrapper.py")
SPEC = importlib.util.spec_from_file_location("rebase_wrapper", PATH)
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

X = "a" * 40
Y = "b" * 40
Z = "c" * 40
Z2 = "d" * 40


# --------------------------------------------------------------------------- #
# derive_verdict: the pure heart of the mechanism (both directions)            #
# --------------------------------------------------------------------------- #
def test_zero_conflict_is_soft_green_zero_conflict_and_landable() -> None:
    v = M.derive_verdict([], None, None, base_clears_floor=True, base_unmet=[])
    assert v["soft_green"] == M.SOFT_ZERO_CONFLICT
    assert v["risk_judgement"] == M.RISK_NA
    assert v["landable"] is True


def test_zero_conflict_but_base_below_floor_is_not_landable() -> None:
    unmet = [{"sha": "e" * 40, "kind": "merge-gate", "field": "merge-gate-v2"}]
    v = M.derive_verdict([], None, None, base_clears_floor=False, base_unmet=unmet)
    # Confidence LEVEL still records the zero-conflict bet ...
    assert v["soft_green"] == M.SOFT_ZERO_CONFLICT
    # ... but landability carries the base: a sub-floor base yields unlandable Z.
    assert v["landable"] is False
    assert "unlandable-base-below-floor" in v["landable_reason"]


def test_conflict_retained_is_soft_green_resolver_judged() -> None:
    v = M.derive_verdict(["src/lib.rs"], M.RISK_RETAIN, "trivial import reorder",
                         base_clears_floor=True, base_unmet=[])
    assert v["soft_green"] == M.SOFT_RESOLVER_JUDGED
    assert v["landable"] is True


def test_conflict_needs_full_validate_is_not_soft_green() -> None:
    v = M.derive_verdict(["src/lib.rs"], M.RISK_VALIDATE, "touches shared fixture",
                         base_clears_floor=True, base_unmet=[])
    assert v["soft_green"] is None
    assert v["landable"] is False
    assert "needs-full-validate" in v["landable_reason"]


def test_conflict_without_judgement_is_refused() -> None:
    for bad in (None, "", "green", "lgtm"):
        try:
            M.derive_verdict(["src/lib.rs"], bad, "some rationale",
                             base_clears_floor=True, base_unmet=[])
        except M.Refused:
            continue
        raise AssertionError(f"absent/invalid judgement {bad!r} must be REFUSED")


def test_conflict_with_judgement_but_no_rationale_is_refused() -> None:
    try:
        M.derive_verdict(["src/lib.rs"], M.RISK_RETAIN, "   ",
                         base_clears_floor=True, base_unmet=[])
    except M.Refused:
        return
    raise AssertionError("a bare judgement with no rationale must be REFUSED")


def test_soft_green_levels_are_distinguishable() -> None:
    assert M.SOFT_ZERO_CONFLICT != M.SOFT_RESOLVER_JUDGED


# --------------------------------------------------------------------------- #
# exact hard at X is inherited; exact hard at Z is an optional stronger basis   #
# --------------------------------------------------------------------------- #
def test_zero_conflict_inherits_source_hard_green_without_result_receipt() -> None:
    v = M.derive_verdict([], None, None, base_clears_floor=True, base_unmet=[],
                         source_hard_green_present=True,
                         result_hard_green_present=False, result=Z)
    assert v["soft_green"] == M.SOFT_ZERO_CONFLICT
    assert v["landable"] is True
    assert v["eligibility_kind"] == "soft-green(inherited-source)"
    assert "post-land" in v["landable_reason"]


def test_exact_result_hard_green_upgrades_landability_basis() -> None:
    v = M.derive_verdict([], None, None, base_clears_floor=True, base_unmet=[],
                         source_hard_green_present=False,
                         result_hard_green_present=True, result=Z)
    assert v["landable"] is True
    assert v["eligibility_kind"] == "hard-green(exact-result)"


def test_resolver_retained_inherits_source_hard_green() -> None:
    v = M.derive_verdict(["src/lib.rs"], M.RISK_RETAIN, "trivial reorder",
                         base_clears_floor=True, base_unmet=[],
                         source_hard_green_present=True,
                         result_hard_green_present=False, result=Z)
    assert v["soft_green"] == M.SOFT_RESOLVER_JUDGED
    assert v["landable"] is True


def test_landable_reason_floor_blocks_even_with_source_hard_green() -> None:
    unmet = [{"sha": "e" * 40, "kind": "merge-gate", "field": "v2"}]
    r = M.landable_reason(M.SOFT_ZERO_CONFLICT, base_clears_floor=False,
                          base_unmet=unmet, source_hard_green_present=True,
                          result_hard_green_present=False, result=Z)
    assert "unlandable-base-below-floor" in r


# --------------------------------------------------------------------------- #
# receipt_identity: pure map from validate-status report -> receipt or None    #
# --------------------------------------------------------------------------- #
def test_receipt_identity_validated_carries_what_it_verified() -> None:
    report = {"verdict": "VALIDATED", "qualifying_count": 1,
              "newest_qualifying": {"profile": "full", "selection_mode": "full",
                                    "result": "pass", "finished_at": "t",
                                    "slot": "lander2", "host": "testhost"}}
    r = M.receipt_identity(report, Z)
    assert r is not None
    assert r["sha"] == Z and r["verdict"] == "VALIDATED" and r["profile"] == "full"


def test_receipt_identity_not_validated_is_none() -> None:
    assert M.receipt_identity({"verdict": "NOT-VALIDATED", "qualifying_count": 0,
                               "newest_qualifying": None}, Z) is None
    # A VALIDATED verdict with no qualifying record is still no receipt.
    assert M.receipt_identity({"verdict": "VALIDATED", "qualifying_count": 0,
                               "newest_qualifying": None}, Z) is None
    assert M.receipt_identity(None, Z) is None


# --------------------------------------------------------------------------- #
# parse_conflicts                                                              #
# --------------------------------------------------------------------------- #
def test_parse_conflicts() -> None:
    assert M.parse_conflicts(None) == []
    assert M.parse_conflicts("none") == []
    assert M.parse_conflicts("  NONE ") == []
    assert M.parse_conflicts("a.rs,b.rs") == ["a.rs", "b.rs"]
    assert M.parse_conflicts("a.rs b.rs") == ["a.rs", "b.rs"]


# --------------------------------------------------------------------------- #
# store + latest-per-Z                                                         #
# --------------------------------------------------------------------------- #
def test_store_roundtrip_and_latest_wins(tmp_path) -> None:
    store = str(tmp_path / "rebase-records.jsonl")
    M.append_record(store, {"result": Z, "soft_green": None, "note": "first"})
    M.append_record(store, {"result": Z, "soft_green": M.SOFT_RESOLVER_JUDGED,
                            "note": "second"})
    recs = M.load_records(store)
    assert len(recs) == 2
    latest = M.latest_by_result(recs)
    # Re-record supersedes: the later line wins for the same Z.
    assert latest[Z]["note"] == "second"


# --------------------------------------------------------------------------- #
# do_record + do_eligible end-to-end (git stubbed out)                         #
# --------------------------------------------------------------------------- #
_STUB_RECEIPT = {"sha": Z, "verdict": "VALIDATED", "profile": "full",
                 "qualifying_count": 1}
_HARD_X = {"schema_version": 1, "sha": X, "verdict": "HARD_GREEN",
           "passing_authorities": ["local-full-validate"], "sources": {}}
_HARD_Z = {"schema_version": 1, "sha": Z, "verdict": "HARD_GREEN",
           "passing_authorities": ["github-portable+privileged"], "sources": {}}
_DURABLE_HARD_X = {
    "schema_version": 1, "sha": X, "verdict": "HARD_GREEN",
    "passing_authorities": ["github-portable+privileged"],
    "sources": {"github": {"lanes": [
        {"state": "passed", "sha": X, "run_id": 10, "job_id": 20},
        {"state": "passed", "sha": X, "run_id": 11, "job_id": 21},
    ]}},
}


def _args(argv, store, monkeypatch, clears=True, unmet=None, receipt=True):
    """Parse argv and stub floor + exact-SHA hard-green authorities."""
    args = M.build_parser().parse_args(argv + ["--store", store, "--no-fetch"]
                                       if "record" == argv[0] else argv + ["--store", store])
    monkeypatch.setattr(M, "base_floor_status",
                        lambda *a, **k: {"ok": clears, "unmet": unmet or []})
    monkeypatch.setattr(M, "hard_green_status", lambda rev: (
        _HARD_X if rev == X else (_HARD_Z if receipt and rev == Z else {
            "schema_version": 1, "sha": rev, "verdict": "NO_RESULT",
            "passing_authorities": [], "sources": {}})))
    monkeypatch.setattr(M, "utc_now", lambda: "2026-08-04T00:00:00Z")
    return args


def _append_clean(store: str, *, source_hard=True, result_hard=False,
                  base_ok=True) -> dict:
    sx = _HARD_X if source_hard else None
    rz = _HARD_Z if result_hard else None
    verdict = M.derive_verdict(
        [], None, None, base_clears_floor=base_ok, base_unmet=[],
        source_hard_green_present=source_hard,
        result_hard_green_present=result_hard, result=Z)
    rec = M.build_record(
        X, Y, Z, [], None, verdict, "2026-08-04T00:00:00Z",
        source_hard_green=sx, result_hard_green=rz)
    M.append_record(store, rec)
    return rec


def _stub_live_source(monkeypatch, *, result=None) -> None:
    result_report = result or {"schema_version": 1, "sha": Z,
                               "verdict": "NO_RESULT", "sources": {}}
    monkeypatch.setattr(M, "hard_green_status",
                        lambda rev: _HARD_X if rev == X else result_report)


def test_record_refuses_caller_asserted_zero_conflict(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    try:
        M.do_record(_args(
            ["record", "--source", X, "--base", Y, "--result", Z,
             "--conflicts", "none"], store, monkeypatch))
    except M.Refused as error:
        assert "only the wrapper-owned `rebase`" in str(error)
        assert M.load_records(store) == []
        return
    raise AssertionError("caller-asserted clean rebase must be refused")


def test_record_requires_full_commit_ids(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    args = _args([
        "record", "--source", X[:12], "--base", Y, "--result", Z,
        "--conflicts", "a.rs", "--risk-judgement", M.RISK_RETAIN,
        "--rationale", "fixture",
    ], store, monkeypatch)
    try:
        M.do_record(args)
    except M.RebaseError as error:
        assert "full 40-hex" in str(error)
        return
    raise AssertionError("abbreviated authority keys must be refused")


def test_wrapper_owned_clean_rebase_mints_inherited_soft_green(
        tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    args = M.build_parser().parse_args([
        "rebase", "--source", X, "--onto", Y, "--no-fetch",
        "--store", store,
    ])
    monkeypatch.setattr(M, "resolve_rev", lambda checkout, rev: Z if rev == "HEAD" else rev)
    monkeypatch.setattr(M, "_git", lambda *args, **kwargs: _cp(0))
    monkeypatch.setattr(M, "_run", lambda *args, **kwargs: _cp(0))
    monkeypatch.setattr(M, "base_floor_status",
                        lambda *args, **kwargs: {"ok": True, "unmet": []})
    monkeypatch.setattr(M, "hard_green_status", lambda rev: (
        _HARD_X if rev == X else {"sha": rev, "verdict": "NO_RESULT"}))
    monkeypatch.setattr(M, "utc_now", lambda: "2026-08-04T00:00:00Z")
    assert M.do_rebase(args) == M.EXIT_OK
    rec = M.load_records(store)[-1]
    assert rec["source_rev"] == X and rec["result"] == Z
    assert rec["soft_green"] == M.SOFT_ZERO_CONFLICT
    assert rec["eligibility_kind"] == "soft-green(inherited-source)"
    assert rec["landable"] is True


def test_do_record_conflict_without_judgement_refused(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    try:
        M.do_record(_args(
            ["record", "--source", X, "--base", Y, "--result", Z,
             "--conflicts", "a.rs"], store, monkeypatch))
    except M.Refused:
        # And nothing eligible was written.
        assert M.load_records(store) == []
        return
    raise AssertionError("conflicted record with no judgement must be REFUSED")


def test_eligible_result_not_landable_exits_refused(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    # needs-full-validate => recorded but not landable.
    M.do_record(_args(
        ["record", "--source", X, "--base", Y, "--result", Z, "--conflicts",
         "a.rs", "--risk-judgement", M.RISK_VALIDATE, "--rationale", "fixture"],
        store, monkeypatch))
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor",
         "--no-recheck-receipt"])
    assert M.do_eligible(q) == M.EXIT_REFUSED


def test_eligible_unknown_head_is_refused(tmp_path) -> None:
    store = str(tmp_path / "empty.jsonl")
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor",
         "--no-recheck-receipt"])
    try:
        M.do_eligible(q)
    except M.Refused:
        return
    raise AssertionError("querying an unrecorded head must be REFUSED")


def test_eligible_recheck_floor_demotes_stale_record(tmp_path, monkeypatch) -> None:
    """A floor added AFTER recording must demote a previously-landable head."""
    store = str(tmp_path / "s.jsonl")
    _append_clean(store)
    # Now a new floor is live; the base no longer clears it.
    monkeypatch.setattr(M.gate_floors, "load_floors", lambda p: [{"sha": "f" * 40}])
    monkeypatch.setattr(M.gate_floors, "clears_all",
                        lambda floors, co, base: {
                            "ok": False,
                            "unmet": [{"sha": "f" * 40, "kind": "merge-gate",
                                       "field": "v3"}]})
    monkeypatch.setattr(M, "hard_green_status", lambda rev: _HARD_X if rev == X else {
        "sha": rev, "verdict": "NO_RESULT"})
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store])
    assert M.do_eligible(q) == M.EXIT_REFUSED


def test_no_result_receipt_is_landable_via_inherited_source(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    rec = _append_clean(store, source_hard=True, result_hard=False)
    assert rec["soft_green"] == M.SOFT_ZERO_CONFLICT
    assert rec["landable"] is True
    assert rec["eligibility_kind"] == "soft-green(inherited-source)"
    _stub_live_source(monkeypatch)
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor"])
    assert M.do_eligible(q) == M.EXIT_OK


def test_live_exact_result_hard_green_upgrades_basis(tmp_path, monkeypatch, capsys) -> None:
    store = str(tmp_path / "s.jsonl")
    _append_clean(store, source_hard=True, result_hard=False)
    _stub_live_source(monkeypatch)
    q0 = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor", "--json"])
    assert M.do_eligible(q0) == M.EXIT_OK
    assert json.loads(capsys.readouterr().out)["eligibility_kind"] == "soft-green(inherited-source)"
    monkeypatch.setattr(M, "hard_green_status",
                        lambda rev: _HARD_X if rev == X else _HARD_Z)
    q1 = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor", "--json"])
    assert M.do_eligible(q1) == M.EXIT_OK
    assert json.loads(capsys.readouterr().out)["eligibility_kind"] == "hard-green(exact-result)"


def test_live_exact_result_red_vetoes_inherited_soft_green(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    _append_clean(store, source_hard=True, result_hard=False)
    monkeypatch.setattr(M, "hard_green_status", lambda rev: (
        _HARD_X if rev == X else {"sha": Z, "verdict": "HARD_RED"}))
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor"])
    assert M.do_eligible(q) == M.EXIT_REFUSED


# --------------------------------------------------------------------------- #
# Fix A: canonical store resolution -- a COPY must not diverge onto its own store #
# --------------------------------------------------------------------------- #
def test_default_store_env_override_wins(monkeypatch, tmp_path) -> None:
    """CI_HUB_REBASE_STORE pins ONE shared path regardless of __file__/parent."""
    target = str(tmp_path / "shared" / "rebase-records.jsonl")
    monkeypatch.setenv("CI_HUB_REBASE_STORE", target)
    assert M.default_store() == __import__("os").path.abspath(target)


def test_default_store_anchored_to_parent_env_not_file(monkeypatch, tmp_path) -> None:
    """With no explicit store, the path anchors to DEV_HERMIT_PARENT -- so a copy
    of the wrapper with a DIFFERENT __file__ still resolves the SAME store. This is
    the divergence dbi flagged (scratch/slot copy -> its own store), closed."""
    monkeypatch.delenv("CI_HUB_REBASE_STORE", raising=False)
    monkeypatch.setenv("DEV_HERMIT_PARENT", str(tmp_path))
    got = M.default_store()
    assert got == str(tmp_path / "ignored" / "rebase-records.jsonl")
    # parent_root honours the env over __file__ (the copy-independence guarantee).
    assert M.parent_root() == str(tmp_path)


def test_default_store_falls_back_to_file_when_no_env(monkeypatch) -> None:
    monkeypatch.delenv("CI_HUB_REBASE_STORE", raising=False)
    monkeypatch.delenv("DEV_HERMIT_PARENT", raising=False)
    # No env -> three levels up from the module file (dev-hermit parent).
    assert M.default_store().endswith("/ignored/rebase-records.jsonl")


# --------------------------------------------------------------------------- #
# receipt_status: an authority FAILURE is UNKNOWN, never ABSENT                 #
# --------------------------------------------------------------------------- #
def _cp(returncode=0, stdout="", stderr=""):
    import subprocess
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_receipt_status_validated(monkeypatch) -> None:
    report = {"verdict": "VALIDATED", "qualifying_count": 1,
              "newest_qualifying": {"profile": "full", "selection_mode": "full",
                                    "result": "pass", "finished_at": "t",
                                    "slot": "s", "host": "h"}}
    monkeypatch.setattr(M, "_run", lambda *a, **k: _cp(0, json.dumps(report)))
    rs = M.receipt_status(Z)
    assert rs["status"] == M.RECEIPT_VALIDATED and rs["identity"]["sha"] == Z


def test_receipt_status_absent_when_authority_answers_not_validated(monkeypatch) -> None:
    report = {"verdict": "NOT-VALIDATED", "qualifying_count": 0,
              "newest_qualifying": None}
    monkeypatch.setattr(M, "_run", lambda *a, **k: _cp(1, json.dumps(report)))
    rs = M.receipt_status(Z)
    assert rs["status"] == M.RECEIPT_ABSENT and rs["identity"] is None


def test_receipt_status_unknown_on_authority_failure(monkeypatch) -> None:
    """The load-bearing distinction: could-not-reach is UNKNOWN, never ABSENT."""
    # (a) tool cannot be invoked.
    def boom(*a, **k):
        raise OSError("validate-status not found")
    monkeypatch.setattr(M, "_run", boom)
    assert M.receipt_status(Z)["status"] == M.RECEIPT_UNKNOWN
    # (b) tool ran but emitted no parseable JSON.
    monkeypatch.setattr(M, "_run", lambda *a, **k: _cp(3, "traceback: kaboom"))
    assert M.receipt_status(Z)["status"] == M.RECEIPT_UNKNOWN


# --------------------------------------------------------------------------- #
# CLOSURE BAR: a planted hard source survives a transient authority failure     #
# --------------------------------------------------------------------------- #
def _no_result_hard(rev):
    return {"schema_version": 1, "sha": rev, "verdict": "NO_RESULT",
            "reason": "authority temporarily unreachable"}


def test_closure_bar_targeted_head_refuses_unverified_cache_during_outage(
        tmp_path, monkeypatch, capsys) -> None:
    """A frozen JSONL positive is visible but cannot authorize during an outage."""
    store = str(tmp_path / "s.jsonl")
    _append_clean(store, source_hard=True, result_hard=False)
    monkeypatch.setattr(M, "hard_green_status", _no_result_hard)
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor",
         "--json"])
    rc = M.do_eligible(q)
    out = json.loads(capsys.readouterr().out)
    assert rc == M.EXIT_REFUSED
    assert out["result"] == Z
    assert out["eligible"] is False
    assert out["source_hard_green_state"] == "NO_RESULT"


def test_closure_bar_reconciled_head_remains_visible_as_unknown(
        tmp_path, monkeypatch, capsys) -> None:
    """Reconciliation shows, rather than trusts, an unverified cached positive."""
    store = str(tmp_path / "s.jsonl")
    _append_clean(store, source_hard=True, result_hard=False)
    monkeypatch.setattr(M, "open_pushed_prs",
                        lambda repo: [{"number": 7, "headRefOid": Z,
                                       "headRefName": "feat", "url": "u"}])
    monkeypatch.setattr(M, "hard_green_status", _no_result_hard)
    q = M.build_parser().parse_args(
        ["eligible", "--store", store, "--no-recheck-floor", "--json"])
    assert M.do_eligible(q) == M.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["eligible"] == 0
    assert out["summary"]["hard-green-unknown"] == 1
    assert out["hard_green_unknown"][0]["result"] == Z
    assert out["reconciled"] is True


def test_planted_legacy_receipt_snapshot_cannot_authorize(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    M.append_record(store, {
        "schema_version": 1, "source_rev": X, "base": Y, "result": Z,
        "conflicts": [], "soft_green": M.SOFT_ZERO_CONFLICT,
        "base_clears_floor": True,
        "receipt_at_Z": {"sha": Z, "verdict": "VALIDATED", "profile": "full"},
        "landable": True,
    })
    monkeypatch.setattr(M, "hard_green_status", _no_result_hard)
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor"])
    assert M.do_eligible(q) == M.EXIT_REFUSED


def test_mismatched_live_hard_green_sha_cannot_authorize(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    _append_clean(store)
    monkeypatch.setattr(M, "hard_green_status", lambda rev: {
        "sha": "f" * 40, "verdict": "HARD_GREEN", "sources": {}})
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor"])
    assert M.do_eligible(q) == M.EXIT_REFUSED


# --------------------------------------------------------------------------- #
# Fix B: reconcile against the LIVE open-PR population (invisible != nothing)   #
# --------------------------------------------------------------------------- #
def test_reconcile_unaccounted_open_pr_fires(tmp_path, monkeypatch, capsys) -> None:
    """An open pushed PR with NO record is UNACCOUNTED, not silently omitted."""
    store = str(tmp_path / "empty.jsonl")
    other = "e" * 40
    monkeypatch.setattr(M, "open_pushed_prs",
                        lambda repo: [{"number": 9, "headRefOid": other,
                                       "headRefName": "pushed-elsewhere",
                                       "url": "u"}])
    q = M.build_parser().parse_args(
        ["eligible", "--store", store, "--no-recheck-floor", "--json"])
    assert M.do_eligible(q) == M.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["unaccounted"] == 1
    assert out["summary"]["open_pushed_prs"] == 1
    assert out["unaccounted"][0]["result"] == other


def test_reconcile_recorded_landable_is_eligible_not_inert(
        tmp_path, monkeypatch, capsys) -> None:
    """The positive bracket: a recorded landable head that IS an open PR fires as
    ELIGIBLE -- the gate is live, not inert."""
    store = str(tmp_path / "s.jsonl")
    _append_clean(store, source_hard=True, result_hard=False)
    monkeypatch.setattr(M, "open_pushed_prs",
                        lambda repo: [{"number": 3, "headRefOid": Z,
                                       "headRefName": "feat", "url": "u"}])
    monkeypatch.setattr(M, "hard_green_status",
                        lambda rev: _HARD_X if rev == X else {
                            "sha": rev, "verdict": "NO_RESULT"})
    q = M.build_parser().parse_args(
        ["eligible", "--store", store, "--no-recheck-floor", "--json"])
    assert M.do_eligible(q) == M.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["eligible"] == 1
    assert out["eligible"][0]["result"] == Z
    assert out["eligible"][0]["pr_number"] == 3


def test_reconcile_orphaned_head_is_recorded_not_open(
        tmp_path, monkeypatch, capsys) -> None:
    """100%-orphan-rate reality: a head we recorded whose PR moved (re-rebased /
    force-pushed away) is no open PR's head. It must be reported as
    recorded_not_open (superseded), NOT counted as eligible."""
    store = str(tmp_path / "s.jsonl")
    _append_clean(store, source_hard=True, result_hard=False)
    # The live population has a DIFFERENT head (the orphaning re-rebase minted Z2).
    monkeypatch.setattr(M, "open_pushed_prs",
                        lambda repo: [{"number": 5, "headRefOid": Z2,
                                       "headRefName": "feat", "url": "u"}])
    q = M.build_parser().parse_args(
        ["eligible", "--store", store, "--no-recheck-floor", "--json"])
    assert M.do_eligible(q) == M.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert Z in out["recorded_not_open"]           # our stale head, surfaced
    assert out["summary"]["eligible"] == 0         # never landed on a moved head
    assert out["summary"]["unaccounted"] == 1      # the new head Z2 is unaccounted


# --------------------------------------------------------------------------- #
# A2: durable, cross-host soft-green provenance                                 #
# --------------------------------------------------------------------------- #
def test_provenance_body_carries_only_nonderivable() -> None:
    rec = {"source_rev": X, "base": Y, "result": Z, "conflicts": [],
           "soft_green": M.SOFT_ZERO_CONFLICT, "risk_judgement": M.RISK_NA,
           "rationale": "", "resolver": "", "recorded_utc": "t",
           "base_clears_floor": True, "receipt_at_Z": {"sha": Z},
           "source_hard_green": _DURABLE_HARD_X}
    prov = json.loads(M.provenance_body(rec))
    assert prov["soft_green"] == M.SOFT_ZERO_CONFLICT and prov["result"] == Z
    assert prov["source_hard_green"]["sha"] == X
    assert "base_clears_floor" not in prov and "receipt_at_Z" not in prov


def test_publish_provenance_refuses_null_soft_green() -> None:
    rec = {"result": Z, "soft_green": None}
    try:
        M.publish_provenance(rec)
    except M.RebaseError:
        return
    raise AssertionError("null soft-green carries no durable claim; must refuse")


def test_publish_provenance_refuses_machine_local_hard_green() -> None:
    rec = {"source_rev": X, "base": Y, "result": Z, "conflicts": [],
           "soft_green": M.SOFT_ZERO_CONFLICT,
           "source_hard_green": _HARD_X}
    try:
        M.publish_provenance(rec)
    except M.RebaseError as error:
        assert "machine-local" in str(error)
        return
    raise AssertionError("non-dereferenceable hard-green snapshot must not publish")


def test_publish_provenance_content_addressed_immutable_path(monkeypatch) -> None:
    import hashlib
    captured = {}

    def fake_publish(repo, branch, path, body):
        captured.update(repo=repo, branch=branch, path=path, body=body)
        return "cafe" * 10
    monkeypatch.setattr(M.publish_receipt, "publish", fake_publish)
    rec = {"source_rev": X, "base": Y, "result": Z, "conflicts": [],
           "soft_green": M.SOFT_ZERO_CONFLICT, "risk_judgement": M.RISK_NA,
           "rationale": "", "resolver": "", "recorded_utc": "t",
           "source_hard_green": _DURABLE_HARD_X}
    info = M.publish_provenance(rec)
    digest = hashlib.sha256(M.provenance_body(rec)).hexdigest()
    assert captured["path"] == f"rebase-provenance/{Z}/{digest}.json"
    assert captured["branch"] == M.RECEIPT_BRANCH
    assert info["digest"] == digest


def test_fetch_provenance_picks_latest_by_time(monkeypatch) -> None:
    import base64
    old = {"result": Z, "soft_green": M.SOFT_RESOLVER_JUDGED,
           "risk_judgement": M.RISK_VALIDATE, "recorded_utc": "2026-08-04T00:00:00Z"}
    new = {"result": Z, "soft_green": M.SOFT_RESOLVER_JUDGED,
           "risk_judgement": M.RISK_RETAIN, "recorded_utc": "2026-08-04T02:00:00Z"}

    def fake_gh(argv, check=True):
        url = argv[-1]
        if url.endswith(f"rebase-provenance/{Z}?ref={M.RECEIPT_BRANCH}"):
            return _cp(0, json.dumps([
                {"type": "file", "path": f"rebase-provenance/{Z}/a.json"},
                {"type": "file", "path": f"rebase-provenance/{Z}/b.json"}]))
        blob = old if "a.json" in url else new
        return _cp(0, json.dumps(
            {"content": base64.b64encode(json.dumps(blob).encode()).decode()}))
    monkeypatch.setattr(M.publish_receipt, "gh", fake_gh)
    got = M.fetch_provenance(Z)
    assert got["risk_judgement"] == M.RISK_RETAIN   # the later upgrade wins


def test_fetch_provenance_missing_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(M.publish_receipt, "gh", lambda argv, check=True: _cp(1, ""))
    assert M.fetch_provenance(Z) is None


def test_durable_provenance_recovers_unaccounted_cross_host(
        tmp_path, monkeypatch, capsys) -> None:
    """A2 cross-host close: this host's local store is EMPTY, but the head is an
    open PR whose soft-green provenance another host published. With
    --durable-provenance it is recovered and (base floor + receipt re-checked live)
    classified ELIGIBLE -- not left UNACCOUNTED forever."""
    store = str(tmp_path / "empty.jsonl")
    monkeypatch.setattr(M, "open_pushed_prs",
                        lambda repo: [{"number": 11, "headRefOid": Z,
                                       "headRefName": "feat", "url": "u"}])
    monkeypatch.setattr(M, "fetch_provenance", lambda z: {
        "source_rev": X, "base": Y, "result": Z, "conflicts": [],
        "soft_green": M.SOFT_ZERO_CONFLICT, "risk_judgement": M.RISK_NA,
        "rationale": "", "resolver": "", "recorded_utc": "t",
        "source_hard_green": _DURABLE_HARD_X})
    # Live floor and exact-source authority still apply; provenance is not itself
    # an authorization.
    monkeypatch.setattr(M.gate_floors, "load_floors", lambda p: [])
    monkeypatch.setattr(M.gate_floors, "clears_all",
                        lambda floors, co, base: {"ok": True, "unmet": []})
    _stub_live_source(monkeypatch)
    q = M.build_parser().parse_args(
        ["eligible", "--store", store, "--durable-provenance", "--json"])
    assert M.do_eligible(q) == M.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["unaccounted"] == 0       # recovered, not orphaned
    assert out["summary"]["eligible"] == 1
    assert out["eligible"][0]["result"] == Z


def test_durable_provenance_off_leaves_head_unaccounted(
        tmp_path, monkeypatch, capsys) -> None:
    """Without --durable-provenance the same head stays UNACCOUNTED (network stays
    OFF the default hot path); the cross-host recovery is strictly opt-in."""
    store = str(tmp_path / "empty.jsonl")
    monkeypatch.setattr(M, "open_pushed_prs",
                        lambda repo: [{"number": 11, "headRefOid": Z,
                                       "headRefName": "feat", "url": "u"}])
    # fetch_provenance must NOT be called; make it explode if it is.
    monkeypatch.setattr(M, "fetch_provenance",
                        lambda z: (_ for _ in ()).throw(AssertionError("called")))
    q = M.build_parser().parse_args(
        ["eligible", "--store", store, "--no-recheck-floor", "--json"])
    assert M.do_eligible(q) == M.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["unaccounted"] == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
