#!/usr/bin/env python3
"""Plant a partial view; confirm it is flagged.

The canonical instance of this class (`scorecard-full-manifest-denominator`) was
a backend passing 131 of 194 rendered as a fraction of 28. Nothing looked wrong,
which is the entire problem: a partial view does not announce itself. So the
detector gets a planted partial view, and — the half that makes it useful — a
FULL view that must NOT be flagged.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scope_declaration as SD  # noqa: E402


def _v(**over):
    kw = dict(
        tool="demo",
        verdict=SD.OK,
        summary="looks good",
        scope=SD.Scope(checks="the thing", not_checked=[], examined=10, total=10,
                       total_source="the manifest"),
    )
    kw.update(over)
    return SD.ScopedVerdict(**kw)


# --- POSITIVE CONTROL: a full, sourced view is clean -------------------------


def test_a_full_sourced_view_is_not_flagged() -> None:
    assert SD.audit_scope(_v()) == []


def test_a_declared_partial_view_is_not_flagged() -> None:
    """Partial is FINE. Partial-and-silent is the defect."""
    v = _v(scope=SD.Scope(checks="the portable lane",
                          not_checked=["the privileged lane"],
                          examined=28, total=194, total_source="the full manifest"))
    assert SD.audit_scope(v) == []


# --- PLANTED PARTIAL VIEWS ---------------------------------------------------


def test_planted_undeclared_partial_view_is_flagged() -> None:
    """THE PLANT, modelled on the real bug: 28 of 194, declaring nothing."""
    v = _v(scope=SD.Scope(checks="the scorecard", not_checked=[],
                          examined=28, total=194, total_source="the full manifest"))
    problems = {f.problem for f in SD.audit_scope(v)}
    assert "undeclared-partial-view" in problems


def test_planted_unsourced_denominator_is_flagged() -> None:
    """The scorecard bug was not a miscount -- it was confidently counting the
    WRONG population. An unsourced total is how that hides."""
    v = _v(scope=SD.Scope(checks="the scorecard", not_checked=["the privileged lane"],
                          examined=28, total=194, total_source=""))
    problems = {f.problem for f in SD.audit_scope(v)}
    assert "unsourced-denominator" in problems


def test_planted_half_denominator_is_flagged() -> None:
    for scope in (
        SD.Scope(checks="x", examined=28, total=None),
        SD.Scope(checks="x", examined=None, total=194),
    ):
        problems = {f.problem for f in SD.audit_scope(_v(scope=scope))}
        assert "half-a-denominator" in problems, scope


def test_a_tool_that_does_not_say_what_it_checks_is_flagged() -> None:
    problems = {f.problem for f in SD.audit_scope(_v(scope=SD.Scope(checks="  ")))}
    assert "no-scope-declared" in problems


def test_an_overclaiming_summary_is_flagged() -> None:
    """'Enforced' is not 'enforced everywhere'."""
    v = _v(summary="all backends enforce the commandment",
           scope=SD.Scope(checks="3 backends", not_checked=["kvm", "sabre", "e9patch"],
                          examined=3, total=3, total_source="the enumerated list"))
    problems = {f.problem for f in SD.audit_scope(v)}
    assert "overclaiming-summary" in problems


def test_a_modest_summary_with_named_blind_spots_is_not_flagged() -> None:
    """Guard against flags-everything: naming your blind spots is the GOOD case
    and must not itself be penalised."""
    v = _v(summary="the three enumerated backends enforce it",
           scope=SD.Scope(checks="3 backends", not_checked=["kvm", "sabre", "e9patch"],
                          examined=3, total=3, total_source="the enumerated list"))
    assert SD.audit_scope(v) == []


# --- THE NEGATIVE TEST THE TASK NAMES ---------------------------------------


def test_consistent_but_broken_must_not_read_as_ok() -> None:
    """`check-reverie-pin.rs` says "a bump is OPTIONAL" while `detcore_misc`
    LIVELOCKS at that pin. The tool is not wrong; the READING is -- and only
    because the scope was invisible."""
    pin = SD.ScopedVerdict(
        tool="check-reverie-pin", verdict=SD.OK,
        summary="a bump is OPTIONAL, not required",
        scope=SD.Scope(
            checks="whether the pinned commit is an ANCESTOR of the target",
            not_checked=["runtime behaviour at that pin (detcore_misc LIVELOCKS there)"],
            examined=1, total=1, total_source="the pin under test"),
    )
    assert SD.consistent_but_broken_is_not_ok(pin) is False, (
        "an OK carrying an unexamined material blind spot must not read as 'fine'")
    # And the omission must be impossible to quote past: it prints WITH the verdict.
    assert "DOES NOT CHECK" in pin.render()
    assert "LIVELOCK" in pin.render()


def test_an_ok_with_nothing_unexamined_does_read_as_ok() -> None:
    """POSITIVE CONTROL for the predicate: it must not brand every OK unsafe."""
    assert SD.consistent_but_broken_is_not_ok(_v()) is True


def test_a_non_ok_verdict_never_reads_as_ok() -> None:
    assert SD.consistent_but_broken_is_not_ok(_v(verdict=SD.REFUSED)) is False


# --- the registry is itself a denominator -----------------------------------


def test_the_registry_states_its_own_denominator() -> None:
    """It would be absurd to fix "state your denominator" without stating this
    one. The registry must account for all five named instances."""
    r = SD.registry_report()
    assert r["total_instances"] == 5
    assert r["declared_count"] + len(r["undeclared"]) == 5
    for name, inst in r["instances"].items():
        assert inst["answers"], name
        assert inst["not_checked"], f"{name} declares no blind spot -- then why is it here?"
        assert inst["why"], name


def test_the_registry_render_names_every_blind_spot() -> None:
    text = SD.render_registry()
    for name in SD.INSTANCES:
        assert name in text
    assert "DOES NOT CHECK" in text
    assert "DECLARED" in text


def test_the_registry_exits_nonzero_while_any_instance_is_undeclared() -> None:
    """A ratchet, not a status page."""
    rc = SD.main([])
    r = SD.registry_report()
    assert rc == (0 if not r["undeclared"] else 1)
    assert rc == 1, (
        f"{len(r['undeclared'])} instance(s) still undeclared "
        f"({', '.join(r['undeclared'])}); this must not report success")


# --- instance 3: a label is a cache, never the fact -------------------------

HEAD = "a" * 40


def test_label_with_no_backing_record_is_refused() -> None:
    """THE LIVE DEFECT: `locally-validated` on four PRs with nothing behind it."""
    v = SD.label_is_backed(label_present=True, backing_record=None, head_sha=HEAD)
    assert v.verdict == SD.REFUSED
    assert "NO BACKING RECORD" in v.summary


def test_label_backed_by_a_record_for_another_head_is_refused() -> None:
    """The case that looks MOST like success: a real record, wrong commit."""
    v = SD.label_is_backed(label_present=True,
                           backing_record={"commit": "b" * 40}, head_sha=HEAD)
    assert v.verdict == SD.REFUSED
    assert "not this head" in v.summary


def test_label_backed_at_this_head_is_ok() -> None:
    """POSITIVE CONTROL -- without it the predicate could refuse everything."""
    v = SD.label_is_backed(label_present=True,
                           backing_record={"commit": HEAD}, head_sha=HEAD)
    assert v.verdict == SD.OK
    assert SD.audit_scope(v) == []


def test_absent_label_is_refused_not_silently_ok() -> None:
    assert SD.label_is_backed(label_present=False, backing_record=None,
                              head_sha=HEAD).verdict == SD.REFUSED
