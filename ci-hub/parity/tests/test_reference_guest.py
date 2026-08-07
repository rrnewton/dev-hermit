#!/usr/bin/env python3
"""Brackets for the reference-guest emission gate.

Both directions on every clause. A gate that only refuses is as useless as one
that only permits, so each refusal below is paired with a case proving the same
clause can emit.

The /bin/true numbers are MEASURED, not invented: 0 heap records, 31 stack
hashes over 1 static [stack] range, 0/31 cross-backend agreement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reference_guest import (  # noqa: E402
    Cell, RefusedError, emit, load_manifest, reference_for, source_sha256,
    stack_growth_ranges,
)

GUESTS = Path(__file__).resolve().parents[1] / "guests"
HEAP = GUESTS / "heap_fragment_reuse.c"
STACK = GUESTS / "stack_deep_recursion.c"
STDOUT = GUESTS / "stdout_bytes.c"
DETLOG = GUESTS / "detlog_syscalls.c"

HEAP_OUT = "heap-sum=6358\n"
STACK_OUT = "stack-depth=4096 stack-sum=4384982038416015348\n"
STDOUT_OUT = "line 000 ...\nstdout-lines=257 stdout-final=16d173bdfcdae583\n"
DETLOG_OUT = "detlog-iters=32 detlog-ops=128\n"

# A [stack] mapping that GREW: the two ranges measured from the real stack guest.
GREW = (
    "INFO detcore: DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 X 0 0:0 0 [stack]->aa\n"
    "INFO detcore: DETLOG [memory][dtid 3] 0x7fffffdf6000-0x7ffffffff000 X 0 0:0 0 [stack]->bb\n"
)
# /bin/true's real shape: many hashes, ONE range, never grew.
STATIC = "".join(
    f"INFO detcore: DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 X 0 0:0 0 [stack]->{i:02x}\n"
    for i in range(31)
)


# ------------------------------- manifest ---------------------------------- #

def test_every_dimension_is_pinned_and_split_by_comparability():
    """Six declared: four comparable, two explicitly NOT-COMPARABLE.

    The split is the point. rdrand/rdseed are DECLARED rather than left absent
    so their refusal carries a reason -- an absent dimension refuses with
    "unknown dimension", which reads as "nobody got to it yet" and is exactly
    the blank this marking exists to replace.
    """
    dims = load_manifest()["dimensions"]
    assert set(dims) == {"stdout", "detlog", "stack", "heap", "rdrand", "rdseed"}
    comparable = {d for d, v in dims.items() if not v.get("not_comparable")}
    marked = {d for d, v in dims.items() if v.get("not_comparable")}
    assert comparable == {"stdout", "detlog", "stack", "heap"}
    assert marked == {"rdrand", "rdseed"}


def test_not_comparable_axes_are_refused_with_the_literal_marking():
    """The marking must be the literal string, matching self_determinism_gate.py."""
    for dim in ("rdrand", "rdseed"):
        with pytest.raises(RefusedError, match="NOT-COMPARABLE"):
            emit(dim, "ptrace", 1, guest_source=GUESTS / "entropy_instructions.c",
                 guest_stdout="cpuid1_ecx=90b82201\n")


def test_not_comparable_is_refused_for_every_backend_not_just_ptrace():
    """The AXIS is uncomparable because the REFERENCE is uncontrolled.

    So a backend that determinizes RDRAND correctly must also be refused -- it
    would otherwise score as diverging from an uncontrolled reference, which
    would penalise the fix.
    """
    for backend in ("ptrace", "kvm", "dbi", "sabre", "liteinst", "e9patch"):
        with pytest.raises(RefusedError, match="NOT-COMPARABLE"):
            emit("rdrand", backend, 1, guest_source=GUESTS / "entropy_instructions.c",
                 guest_stdout="cpuid1_ecx=90b82201\n")


def test_the_marking_carries_its_reason_and_evidence():
    """A marking without its reason is a blank with extra steps."""
    try:
        emit("rdrand", "ptrace", 1, guest_source=GUESTS / "entropy_instructions.c",
             guest_stdout="cpuid1_ecx=90b82201\n")
        raise AssertionError("should have refused")
    except RefusedError as e:
        text = str(e)
        assert "NO-REFERENCE" in text
        assert "6/6 distinct" in text          # the evidence
        assert "CR4.TSD" in text               # the mechanism it contrasts against
        assert "penalise the fix" in text      # the consequence


def test_pinned_sha_matches_the_checked_in_source():
    """The manifest must describe the files actually in the tree."""
    for dim, src in (("heap", HEAP), ("stack", STACK),
                     ("stdout", STDOUT), ("detlog", DETLOG)):
        assert reference_for(dim)["source_sha256"] == source_sha256(src), dim


def test_the_recovered_heap_guest_is_byte_exact():
    """Provenance: this is the guest from the first real heap-parity run."""
    assert source_sha256(HEAP) == (
        "4760311f36d52ef98c8dc43efdad475756d5f3c79205a4ce41a741fcedefec29"
    )


# --------------------------- NEGATIVE: refusals ---------------------------- #

def test_bin_true_is_refused_for_heap():
    """The measured 0/0: an ambiguous zero that must never render green."""
    with pytest.raises(RefusedError, match="reference guest"):
        emit("heap", "ptrace", 0, guest_source=STDOUT, guest_stdout=HEAP_OUT)


def test_bin_true_is_refused_for_stack_despite_31_populated_hashes():
    """THE headline case. Populated output is not a valid control."""
    with pytest.raises(RefusedError, match="reference guest"):
        emit("stack", "ptrace", 31, guest_source=STDOUT,
             guest_stdout=STACK_OUT, detlog_text=STATIC)


def test_right_guest_but_static_stack_is_refused():
    """Identity alone is not enough: the mapping must actually have grown."""
    with pytest.raises(RefusedError, match="never grew"):
        emit("stack", "ptrace", 31, guest_source=STACK,
             guest_stdout=STACK_OUT, detlog_text=STATIC)


def test_right_guest_without_its_witness_is_refused():
    """The binary ran but its code did not: not a cell."""
    with pytest.raises(RefusedError, match="witness"):
        emit("heap", "ptrace", 24, guest_source=HEAP, guest_stdout="")


def test_unknown_dimension_is_refused():
    with pytest.raises(RefusedError, match="unknown dimension"):
        emit("bitwise", "ptrace", 1, guest_source=HEAP, guest_stdout=HEAP_OUT)


def test_each_dimension_refuses_the_other_three_guests():
    """No guest may stand in for a dimension it does not exercise."""
    ok = {"heap": HEAP, "stack": STACK, "stdout": STDOUT, "detlog": DETLOG}
    out = {"heap": HEAP_OUT, "stack": STACK_OUT,
           "stdout": STDOUT_OUT, "detlog": DETLOG_OUT}
    for dim in ok:
        for other, src in ok.items():
            if other == dim:
                continue
            with pytest.raises(RefusedError):
                emit(dim, "ptrace", 1, guest_source=src,
                     guest_stdout=out[dim], detlog_text=GREW)


# --------------------------- POSITIVE: it emits ---------------------------- #

def test_each_reference_guest_emits_for_its_own_dimension():
    """Not inert: every pinned pairing must be able to produce a cell."""
    cases = [
        ("heap", HEAP, HEAP_OUT, ""),
        ("stack", STACK, STACK_OUT, GREW),
        ("stdout", STDOUT, STDOUT_OUT, ""),
        ("detlog", DETLOG, DETLOG_OUT, ""),
    ]
    for dim, src, out, log in cases:
        cell = emit(dim, "ptrace", 1, guest_source=src,
                    guest_stdout=out, detlog_text=log)
        assert isinstance(cell, Cell) and cell.dimension == dim


def test_every_emitted_cell_records_its_guest():
    """The core requirement: the guest travels with the value."""
    cell = emit("heap", "ptrace", 24, guest_source=HEAP, guest_stdout=HEAP_OUT)
    row = cell.as_row()
    assert row["guest"].endswith("heap_fragment_reuse.c")
    assert row["guest_sha256"].startswith("4760311f")
    assert row["witness"] == "heap-sum=6358"
    assert row["value"] == 24


def test_stack_growth_detector_is_not_inert():
    """Bracket the structural clause itself, both ways."""
    assert len({b - a for a, b in stack_growth_ranges(GREW)}) == 2
    assert len({b - a for a, b in stack_growth_ranges(STATIC)}) == 1
    assert stack_growth_ranges("") == set()
