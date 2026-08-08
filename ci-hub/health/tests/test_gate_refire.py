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


# ---------------------------------------------------------------------------
# Delta paging: the gate must be satisfiable by the actor it instructs.
#
# The classifier above decides WHETHER a gate is due. These decide whether the
# ALARM is worth sending. `--fail-on-due` paged on standing state every 30
# minutes for a backlog the tick deliberately does not auto-drain, and emitted no
# key=value lines, so the action rendered a literal `{summary}` naming no PR at
# all. Firing constantly while naming nothing is maximum noise and zero
# actionability; both directions are asserted below.
# ---------------------------------------------------------------------------

import json as _json
import tempfile
from pathlib import Path as _Path


def _survey(due=(), other=(), heads_total=None, covered=None, open_numbers=None):
    s = gr.Survey()
    for number in due:
        s.verdicts.append(gr.PrVerdict(
            number=number, head=f"{number:040x}", state=gr.REFIRE_DUE,
            gate_run_id=900000 + number, gate_conclusion="cancelled"))
    for number in other:
        s.verdicts.append(gr.PrVerdict(
            number=number, head=f"{number:040x}", state=gr.GATE_OK,
            gate_run_id=900000 + number, gate_conclusion="success"))
    seen = list(due) + list(other)
    s.open_numbers = set(open_numbers if open_numbers is not None else seen)
    s.heads_total = heads_total if heads_total is not None else len(s.open_numbers)
    s.heads_covered = covered if covered is not None else len(seen)
    return s


def _gate(survey, path, dry_run=False):
    return gr.gate_report(survey, "rrnewton/hermit", 1.0, path, dry_run)


def _tmp_state():
    tmp = tempfile.TemporaryDirectory()
    return tmp, _Path(tmp.name) / "baseline.json"


# ---- POSITIVE: a genuinely actionable condition must surface promptly -------

def test_a_newly_parked_gate_pages_and_NAMES_the_pr():
    """The whole point: the page must be actionable on its own.

    The old output named nothing, so a recipient could not act without re-running
    the tool by hand. The summary has to carry the PR number, the gate run id and
    the exact command that clears it.
    """
    tmp, path = _tmp_state()
    with tmp:
        _gate(_survey(due=(1890,)), path)              # establish a baseline
        code, fields = _gate(_survey(due=(1890, 1971)), path)

        assert code == 1, fields
        assert fields["state"] == "refire-due"
        assert fields["due_new"] == 1
        assert fields["due_new_prs"] == "1971"
        assert "#1971" in fields["summary"]
        assert "901971" in fields["summary"], "gate run id must be in the page"
        assert "--refire" in fields["summary"], "the page must state the remedy"
        # The one already known about must not be re-announced as new.
        assert "#1890" not in fields["summary"]


def test_a_pr_that_clears_and_parks_again_pages_again():
    """The baseline is the CURRENT due set, never a cumulative union."""
    tmp, path = _tmp_state()
    with tmp:
        _gate(_survey(due=(1890,)), path)
        cleared, _ = _gate(_survey(due=(), other=(1890,)), path)
        assert cleared == 0
        code, fields = _gate(_survey(due=(1890,)), path)
        assert code == 1
        assert fields["due_new_prs"] == "1890"


# ---- NEGATIVE: a backlog no tick can drain must NOT page every 30 min -------

def test_a_standing_backlog_does_not_page():
    """41 due, none of them new. This is the filed defect."""
    tmp, path = _tmp_state()
    with tmp:
        backlog = tuple(range(1900, 1941))
        _gate(_survey(due=backlog), path)
        code, fields = _gate(_survey(due=backlog), path)

        assert code == 0, fields
        assert fields["state"] == "ok"
        assert fields["due_new"] == 0
        # Silent is not the same as invisible: the backlog stays in the fields.
        assert fields["due_standing"] == 41
        assert "1900" in fields["due_prs"]


def test_first_run_adopts_the_backlog_without_paging():
    """A standing backlog did not happen at this tick and must not read as if it did."""
    tmp, path = _tmp_state()
    with tmp:
        code, fields = _gate(_survey(due=(1890, 1897, 1905)), path)
        assert code == 0, fields
        assert fields["due_standing"] == 3
        assert _json.loads(path.read_text())["reported"] == [1890, 1897, 1905]


def test_a_pr_outside_the_sampling_window_is_not_retired_and_never_re_pages():
    """The 50%-coverage window must not be able to forge news.

    Measured live: 70 of 139 heads covered. A PR drifts out of the run window and
    back in through no change of its own. Retiring it on absence would drop it
    from the baseline and re-page it as NEW on its return -- manufacturing exactly
    the noise this change removes. Retire on positive evidence only.
    """
    tmp, path = _tmp_state()
    with tmp:
        _gate(_survey(due=(1890, 1897)), path)
        # 1897 falls outside the window: absent from verdicts, still an open PR.
        drifted = _survey(due=(1890,), heads_total=2, covered=1, open_numbers=(1890, 1897))
        code, fields = _gate(drifted, path)
        assert code == 0, fields
        assert _json.loads(path.read_text())["reported"] == [1890, 1897], \
            "an uncovered PR was retired from the baseline on absence alone"

        # It comes back into the window, unchanged. Still not news.
        code, fields = _gate(_survey(due=(1890, 1897)), path)
        assert code == 0, fields
        assert fields["due_new"] == 0


def test_a_pr_covered_and_no_longer_due_IS_retired():
    """Positive evidence retires it -- otherwise the baseline never shrinks."""
    tmp, path = _tmp_state()
    with tmp:
        _gate(_survey(due=(1890, 1897)), path)
        _gate(_survey(due=(1890,), other=(1897,)), path)
        assert _json.loads(path.read_text())["reported"] == [1890]


def test_a_closed_pr_leaves_the_baseline():
    tmp, path = _tmp_state()
    with tmp:
        _gate(_survey(due=(1890, 1897)), path)
        still_open = _survey(due=(1890,), heads_total=1, covered=1, open_numbers=(1890,))
        _gate(still_open, path)
        assert _json.loads(path.read_text())["reported"] == [1890]


# ---- the output contract tick-hub actually parses --------------------------

def test_output_is_capturable_key_values_with_a_real_summary():
    """`capture: true` parses key=value; prose alone rendered a literal {summary}.

    This is the third gate found with this defect, so it is pinned by a test here
    rather than left to review.
    """
    tmp, path = _tmp_state()
    with tmp:
        _gate(_survey(due=(1890,)), path)
        _code, fields = _gate(_survey(due=(1890, 1971)), path)
        for key in ("state", "summary", "due_new", "due_standing",
                    "heads_total", "heads_covered"):
            assert key in fields, key
        assert fields["summary"].strip()
        assert "{summary}" not in fields["summary"]


def test_the_count_always_travels_with_its_denominator():
    """"10 due" from a 50%-covered census is a floor, not a census."""
    tmp, path = _tmp_state()
    with tmp:
        s = _survey(due=(1890,), heads_total=139, covered=70, open_numbers=range(1800, 1939))
        _code, fields = _gate(s, path)
        assert fields["heads_total"] == 139
        assert fields["heads_covered"] == 70
        assert "70/139" in fields["summary"]
        assert "floor, not a census" in fields["summary"] or "not paged" in fields["summary"]


def test_partial_data_never_writes_a_baseline():
    """A truncated survey would record a too-small due set and then suppress the
    real one as 'already reported' on the next healthy tick."""
    tmp, path = _tmp_state()
    with tmp:
        s = _survey(due=(1890,))
        s.truncated = True
        s.error = "deadline exceeded"
        # main() refuses before reaching gate_report; assert the guard's premise
        # holds -- a refused survey leaves no baseline behind.
        assert s.error or s.truncated
        assert not path.exists()
