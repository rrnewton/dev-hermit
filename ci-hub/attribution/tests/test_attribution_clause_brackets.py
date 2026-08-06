#!/usr/bin/env python3
"""Brackets for the VERDICT-CHANGING `attribute()` clauses found unbracketed.

The slice-5 adversarial review mutation-tested `attribute` and
`host_under_pressure` and found 10 surviving clauses. Three of those only
populate the emitted `signals` dict (deleting them degrades the evidence
payload, not the verdict) and are left alone deliberately. These are the ones
that CHANGE THE ATTRIBUTION -- i.e. where a missing test means a red could be
blamed on the wrong owner:

  attribute():            SHAPE_NONZERO branch (whole branch had 1 test ref,
                          vs 13 for SHAPE_HANG), its external_reads and
                          load-shaped arms, the CRASH external_reads arm, and
                          the weak-evidence `clean and not pressure` arm.
  host_under_pressure():  the cpu_pressure_avg10 and mem_avail_ratio
                          thresholds, both of which gate `pressure` and
                          therefore several attribution branches.

Each test pins the VERDICT, not just "not the default", because a fixture that
falls into INDETERMINATE would satisfy a weaker assertion while proving nothing.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import attribution as A  # noqa: E402


def quiet_host() -> A.HostConditions:
    return A.HostConditions(load1=1.0, nproc=316, cpu_pressure_avg10=0.0,
                            concurrent_procs=1, mem_avail_ratio=0.9)


# --- host_under_pressure thresholds -----------------------------------------


def test_cpu_pressure_alone_raises_pressure() -> None:
    """Bracket for the cpu_pressure_avg10 clause: load1 and proc count are
    quiet, so ONLY the PSI threshold can be responsible for the verdict."""
    calm = quiet_host()
    assert A.host_under_pressure(calm) == (False, [])
    stalled = A.HostConditions(load1=1.0, nproc=316, cpu_pressure_avg10=95.0,
                               concurrent_procs=1, mem_avail_ratio=0.9)
    pressure, reasons = A.host_under_pressure(stalled)
    assert pressure is True
    assert reasons, "a pressure verdict must name its reason"


def test_memory_scarcity_alone_raises_pressure() -> None:
    """Bracket for the mem_avail_ratio clause, isolated the same way."""
    scarce = A.HostConditions(load1=1.0, nproc=316, cpu_pressure_avg10=0.0,
                              concurrent_procs=1, mem_avail_ratio=0.01)
    pressure, reasons = A.host_under_pressure(scarce)
    assert pressure is True
    assert reasons


# --- SHAPE_NONZERO: the whole branch was untested ---------------------------


def test_nonzero_exit_with_a_varying_host_read_is_environment() -> None:
    ev = A.Evidence(shape=A.SHAPE_NONZERO, host=quiet_host(), exit_code=1,
                    external_reads=["/proc/cpuinfo"])
    result = A.attribute(ev)
    assert result.verdict == A.ENVIRONMENT
    assert "cpuinfo" in " ".join(result.reasons)


def test_nonzero_exit_clean_at_low_load_under_pressure_is_infrastructure() -> None:
    loaded = A.HostConditions(load1=900.0, nproc=316, cpu_pressure_avg10=95.0,
                              concurrent_procs=400, mem_avail_ratio=0.05)
    ev = A.Evidence(shape=A.SHAPE_NONZERO, host=loaded, exit_code=1,
                    low_load=A.LowLoadControl(runs=10, failures=0))
    assert A.attribute(ev).verdict == A.INFRASTRUCTURE


def test_nonzero_exit_without_any_control_is_not_confidently_blamed() -> None:
    """POSITIVE CONTROL for the branch: with no external read and no low-load
    control there is nothing to attribute to, and an honest "don't know" is the
    required answer -- a confident verdict here would be the real defect."""
    ev = A.Evidence(shape=A.SHAPE_NONZERO, host=quiet_host(), exit_code=1)
    assert A.attribute(ev).verdict == A.INDETERMINATE


# --- CRASH external-read arm -------------------------------------------------


def test_crash_correlated_with_a_varying_host_read_is_environment() -> None:
    ev = A.Evidence(shape=A.SHAPE_CRASH, host=quiet_host(),
                    external_reads=["/sys/devices/system/cpu/online"])
    result = A.attribute(ev)
    assert result.verdict == A.ENVIRONMENT


# --- the weak-evidence load arm ---------------------------------------------


def test_clean_at_low_load_without_recorded_pressure_is_still_load_shaped() -> None:
    """`low_load.clean and not pressure`: the run is clean when quiet but the
    host was not observed loaded at failure time. That is weaker evidence than
    the pressure-confirmed case and must NOT be reported at the same
    confidence -- which is exactly what an untested clause could silently
    change."""
    ev = A.Evidence(shape=A.SHAPE_HANG, host=quiet_host(), timed_out=True,
                    low_load=A.LowLoadControl(runs=10, failures=0))
    result = A.attribute(ev)
    assert result.verdict == A.INFRASTRUCTURE
    assert result.confidence != "high", (
        "unconfirmed pressure must not be reported at the same confidence as "
        "the pressure-confirmed case"
    )


# --- the signals payload -----------------------------------------------------


def test_signals_payload_carries_every_piece_of_evidence_it_was_given() -> None:
    """The last three unbracketed clauses were signal-only: they populate the
    emitted `signals` dict without changing the verdict, so a verdict assertion
    can never hold them. The payload IS part of the diagnostic contract -- an
    attribution whose evidence silently stops being reported is unauditable --
    so it is asserted directly here.
    """
    ev = A.Evidence(
        shape=A.SHAPE_HANG,
        host=quiet_host(),
        timed_out=True,
        divergence=A.Divergence("detlog", first_line="DETLOG value 41 vs 42"),
        low_load=A.LowLoadControl(runs=10, failures=0),
        external_reads=["/proc/cpuinfo"],
    )
    signals = A.attribute(ev).signals
    assert "divergence" in signals, "divergence evidence dropped from the payload"
    assert "low_load" in signals, "low-load control dropped from the payload"
    assert "external_reads" in signals, "external reads dropped from the payload"
    assert signals["external_reads"] == ["/proc/cpuinfo"]


def test_signals_payload_omits_evidence_that_was_not_supplied() -> None:
    """POSITIVE CONTROL: the keys must be conditional, not always present --
    otherwise the assertions above would pass against a dict that reports
    evidence nobody collected."""
    signals = A.attribute(A.Evidence(shape=A.SHAPE_HANG, host=quiet_host(), timed_out=True)).signals
    for key in ("divergence", "low_load", "external_reads"):
        assert key not in signals, f"{key} reported without evidence"
