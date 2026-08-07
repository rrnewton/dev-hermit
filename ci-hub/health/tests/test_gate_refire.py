#!/usr/bin/env python3
"""Both-direction tests for the merge-gate refire watchdog.

The asymmetry is the point and is asserted in both directions:

  * a gate parked with EVERY leg complete MUST be reported REFIRE_DUE, and
  * a gate parked with ANY leg still running MUST NOT be, because refiring there
    recomputes the same NO_RESULT, re-dispatches the legs and cancels again -- a
    busy-loop that also starves the runners those legs are queued for.

A watchdog that refired on every cancelled gate would pass the first test and be
strictly worse than doing nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gate_refire as gr  # noqa: E402


def run(name, status="completed", conclusion="success", started="2026-08-07T10:00:00Z", rid=1):
    return {"name": name, "status": status, "conclusion": conclusion,
            "run_started_at": started, "id": rid}


PORTABLE = gr.LEG_WORKFLOWS[0]
PRIV = gr.LEG_WORKFLOWS[1]
DEMO = gr.LEG_WORKFLOWS[2]


# ------------------------------------------------------- the DUE direction

def test_parked_gate_with_all_legs_complete_is_REFIRE_DUE():
    state, gid, concl, legs = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="cancelled", rid=99),
        run(PORTABLE), run(PRIV),
    ])
    assert state == gr.REFIRE_DUE
    assert gid == 99 and concl == "cancelled"
    assert legs == {PORTABLE: "completed/success", PRIV: "completed/success"}


def test_due_even_when_a_completed_leg_FAILED():
    """`completed` is the predicate, not `success`.

    The gate's job is to evaluate a finished leg, including a red one. Waiting for
    a leg that already failed would park the PR forever on a decided outcome.
    """
    state, _, _, _ = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="cancelled"),
        run(PORTABLE, conclusion="failure"), run(PRIV),
    ])
    assert state == gr.REFIRE_DUE


# --------------------------------------------------- the MUST-NOT direction

def test_parked_gate_with_a_queued_leg_is_NOT_due():
    state, _, _, legs = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="cancelled"),
        run(PORTABLE, status="queued", conclusion=None), run(PRIV),
    ])
    assert state == gr.PARKED_WAIT
    assert legs[PORTABLE] == "queued/-"


def test_parked_gate_with_an_in_progress_leg_is_NOT_due():
    state, _, _, _ = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="cancelled"),
        run(PORTABLE), run(PRIV, status="in_progress", conclusion=None),
    ])
    assert state == gr.PARKED_WAIT


def test_parked_gate_with_no_visible_legs_is_NOT_due():
    """Refusing to guess. No legs visible means we cannot show they finished."""
    state, _, _, legs = gr.classify_head([run(gr.GATE_WORKFLOW, conclusion="cancelled")])
    assert state == gr.PARKED_WAIT and legs == {}


# ------------------------------------------------------------- not parked

def test_successful_gate_is_not_touched():
    state, _, concl, _ = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="success"), run(PORTABLE), run(PRIV)])
    assert state == gr.GATE_OK and concl == "success"


def test_failed_gate_is_not_a_refire_candidate():
    """A red gate is a decided answer; refiring it would launder a failure."""
    state, _, _, _ = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="failure"), run(PORTABLE), run(PRIV)])
    assert state == gr.GATE_OK


def test_head_with_no_gate_run_at_all():
    assert gr.classify_head([run(PORTABLE)])[0] == gr.NO_GATE


# --------------------------------- newest run per workflow decides, not order

def test_a_newer_successful_gate_beats_an_older_cancelled_one():
    state, _, _, _ = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="cancelled", started="2026-08-06T21:55:00Z", rid=1),
        run(gr.GATE_WORKFLOW, conclusion="success", started="2026-08-07T14:40:00Z", rid=2),
        run(PORTABLE), run(PRIV),
    ])
    assert state == gr.GATE_OK


def test_an_older_completed_leg_does_not_mask_a_newer_running_one():
    """The real hazard: a stale green leg beside a fresh queued one reads as DUE."""
    state, _, _, _ = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="cancelled", started="2026-08-07T14:00:00Z"),
        run(PORTABLE, started="2026-08-07T09:00:00Z"),
        run(PORTABLE, status="in_progress", conclusion=None, started="2026-08-07T14:30:00Z"),
        run(PRIV),
    ])
    assert state == gr.PARKED_WAIT


def test_the_watchdog_does_not_flag_everything():
    """Mixed population in, exact split out -- the control for a flag-everything bug."""
    heads = {
        "due":  [run(gr.GATE_WORKFLOW, conclusion="cancelled"), run(PORTABLE), run(PRIV)],
        "wait": [run(gr.GATE_WORKFLOW, conclusion="cancelled"), run(PORTABLE),
                 run(PRIV, status="queued", conclusion=None)],
        "ok":   [run(gr.GATE_WORKFLOW, conclusion="success"), run(PORTABLE)],
        "none": [run(PORTABLE)],
    }
    got = {k: gr.classify_head(v)[0] for k, v in heads.items()}
    assert got == {"due": gr.REFIRE_DUE, "wait": gr.PARKED_WAIT,
                   "ok": gr.GATE_OK, "none": gr.NO_GATE}


def test_demo_gate_counts_as_a_leg():
    state, _, _, legs = gr.classify_head([
        run(gr.GATE_WORKFLOW, conclusion="cancelled"),
        run(PORTABLE), run(PRIV), run(DEMO, status="in_progress", conclusion=None)])
    assert state == gr.PARKED_WAIT
    assert DEMO in legs


# ------------------------------------------------------------ refire safety

def test_refire_never_touches_a_parked_wait(monkeypatch):
    calls = []
    monkeypatch.setattr(gr.subprocess, "run",
                        lambda *a, **k: calls.append(a) or type("R", (), {"returncode": 0, "stderr": ""})())
    v_wait = gr.PrVerdict(number=1, head="a", state=gr.PARKED_WAIT, gate_run_id=11)
    v_ok = gr.PrVerdict(number=2, head="b", state=gr.GATE_OK, gate_run_id=22)
    assert gr.do_refire("r/x", [v_wait, v_ok]) == 0
    assert calls == []


def test_refire_fires_exactly_the_due_ones(monkeypatch):
    calls = []

    def fake(cmd, **k):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(gr.subprocess, "run", fake)
    vs = [gr.PrVerdict(number=1, head="a", state=gr.REFIRE_DUE, gate_run_id=11),
          gr.PrVerdict(number=2, head="b", state=gr.PARKED_WAIT, gate_run_id=22)]
    assert gr.do_refire("r/x", vs) == 1
    assert len(calls) == 1 and "11" in calls[0]
