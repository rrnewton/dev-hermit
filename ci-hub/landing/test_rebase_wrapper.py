#!/usr/bin/env python3
"""Rebase-wrapper: soft-green is a confidence LEVEL; an absent judgement is REFUSED.

Verifies BOTH directions the owner named: a zero-conflict rebase is soft-greened
mechanically; a conflicted rebase WITHOUT a risk judgement is refused, never
defaulted to green. Also that the base's floor status is carried (a clean rebase
onto a sub-floor base is NOT landable) and that the lander's `eligible` query
answers both directions.
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
def _args(argv, store, monkeypatch, clears=True, unmet=None):
    """Parse argv, point the store at tmp, and stub the floor check + clock."""
    args = M.build_parser().parse_args(argv + ["--store", store, "--no-fetch"]
                                       if "record" == argv[0] else argv + ["--store", store])
    monkeypatch.setattr(M, "base_floor_status",
                        lambda *a, **k: {"ok": clears, "unmet": unmet or []})
    monkeypatch.setattr(M, "utc_now", lambda: "2026-08-04T00:00:00Z")
    return args


def test_do_record_zero_conflict_then_eligible_query(tmp_path, monkeypatch) -> None:
    store = str(tmp_path / "s.jsonl")
    rc = M.do_record(_args(
        ["record", "--source", X, "--base", Y, "--result", Z,
         "--conflicts", "none"], store, monkeypatch))
    assert rc == M.EXIT_OK
    rec = M.load_records(store)[-1]
    assert rec["soft_green"] == M.SOFT_ZERO_CONFLICT and rec["landable"] is True

    # Lander QUERIES: this exact head is eligible -> exit 0.
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor"])
    assert M.do_eligible(q) == M.EXIT_OK


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
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor"])
    assert M.do_eligible(q) == M.EXIT_REFUSED


def test_eligible_unknown_head_is_refused(tmp_path) -> None:
    store = str(tmp_path / "empty.jsonl")
    q = M.build_parser().parse_args(
        ["eligible", "--result", Z, "--store", store, "--no-recheck-floor"])
    try:
        M.do_eligible(q)
    except M.Refused:
        return
    raise AssertionError("querying an unrecorded head must be REFUSED")


def test_eligible_recheck_floor_demotes_stale_record(tmp_path, monkeypatch) -> None:
    """A floor added AFTER recording must demote a previously-landable head."""
    store = str(tmp_path / "s.jsonl")
    M.do_record(_args(
        ["record", "--source", X, "--base", Y, "--result", Z, "--conflicts",
         "none"], store, monkeypatch, clears=True))
    assert M.load_records(store)[-1]["landable"] is True
    # Now a new floor is live; the base no longer clears it.
    monkeypatch.setattr(M.gate_floors, "load_floors", lambda p: [{"sha": "f" * 40}])
    monkeypatch.setattr(M.gate_floors, "clears_all",
                        lambda floors, co, base: {
                            "ok": False,
                            "unmet": [{"sha": "f" * 40, "kind": "merge-gate",
                                       "field": "v3"}]})
    q = M.build_parser().parse_args(["eligible", "--result", Z, "--store", store])
    assert M.do_eligible(q) == M.EXIT_REFUSED


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
