#!/usr/bin/env python3
"""Tests for the monotonic ratchet set.

THE ONE DISTINCTION UNDER TEST: a drop with a recorded definition change is a
RE-BASELINE; a drop without one is a REGRESSION. Everything else here exists to
stop that distinction being reachable by accident.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ratchet  # noqa: E402


def m(value, *, denom="cells", defn="v1", reason=None, unmeasured_reason=None):
    out = {"value": value, "denominator": denom, "definition_version": defn,
           "definition": "test metric"}
    if reason:
        out["rebaseline_reason"] = reason
    if unmeasured_reason:
        out["unmeasured_reason"] = unmeasured_reason
    return out


def rec(**metrics):
    return {"record_version": 1, "as_of": "2026-08-07", "metrics": metrics}


# ---- the load-bearing distinction ------------------------------------------

def test_drop_without_a_definition_change_is_a_regression():
    """The whole point. A silent drop must be actionable."""
    moves = ratchet.compare(rec(depth=m(8)), rec(depth=m(3)))
    assert [x.verdict for x in moves] == [ratchet.REGRESSION]
    assert moves[0].verdict in ratchet.ACTIONABLE


def test_drop_with_a_recorded_definition_change_is_a_rebaseline_not_a_regression():
    """A tightening legitimately lowers the number and must NOT page anyone.

    If it did, the cheapest way to clear the alert would be to loosen the
    definition back — the exact fake-green move this set exists to prevent.
    """
    moves = ratchet.compare(
        rec(depth=m(8, defn="stdout-only/v1")),
        rec(depth=m(3, defn="stdout+detlog/v2",
                   reason="deepened from stdout-only to detlog comparison")),
    )
    assert moves[0].verdict == ratchet.REBASELINE
    assert moves[0].verdict not in ratchet.ACTIONABLE
    assert "deepened" in moves[0].detail


def test_a_tightening_without_a_reason_is_refused():
    """Recording the version bump but not WHY leaves the next reader unable to
    tell a re-baseline from a regression, so the record is rejected outright."""
    with pytest.raises(ratchet.RatchetError):
        ratchet.compare(rec(depth=m(8, defn="v1")), rec(depth=m(3, defn="v2")))


def test_denominator_change_is_treated_like_a_definition_change():
    """`8 of 85` and `8 of 12` are not the same measurement."""
    with pytest.raises(ratchet.RatchetError):
        ratchet.compare(rec(x=m(8, denom="85 cells")), rec(x=m(3, denom="12 cells")))
    moves = ratchet.compare(
        rec(x=m(8, denom="85 cells")),
        rec(x=m(3, denom="12 cells", reason="scope narrowed to the CI-enabled subset")),
    )
    assert moves[0].verdict == ratchet.REBASELINE


# ---- the three states that must not collapse into "0" ----------------------

def test_unmeasured_never_reads_as_a_drop():
    """value:null is silence. Treating it as 0 manufactures a regression and
    then pressures someone to 'fix' a number that was never taken."""
    moves = ratchet.compare(
        rec(x=m(8)), rec(x=m(None, unmeasured_reason="collector mid-edit")))
    assert moves[0].verdict == ratchet.UNMEASURED
    assert moves[0].verdict not in ratchet.ACTIONABLE
    assert "mid-edit" in moves[0].detail


def test_a_new_metric_is_not_an_improvement_from_zero():
    moves = ratchet.compare(rec(), rec(x=m(5)))
    assert moves[0].verdict == ratchet.NEW


def test_zero_is_a_real_value_and_still_compares():
    """Zero is measured, unlike null: dropping to it IS a regression."""
    moves = ratchet.compare(rec(x=m(4)), rec(x=m(0)))
    assert moves[0].verdict == ratchet.REGRESSION


def test_a_blank_value_without_a_reason_is_refused():
    with pytest.raises(ratchet.RatchetError):
        ratchet.validate_record(rec(x=m(None)))


# ---- ordinary movement -----------------------------------------------------

def test_rise_under_a_fixed_definition_is_the_ratchet_advancing():
    moves = ratchet.compare(rec(x=m(3)), rec(x=m(8)))
    assert moves[0].verdict == ratchet.UP


def test_a_rise_across_a_definition_change_is_not_banked_as_progress():
    """Two numbers measuring different things are not comparable in EITHER
    direction; calling the rise progress would overstate just as badly."""
    moves = ratchet.compare(
        rec(x=m(3, defn="v1")),
        rec(x=m(9, defn="v2", reason="loosened to include relaxed runs")))
    assert moves[0].verdict == ratchet.REBASELINE
    assert "not" in moves[0].detail and "comparable" in moves[0].detail


def test_flat_is_flat():
    assert ratchet.compare(rec(x=m(3)), rec(x=m(3)))[0].verdict == ratchet.FLAT


# ---- the record shipped in this repo ---------------------------------------

def test_shipped_record_is_valid_and_covers_the_whole_set():
    """The five metrics the owner named must each be present and classifiable."""
    data = ratchet.load()
    ratchet.validate_record(data)
    names = set(data["metrics"])
    for needle in ("prefix-parity", "compat-envelope", "green-time",
                   "fixtures-landed-running-and-failable", "zero-ptracer"):
        assert any(needle in n for n in names), f"ratchet set is missing {needle}"


def test_shipped_record_states_a_denominator_for_every_metric():
    for name, metric in ratchet.load()["metrics"].items():
        assert metric["denominator"], f"{name} has no denominator"
        assert metric["definition_version"], f"{name} has no definition_version"


def test_comparing_the_shipped_record_against_itself_is_all_flat():
    data = ratchet.load()
    verdicts = {mv.verdict for mv in ratchet.compare(data, data)}
    assert verdicts <= {ratchet.FLAT, ratchet.UNMEASURED}, verdicts


def test_exit_code_is_nonzero_only_for_a_real_regression(tmp_path, capsys):
    base = tmp_path / "base.json"
    cur = tmp_path / "cur.json"
    base.write_text(json.dumps(rec(x=m(8))))

    cur.write_text(json.dumps(rec(x=m(3, defn="v2", reason="tightened"))))
    assert ratchet.main(["--record", str(cur), "--against", str(base)]) == 0

    cur.write_text(json.dumps(rec(x=m(3))))
    assert ratchet.main(["--record", str(cur), "--against", str(base)]) == 1
