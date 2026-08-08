#!/usr/bin/env python3
"""A passing e9patch cell that rewrote nothing must be refused.

Fixtures are REAL banner text captured from this box on 2026-08-06.
"""
from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import e9patch_reach as E  # noqa: E402

VACUOUS = (":: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=0; "
           "mapped_sites=0; b0_sites=0; instruction_map_cache=Hit; "
           "rewrite_cache=not-applicable; artifact_sha256=none; preprocess_us=1274")
REAL = (":: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=57; "
        "mapped_sites=57; b0_sites=0; instruction_map_cache=Hit")


def test_zero_mapped_sites_is_vacuous() -> None:
    v = E.classify_run(VACUOUS)
    assert v.state == E.VACUOUS
    assert v.is_e9patch_result is False
    assert v.mapped_sites == 0
    assert "compares ptrace with itself" in v.reason


def test_rewritten_sites_is_a_real_e9patch_run() -> None:
    """POSITIVE CONTROL -- captured from `hermit --version` as the guest."""
    v = E.classify_run(REAL)
    assert v.state == E.REAL and v.is_e9patch_result is True
    assert v.mapped_sites == 57


def test_no_banner_is_unknown_not_pass() -> None:
    v = E.classify_run("hello\n")
    assert v.state == E.UNKNOWN and v.is_e9patch_result is False


def test_a_passing_parity_cell_with_zero_reach_is_REFUSED() -> None:
    """THE PLANT: the exact shape a corpus sweep would produce today."""
    v = E.qualify_parity_cell(backend="e9patch", parity_pass=True, run_output=VACUOUS)
    assert v.is_e9patch_result is False
    assert "PARITY PASS REFUSED" in v.reason


def test_a_passing_parity_cell_with_real_reach_is_accepted() -> None:
    v = E.qualify_parity_cell(backend="e9patch", parity_pass=True, run_output=REAL)
    assert v.is_e9patch_result is True


def test_other_backends_are_untouched() -> None:
    """Guard against flags-everything: this gate is e9patch-specific."""
    for b in ("ptrace", "kvm", "dbi", "sabre"):
        assert E.qualify_parity_cell(backend=b, parity_pass=True,
                                     run_output="").is_e9patch_result is True


def test_banner_parsing_is_exact() -> None:
    assert E.parse_banner(VACUOUS) == (0, 0)
    assert E.parse_banner(REAL) == (57, 57)
    assert E.parse_banner("no banner here") is None
