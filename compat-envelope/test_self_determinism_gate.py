#!/usr/bin/env python3
"""Tests for the standing self-determinism gate.

Both sides are bracketed for every rule: a qualifying case must FIRE (the gate
is not inert) and a violating case must be REFUSED. Counts are asserted, not
just booleans, so a test cannot pass by comparing nothing.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from self_determinism_gate import (  # noqa: E402
    NOT_COMPARABLE,
    PASS,
    UNMEASURED,
    Ledger,
    LedgerError,
)

GATE = Path(__file__).resolve().parent / "self_determinism_gate.py"


def row(guest, backend, dimension, matches, denominator):
    return {
        "guest": guest,
        "backend": backend,
        "dimension": dimension,
        "ordinal_matches": str(matches),
        "denominator": str(denominator),
    }


# Real measured values at Hermit 590fcc9e, so the fixtures are not invented.
MEASURED = [
    row("threaded", "ptrace", "heap", 38, 38),
    row("threaded", "kvm", "heap", 38, 38),
    row("threaded", "ptrace", "stack", 75, 75),
    row("threaded", "kvm", "stack", 38, 59),          # measured self-FAIL
    row("trivial", "ptrace", "heap", 0, 0),           # measured vacuous n=0
    row("trivial", "kvm", "heap", 0, 0),
    row("heap_exercising", "ptrace", "heap", 6, 6),
    row("heap_exercising", "kvm", "heap", 6, 6),
]


class VerdictTests(unittest.TestCase):
    def test_full_agreement_is_pass(self):
        cell = Ledger.from_rows([row("g", "ptrace", "heap", 38, 38)]).lookup("g", "ptrace", "heap")
        self.assertEqual(cell.verdict, PASS)
        self.assertEqual((cell.ordinal_matches, cell.denominator), (38, 38))

    def test_partial_agreement_is_not_comparable(self):
        cell = Ledger.from_rows([row("g", "kvm", "stack", 38, 59)]).lookup("g", "kvm", "stack")
        self.assertEqual(cell.verdict, NOT_COMPARABLE)
        self.assertIn("38/59", cell.reason)

    def test_one_ordinal_short_is_refused(self):
        """The boundary: n-1 of n must NOT round up to PASS."""
        cell = Ledger.from_rows([row("g", "kvm", "stack", 58, 59)]).lookup("g", "kvm", "stack")
        self.assertEqual(cell.verdict, NOT_COMPARABLE)

    def test_zero_denominator_is_not_comparable_not_pass(self):
        """0/0 is vacuous agreement and must never score as PASS."""
        cell = Ledger.from_rows([row("g", "kvm", "heap", 0, 0)]).lookup("g", "kvm", "heap")
        self.assertEqual(cell.verdict, NOT_COMPARABLE)
        self.assertIn("vacuous n=0", cell.reason)


class RunFailureTests(unittest.TestCase):
    """Agreement between two FAILED runs is not self-determinism."""

    def test_failed_runs_that_agree_are_refused(self):
        r = row("fork_exec_pipeline", "kvm", "heap", 518, 518)
        r["status"] = "REFUSED_RUN_FAILURE"
        cell = Ledger.from_rows([r]).lookup("fork_exec_pipeline", "kvm", "heap")
        self.assertEqual(cell.verdict, NOT_COMPARABLE)
        self.assertIn("run failure", cell.reason)

    def test_identical_counts_pass_when_runs_are_clean(self):
        """Positive control: the same 518/518 PASSES with a clean status."""
        r = row("fork_exec_pipeline", "kvm", "heap", 518, 518)
        r["status"] = "PASS"
        cell = Ledger.from_rows([r]).lookup("fork_exec_pipeline", "kvm", "heap")
        self.assertEqual(cell.verdict, PASS)

    def test_unknown_status_fails_closed(self):
        r = row("g", "kvm", "heap", 6, 6)
        r["status"] = "SOMETHING_NEW_NOBODY_HANDLED"
        self.assertEqual(Ledger.from_rows([r]).lookup("g", "kvm", "heap").verdict, NOT_COMPARABLE)

    def test_run_failure_refuses_the_parity_emission(self):
        rows = [row("fp", "ptrace", "heap", 1061, 1061), row("fp", "kvm", "heap", 518, 518)]
        rows[1]["status"] = "REFUSED_RUN_FAILURE"
        d = Ledger.from_rows(rows).parity_decision("fp", "heap", ("ptrace", "kvm"))
        self.assertFalse(d.emittable)
        self.assertIn("run failure", d.render())


class ParityDecisionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger.from_rows(MEASURED)

    def test_both_sides_pass_is_emittable(self):
        """Positive control: the gate must FIRE, not merely never refuse."""
        d = self.ledger.parity_decision("threaded", "heap", ("ptrace", "kvm"))
        self.assertEqual(d.verdict, PASS)
        self.assertTrue(d.emittable)
        self.assertNotIn("NOT-COMPARABLE", d.render())

    def test_one_side_self_failing_refuses(self):
        d = self.ledger.parity_decision("threaded", "stack", ("ptrace", "kvm"))
        self.assertEqual(d.verdict, NOT_COMPARABLE)
        self.assertFalse(d.emittable)
        self.assertIn("NOT-COMPARABLE", d.render())
        self.assertIn("38/59", d.render())

    def test_reason_names_which_side_and_dimension(self):
        """The verdict must carry which side failed, not just that one did."""
        text = self.ledger.parity_decision("threaded", "stack", ("ptrace", "kvm")).render()
        self.assertIn("kvm", text)
        self.assertIn("stack", text)
        self.assertIn("not self-deterministic", text)
        # and it must not blame the healthy side
        self.assertNotIn("ptrace stack is not self-deterministic", text)

    def test_zero_denominator_both_sides_refuses(self):
        d = self.ledger.parity_decision("trivial", "heap", ("ptrace", "kvm"))
        self.assertEqual(d.verdict, NOT_COMPARABLE)
        self.assertIn("vacuous n=0", d.render())

    def test_unmeasured_triple_is_refused_not_permitted(self):
        """The growing-set hole: an absent record must REFUSE."""
        d = self.ledger.parity_decision("threaded", "heap", ("ptrace", "sabre"))
        self.assertEqual(d.verdict, UNMEASURED)
        self.assertFalse(d.emittable)
        self.assertIn("UNMEASURED", d.render())

    def test_unmeasured_guest_is_refused_even_for_passing_backends(self):
        """Self-determinism is per-guest: a PASS elsewhere must not transfer."""
        d = self.ledger.parity_decision("brand_new_guest", "heap", ("ptrace", "kvm"))
        self.assertFalse(d.emittable)
        self.assertEqual(d.verdict, UNMEASURED)

    def test_measured_failure_outranks_unmeasured_in_the_reason(self):
        d = self.ledger.parity_decision("threaded", "stack", ("kvm", "sabre"))
        self.assertEqual(d.verdict, NOT_COMPARABLE)
        self.assertIn("38/59", d.render())

    def test_single_backend_is_not_a_parity_comparison(self):
        with self.assertRaises(ValueError):
            self.ledger.parity_decision("threaded", "heap", ("ptrace",))


class PlantedNondeterminismTests(unittest.TestCase):
    """A planted nondeterminism must flip a previously emittable cell."""

    def test_plant_flips_pass_to_not_comparable(self):
        clean = Ledger.from_rows(MEASURED)
        before = clean.parity_decision("heap_exercising", "heap", ("ptrace", "kvm"))
        self.assertTrue(before.emittable, "fixture must be emittable before the plant")

        planted = [
            row("heap_exercising", "kvm", "heap", 5, 6) if r["guest"] == "heap_exercising"
            and r["backend"] == "kvm" else r
            for r in MEASURED
        ]
        after = Ledger.from_rows(planted).parity_decision(
            "heap_exercising", "heap", ("ptrace", "kvm")
        )
        self.assertFalse(after.emittable)
        self.assertEqual(after.verdict, NOT_COMPARABLE)
        self.assertIn("5/6", after.render())


class MalformedLedgerTests(unittest.TestCase):
    """A ledger that cannot be trusted must fail closed, not silently permit."""

    def test_matches_exceeding_denominator_is_rejected(self):
        with self.assertRaises(LedgerError):
            Ledger.from_rows([row("g", "kvm", "heap", 7, 6)])

    def test_negative_counts_rejected(self):
        with self.assertRaises(LedgerError):
            Ledger.from_rows([row("g", "kvm", "heap", -1, 6)])

    def test_missing_field_rejected(self):
        bad = row("g", "kvm", "heap", 6, 6)
        del bad["denominator"]
        with self.assertRaises(LedgerError):
            Ledger.from_rows([bad])

    def test_non_integer_rejected(self):
        with self.assertRaises(LedgerError):
            Ledger.from_rows([row("g", "kvm", "heap", "many", 6)])

    def test_conflicting_duplicate_rejected(self):
        with self.assertRaises(LedgerError):
            Ledger.from_rows(
                [row("g", "kvm", "heap", 6, 6), row("g", "kvm", "heap", 3, 6)]
            )

    def test_identical_duplicate_allowed(self):
        ledger = Ledger.from_rows([row("g", "kvm", "heap", 6, 6), row("g", "kvm", "heap", 6, 6)])
        self.assertEqual(ledger.coverage()["cells_recorded"], 1)


class CoverageTests(unittest.TestCase):
    def test_coverage_states_its_denominator(self):
        c = Ledger.from_rows(MEASURED).coverage()
        self.assertEqual(c["cells_recorded"], 8)
        self.assertEqual(c["cells_pass"], 5)
        self.assertEqual(c["cells_not_comparable"], 3)
        self.assertEqual(c["guests"], 3)
        self.assertEqual(c["backends"], 2)
        self.assertEqual(c["dimensions"], 2)

    def test_report_names_the_unmeasured_gap(self):
        text = Ledger.from_rows(MEASURED).coverage_report()
        self.assertIn("UNMEASURED (gap)  : 4", text)  # 3*2*2=12 expected, 8 recorded
        self.assertIn("cells recorded    : 8", text)


class CliTests(unittest.TestCase):
    def _ledger_file(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False)
        fh.write("guest\tbackend\tdimension\tordinal_matches\tdenominator\n")
        for r in MEASURED:
            fh.write(
                f"{r['guest']}\t{r['backend']}\t{r['dimension']}\t"
                f"{r['ordinal_matches']}\t{r['denominator']}\n"
            )
        fh.close()
        return fh.name

    def test_cli_exit_0_when_emittable(self):
        p = subprocess.run(
            [sys.executable, str(GATE), "--ledger", self._ledger_file(), "--check",
             "guest=threaded", "dimension=heap", "backends=ptrace,kvm"],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_cli_exit_3_when_refused(self):
        p = subprocess.run(
            [sys.executable, str(GATE), "--ledger", self._ledger_file(), "--check",
             "guest=threaded", "dimension=stack", "backends=ptrace,kvm"],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 3)
        self.assertIn("NOT-COMPARABLE", p.stdout)

    def test_cli_exit_2_on_unusable_ledger(self):
        p = subprocess.run(
            [sys.executable, str(GATE), "--ledger", "/nonexistent/ledger.tsv", "--report"],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
