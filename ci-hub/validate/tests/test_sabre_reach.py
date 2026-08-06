#!/usr/bin/env python3
"""Both regimes must be distinguishable, and neither may claim full parity.

Bracketed both ways: a fallback cell is refused, a real cell is NOT refused
(so this is not a gate that says "no" to everything), and the counter is read
from real captured output rather than a hand-written string.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sabre_reach as SR  # noqa: E402

# Verbatim from a real run on this box, 2026-08-06, hermit release binary:
#   ./target/release/hermit --log=info run --backend sabre -- /bin/echo hi
REAL_FALLBACK_OUTPUT = (
    "2026-08-06T12:14:08.143897Z  INFO hermit::sabre: launching Detcore guest "
    "through SaBRe with coordinator RPC guest=/bin/echo "
    "plugin=/home/newton/work/dev-hermit/hermit/target/install_pkg/rsrcs/"
    "libdetcore_sabre.so socket=/tmp/hermit-sabre-rpc-U548ZF/coordinator.sock\n"
    "2026-08-06T12:14:08.496193Z  INFO hermit::sabre::fallback: SaBRe ptrace "
    "fallback completed patched_sites=0\n"
)
PATCHED_OUTPUT = REAL_FALLBACK_OUTPUT.replace("patched_sites=0", "patched_sites=7")


# --- regime A: patched nothing -> refused -------------------------------------


def test_real_captured_fallback_output_is_classified_vacuous() -> None:
    v = SR.classify_run(REAL_FALLBACK_OUTPUT)
    assert v.state == SR.FALLBACK
    assert v.patched_sites == 0
    assert v.fallback_announced is True


def test_a_passing_fallback_cell_is_REFUSED_not_recorded() -> None:
    """THE POINT: regime A looks excellent (~86-91% of ptrace's syscalls)
    precisely because SaBRe did nothing."""
    v = SR.qualify_parity_cell(
        backend="sabre", parity_pass=True, run_output=REAL_FALLBACK_OUTPUT)
    assert not v.is_sabre_result
    assert v.reason.startswith("PARITY PASS REFUSED")


# --- regime B: patched something -> accepted as a SaBRe result ----------------


def test_a_patched_run_is_accepted_so_this_is_not_a_reject_everything_gate() -> None:
    v = SR.qualify_parity_cell(
        backend="sabre", parity_pass=True, run_output=PATCHED_OUTPUT)
    assert v.is_sabre_result
    assert v.patched_sites == 7


def test_a_patched_run_still_cannot_claim_FULL_parity() -> None:
    """The task's negative condition: main-ELF-only reach is not full parity."""
    allowed, reason = SR.full_parity_claim_allowed(PATCHED_OUTPUT)
    assert allowed is False
    assert "main ELF only" in reason and "ld.so" in reason


# --- an absent counter is unknown, never a pass -------------------------------


def test_missing_counter_is_UNKNOWN_not_a_pass() -> None:
    v = SR.classify_run("some log with no counter at all")
    assert v.state == SR.UNKNOWN
    assert v.patched_sites is None
    assert not v.is_sabre_result


def test_counter_is_read_without_depending_on_the_sentence_around_it() -> None:
    """A reworded log line must not silently degrade to UNKNOWN."""
    v = SR.classify_run("sabre: reworded summary, patched_sites=3, done")
    assert v.state == SR.REAL and v.patched_sites == 3
    assert v.fallback_announced is False


def test_last_counter_wins_when_several_are_emitted() -> None:
    v = SR.classify_run("patched_sites=5\nlater summary patched_sites=0\n")
    assert v.patched_sites == 0 and v.state == SR.FALLBACK


# --- other backends are untouched ---------------------------------------------


def test_gate_does_not_apply_to_other_backends() -> None:
    for backend in ("ptrace", "dbi", "e9patch", "kvm"):
        v = SR.qualify_parity_cell(
            backend=backend, parity_pass=True, run_output="no counter here")
        assert v.is_sabre_result, f"{backend} must not be gated by the sabre reach check"


# --- the CLI exit code carries the verdict ------------------------------------


def test_cli_exits_nonzero_on_a_fallback_capture(tmp_path) -> None:
    p = tmp_path / "run.txt"
    p.write_text(REAL_FALLBACK_OUTPUT)
    assert SR.main(["--run-output", str(p)]) == 1


def test_cli_exits_zero_on_a_patched_capture(tmp_path) -> None:
    p = tmp_path / "run.txt"
    p.write_text(PATCHED_OUTPUT)
    assert SR.main(["--run-output", str(p)]) == 0
