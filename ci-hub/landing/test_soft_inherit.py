#!/usr/bin/env python3
"""Soft-inherit across a clean rebase: evidence beats claim, and debt is queryable.

The task's acceptance list, one test-group each:
  * a clean rebase inherits and is LABELLED SOFT
  * a conflicted rebase does NOT inherit
  * soft-green is DISTINGUISHABLE from full-green everywhere it is read
  * the outstanding soft-green DEBT on main is QUERYABLE (how many, since when)
  * NEGATIVE TEST: a rebase with a resolved conflict is REFUSED inheritance
    EVEN IF THE AGENT CLAIMS IT WAS CLEAN
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import soft_inherit as SI  # noqa: E402

P1, P2, P3 = "pid1", "pid2", "pid3"


# --- a clean rebase inherits, and is labelled SOFT ---------------------------


def test_observed_clean_rebase_inherits_soft() -> None:
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z", observed_conflicts=[]))
    assert v.verdict == SI.INHERIT_SOFT
    assert v.basis == SI.OBSERVED
    assert v.inherits is True


def test_unobserved_but_patch_id_corroborated_rebase_inherits_soft() -> None:
    """The after-the-fact path: nobody watched, but the artefact proves the
    branch's own patches are unchanged."""
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z",
        patch_ids_before=[P1, P2], patch_ids_after=[P2, P1]))   # order-insensitive
    assert v.verdict == SI.INHERIT_SOFT
    assert v.basis == SI.CORROBORATED


def test_inheritance_is_never_labelled_full_green() -> None:
    """Soft is a DISTINCT state. Nothing here may produce a full green."""
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z", observed_conflicts=[]))
    assert v.verdict == SI.INHERIT_SOFT
    assert "soft" in v.verdict
    assert v.verdict != "green" and v.basis in (SI.OBSERVED, SI.CORROBORATED)


# --- a conflicted rebase does NOT inherit ------------------------------------


def test_observed_conflicts_do_not_inherit() -> None:
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z", observed_conflicts=["Cargo.toml"],
        claimed_conflicts=["Cargo.toml"]))
    assert v.verdict == SI.NO_INHERIT
    assert "content changed" in v.reason


def test_changed_patch_ids_do_not_inherit() -> None:
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z",
        patch_ids_before=[P1, P2], patch_ids_after=[P1, P3]))
    assert v.verdict == SI.NO_INHERIT
    assert v.detail["lost"] == [P2] and v.detail["gained"] == [P3]


# --- THE NEGATIVE TEST THE TASK NAMES ---------------------------------------


def test_resolved_conflict_is_refused_even_when_the_agent_claims_clean() -> None:
    """THE LAUNDERING VECTOR. `rebase_wrapper record --conflicts` defaults to
    "none", so an agent that resolved conflicts out of band and then records can
    assert a clean rebase. The artefact must beat the assertion."""
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z",
        claimed_conflicts=[],                       # the agent says: clean
        patch_ids_before=[P1, P2], patch_ids_after=[P1, P3]))   # the artefact says: not
    assert v.verdict == SI.NO_INHERIT
    assert "CONTRADICTED" in v.reason
    assert v.basis == SI.CORROBORATED


def test_observed_conflicts_beat_a_clean_claim_too() -> None:
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z",
        claimed_conflicts=[], observed_conflicts=["src/lib.rs"]))
    assert v.verdict == SI.NO_INHERIT
    assert v.detail.get("claim_contradicted")


def test_a_bare_claim_with_nothing_to_corroborate_it_does_not_inherit() -> None:
    """"I rebased cleanly" with no observation and no patch-ids is worthless."""
    v = SI.classify_rebase(SI.RebaseEvidence(source="x", base="y", result="z",
                                             claimed_conflicts=[]))
    assert v.verdict == SI.NO_INHERIT
    assert v.basis == SI.CLAIMED
    assert "laundered" in v.reason


def test_reported_conflicts_are_believed_even_if_patch_ids_match() -> None:
    """A resolution can be patch-id-preserving (taking one side wholesale). The
    conservative answer is the one that does NOT inherit."""
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z", claimed_conflicts=["a.rs"],
        patch_ids_before=[P1], patch_ids_after=[P1]))
    assert v.verdict == SI.NO_INHERIT


def test_observation_and_artefact_disagreeing_is_REFUSED_not_guessed() -> None:
    """Observed clean but patch-ids moved. Picking either would be inventing a
    fact; the honest answer is to refuse."""
    v = SI.classify_rebase(SI.RebaseEvidence(
        source="x", base="y", result="z", observed_conflicts=[],
        patch_ids_before=[P1], patch_ids_after=[P2]))
    assert v.verdict == SI.REFUSED
    assert v.inherits is False


# --- the debt on main is queryable -------------------------------------------


def _soft(c, ts):
    return SI.SoftCommit(commit=c, landed_at=ts, inherited_from="old" + c, basis=SI.OBSERVED)


def test_outstanding_debt_is_counted_and_dated() -> None:
    rep = SI.debt_report(
        [_soft("a" * 40, "2026-08-04T10:00:00Z"), _soft("b" * 40, "2026-08-05T10:00:00Z")],
        full_greens_at=set())
    assert rep["total_soft_on_main"] == 2
    assert rep["outstanding"] == 2
    assert rep["oldest_outstanding"] == "2026-08-04T10:00:00Z", "'since when' must be answerable"


def test_a_full_green_at_the_commit_UPGRADES_it() -> None:
    c = "a" * 40
    rep = SI.debt_report([_soft(c, "2026-08-04T10:00:00Z")], full_greens_at={c})
    assert rep["upgraded"] == 1 and rep["outstanding"] == 0


def test_a_later_full_green_on_main_REDEEMS_it() -> None:
    rep = SI.debt_report(
        [_soft("a" * 40, "2026-08-04T10:00:00Z")], full_greens_at=set(),
        later_full_green_on_main=("c" * 40, "2026-08-05T00:00:00Z"))
    assert rep["redeemed"] == 1 and rep["outstanding"] == 0


def test_an_EARLIER_full_green_does_not_redeem() -> None:
    """POSITIVE CONTROL for the ordering: redemption requires a green AFTER the
    soft commit landed; an earlier one says nothing about it."""
    rep = SI.debt_report(
        [_soft("a" * 40, "2026-08-05T10:00:00Z")], full_greens_at=set(),
        later_full_green_on_main=("c" * 40, "2026-08-04T00:00:00Z"))
    assert rep["outstanding"] == 1 and rep["redeemed"] == 0


def test_no_soft_commits_is_zero_debt_not_an_error() -> None:
    rep = SI.debt_report([], full_greens_at=set())
    assert rep == {**rep, "total_soft_on_main": 0, "outstanding": 0,
                   "oldest_outstanding": None}


def test_the_rendered_debt_names_every_commit_and_its_state() -> None:
    """Soft-green must be DISTINGUISHABLE wherever it is read."""
    c = "a" * 40
    text = SI.render_debt(SI.debt_report([_soft(c, "2026-08-04T10:00:00Z")],
                                         full_greens_at=set()))
    assert c[:12] in text
    assert "OUTSTANDING" in text
    assert "soft-inherited from" in text
    assert "a debt, not a state to settle into" in text
