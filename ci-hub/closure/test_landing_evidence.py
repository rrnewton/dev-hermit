#!/usr/bin/env python3
"""tg cannot verify landing — and every other thing that has been mistaken for it.

Each negative here is a source someone actually read as "it landed". The
positive controls matter as much: a predicate that refused everything would pass
every negative and make the gateway unusable.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import landing_evidence as LE  # noqa: E402

OID = "c" * 40


# --- tg is a tracker, not a source of truth on main --------------------------


def test_tg_status_closed_does_not_prove_landing() -> None:
    """THE HEADLINE. `closed` records that a coordinator closed the task; reading
    it back as proof is circular -- the tracker certifying what it was told."""
    v = LE.classify_evidence("tg-status-closed")
    assert v.authority == LE.NON_AUTHORITATIVE
    assert v.proves_landing is False
    assert "TRACKER" in v.reason


def test_the_implemented_tag_does_not_prove_landing() -> None:
    """`implemented` means published-but-NOT-landed; it is the tag that exists
    precisely to keep the two apart."""
    v = LE.classify_evidence("tg-implemented-tag")
    assert v.proves_landing is False
    assert "NOT-landed" in v.reason


def test_a_task_note_does_not_prove_landing() -> None:
    assert LE.classify_evidence("tg-note-claiming-landed").proves_landing is False


# --- everything else that has been mistaken for it ---------------------------


def test_every_catalogued_non_authoritative_source_is_refused() -> None:
    for kind in LE.NON_AUTHORITATIVE_SOURCES:
        v = LE.classify_evidence(kind)
        assert v.authority == LE.NON_AUTHORITATIVE, kind
        assert v.reason.strip(), f"{kind} refuses without saying what it DOES tell you"


def test_the_merged_flag_alone_is_not_landing() -> None:
    v = LE.classify_evidence("pr-merged-flag")
    assert v.proves_landing is False
    assert "ORPHANS" in v.reason


def test_pr_head_ancestry_is_the_wrong_form() -> None:
    v = LE.classify_evidence("pr-head-ancestry")
    assert v.proves_landing is False
    assert "79 unlanded when 46 had landed" in v.reason


# --- what IS authoritative ---------------------------------------------------


def test_merge_commit_ancestry_on_a_fresh_fetch_is_authoritative() -> None:
    """POSITIVE CONTROL -- without it the predicate could refuse everything."""
    v = LE.classify_evidence("merge-commit-ancestry", fetched_fresh=True,
                             merge_commit_oid=OID, is_ancestor=True)
    assert v.authority == LE.AUTHORITATIVE
    assert v.proves_landing is True


def test_a_satisfied_directives_ledger_is_authoritative() -> None:
    """The ledger is a cache WITH a dereference, not a label: it grants
    `satisfied` only on freshly-fetched ancestry."""
    v = LE.classify_evidence("directives-ledger", ledger_satisfied=True)
    assert v.authority == LE.AUTHORITATIVE


# --- the ways the authoritative form still fails -----------------------------


def test_a_stale_fetch_is_unverifiable_not_authoritative() -> None:
    v = LE.classify_evidence("merge-commit-ancestry", fetched_fresh=False,
                             merge_commit_oid=OID, is_ancestor=True)
    assert v.authority == LE.UNVERIFIABLE
    assert v.proves_landing is False


def test_a_non_ancestor_merge_commit_is_refused() -> None:
    v = LE.classify_evidence("merge-commit-ancestry", fetched_fresh=True,
                             merge_commit_oid=OID, is_ancestor=False)
    assert v.authority == LE.NON_AUTHORITATIVE
    assert "orphaned" in v.reason


def test_undetermined_ancestry_is_unverifiable() -> None:
    v = LE.classify_evidence("merge-commit-ancestry", fetched_fresh=True,
                             merge_commit_oid=OID, is_ancestor=None)
    assert v.authority == LE.UNVERIFIABLE


def test_an_unconsulted_ledger_is_unverifiable_not_satisfied() -> None:
    assert LE.classify_evidence("directives-ledger").authority == LE.UNVERIFIABLE


def test_an_unknown_evidence_kind_is_refused_not_allowed() -> None:
    """A source nobody has classified is exactly the one to refuse: the reader
    has no way to know which list it belongs to."""
    v = LE.classify_evidence("some-new-dashboard")
    assert v.authority == LE.UNVERIFIABLE
    assert v.proves_landing is False


# --- non-authoritative sources do not accumulate -----------------------------


def test_many_weak_sources_do_not_sum_to_proof() -> None:
    """THE ARITHMETIC ERROR this whole class is made of: closed + implemented +
    MERGED + a green check still is not landing."""
    weak = [LE.classify_evidence(k) for k in
            ("tg-status-closed", "tg-implemented-tag", "pr-merged-flag", "green-check")]
    agg = LE.require_landing_evidence(weak)
    assert agg.proves_landing is False
    assert "do not accumulate" in agg.reason


def test_one_authoritative_source_among_weak_ones_is_accepted() -> None:
    mixed = [LE.classify_evidence("tg-status-closed"),
             LE.classify_evidence("merge-commit-ancestry", fetched_fresh=True,
                                  merge_commit_oid=OID, is_ancestor=True)]
    assert LE.require_landing_evidence(mixed).proves_landing is True


def test_no_evidence_at_all_is_unverifiable() -> None:
    assert LE.require_landing_evidence([]).authority == LE.UNVERIFIABLE


# --- the CLI is usable without the coordinator restating anything ------------


def test_the_cli_lists_both_sides_and_exits_nonzero_on_a_weak_kind() -> None:
    assert LE.main(["--list"]) == 0
    assert LE.main(["--kind", "tg-status-closed"]) == 1
    assert LE.main(["--kind", "merge-commit-ancestry"]) == 1, (
        "bare kind with no fetch/ancestry data must not self-certify")
