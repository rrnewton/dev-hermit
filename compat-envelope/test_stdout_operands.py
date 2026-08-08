#!/usr/bin/env python3
"""Both directions for the stdout-operand check, with counts.

The property under test is that a parity verdict is RE-DERIVABLE from its own row,
and that the three states stay distinct. A test suite that only exercised the
happy path would leave `UNMEASURED` and `DIFFERED` collapsible, which is the whole
defect.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("so", HERE / "stdout_operands.py")
so = importlib.util.module_from_spec(_spec)
sys.modules["so"] = so
_spec.loader.exec_module(so)

HEADER = "test_id,backend,stdout_parity,output_hash,ref_output_hash"
A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64


def row(name, parity="", out="", ref=""):
    return f"{name},ptrace,{parity},{out},{ref}"


def run(rows, header=HEADER):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "probe-scorecard.csv").write_text("\n".join((header, *rows)) + "\n")
    return so.check(tmp)


class ThreeStatesStayDistinct(unittest.TestCase):
    def test_both_operands_equal_is_HELD(self):
        report = run([row("g", out=A, ref=A)])
        self.assertEqual(report.states[so.HELD], 1)
        self.assertEqual(report.states[so.DIFFERED], 0)
        self.assertEqual(report.states[so.UNMEASURED], 0)

    def test_both_operands_unequal_is_DIFFERED_not_unmeasured(self):
        report = run([row("g", out=A, ref=B)])
        self.assertEqual(report.states[so.DIFFERED], 1)
        self.assertEqual(report.states[so.UNMEASURED], 0)

    def test_a_missing_operand_is_UNMEASURED_not_a_zero(self):
        """The distinction the whole module exists for: absent measurement is not
        a failed measurement, and neither is a pass."""
        report = run([row("candidate-only", out=A), row("reference-only", ref=A),
                      row("neither")])
        self.assertEqual(report.states[so.UNMEASURED], 3)
        self.assertEqual(report.states[so.DIFFERED], 0)
        self.assertEqual(report.states[so.HELD], 0)

    def test_the_missing_operand_is_NAMED_because_that_is_the_actionable_half(self):
        report = run([row("a", out=A), row("b")])
        self.assertEqual(report.missing["no ref_output_hash"], 1)
        self.assertEqual(report.missing["no output_hash and no ref_output_hash"], 1)

    def test_classify_ignores_the_recorded_boolean(self):
        """It must answer from the EVIDENCE, or it is the label validating itself."""
        self.assertEqual(so.classify({"output_hash": A, "ref_output_hash": B,
                                      "stdout_parity": "1"})[0], so.DIFFERED)
        self.assertEqual(so.classify({"output_hash": A, "ref_output_hash": A,
                                      "stdout_parity": "0"})[0], so.HELD)

    def test_blank_spellings_do_not_read_as_operands(self):
        for blank in ("", "  ", "-", "n/a", "none", "NULL"):
            state, _ = so.classify({"output_hash": A, "ref_output_hash": blank})
            self.assertEqual(state, so.UNMEASURED, blank)


class UnsupportedAssertionsAreRefused(unittest.TestCase):
    """The backfill guard. Writing a boolean into an empty column would turn a
    visible gap into an invisible false record; these are the refusals that stop it."""

    def test_asserting_parity_with_no_operands_is_refused(self):
        for parity in ("0", "1"):
            report = run([row("g", parity=parity)])
            self.assertEqual(len(report.violations), 1, parity)
            self.assertIn("cannot be re-derived", report.violations[0].reason)

    def test_asserting_held_while_the_operands_differ_is_refused(self):
        report = run([row("g", parity="1", out=A, ref=B)])
        self.assertEqual(len(report.violations), 1)
        self.assertIn("operands say DIFFERED", report.violations[0].reason)

    def test_asserting_differed_while_the_operands_match_is_refused(self):
        report = run([row("g", parity="0", out=A, ref=A)])
        self.assertEqual(len(report.violations), 1)
        self.assertIn("operands say HELD", report.violations[0].reason)

    def test_an_honest_blank_is_NOT_a_violation(self):
        """UNMEASURED with no assertion is the current state of 2290 rows. It is
        counted, reported, and explicitly not an error -- otherwise this gate could
        never be wired, and an unwireable gate is the defect one level up."""
        report = run([row("g"), row("h", out=A)])
        self.assertEqual(report.states[so.UNMEASURED], 2)
        self.assertEqual(report.violations, [])

    def test_a_correct_assertion_passes(self):
        report = run([row("held", parity="1", out=A, ref=A),
                      row("differed", parity="0", out=A, ref=B)])
        self.assertEqual(report.violations, [])
        self.assertEqual(report.states[so.HELD], 1)
        self.assertEqual(report.states[so.DIFFERED], 1)

    def test_the_legacy_parity_spelling_is_classified_not_skipped(self):
        report = run(["g,ptrace,1,,"], header="test_id,backend,parity,output_hash,ref_output_hash")
        self.assertEqual(len(report.violations), 1)

    def test_exit_codes(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "probe-scorecard.csv").write_text(HEADER + "\n" + row("g") + "\n")
        self.assertEqual(so.main(["--root", str(tmp)]), 0)
        (tmp / "probe-scorecard.csv").write_text(
            HEADER + "\n" + row("g", parity="1") + "\n")
        self.assertEqual(so.main(["--root", str(tmp)]), 1)
        self.assertEqual(so.main(["--root", str(Path(tempfile.mkdtemp()))]), 2)


class ShippedData(unittest.TestCase):
    def test_the_published_scorecards_assert_no_parity_they_cannot_support(self):
        """Green today, and this is what keeps it green: it fails the moment
        somebody fills one of the 2290 blanks with a boolean instead of a hash."""
        report = so.check(HERE)
        self.assertEqual(report.violations, [],
                         "a published row asserts a parity its operands cannot support")


class SurvivesIntoValidateEnvelope(unittest.TestCase):
    """The distinction must live in the GATE, not only in this module.

    A checker with no caller is not a check -- the defect that produced
    `tier_evidence.py`, which sat with 18 passing tests and zero call sites. So
    this does not merely assert that a call site exists: it lifts the exact
    command line out of validate-envelope.sh and RUNS it, so the thing proven is
    the invocation the gate performs rather than a paraphrase of it.
    """

    SCRIPT = HERE / "validate-envelope.sh"

    def _invocation(self) -> list[str]:
        text = self.SCRIPT.read_text(encoding="utf-8")
        needle = 'python3 "${here}/stdout_operands.py" --root "${here}"'
        self.assertIn(needle, text,
                      "validate-envelope.sh no longer invokes stdout_operands.py -- "
                      "the gate has regressed to zero callers")
        return needle

    def test_the_gate_invokes_this_checker(self):
        self._invocation()

    def test_the_gates_own_command_refuses_a_planted_unsupported_assertion(self):
        """NEGATIVE, through the gate's command: plant the violating row, confirm
        the exit code the script branches on is nonzero."""
        import subprocess
        needle = self._invocation()
        tmp = Path(tempfile.mkdtemp())
        (tmp / "probe-scorecard.csv").write_text(
            HEADER + "\n" + row("planted", parity="1") + "\n")
        command = needle.replace("${here}/stdout_operands.py", str(HERE / "stdout_operands.py")) \
                        .replace('"${here}"', str(tmp))
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot be re-derived", result.stderr)

    def test_the_gates_own_command_passes_an_honest_blank(self):
        """POSITIVE, same command: an unmeasured row is reported, not failed --
        otherwise the gate could not be wired against the current 2290."""
        import subprocess
        needle = self._invocation()
        tmp = Path(tempfile.mkdtemp())
        (tmp / "probe-scorecard.csv").write_text(
            HEADER + "\n" + row("honest-blank") + "\n"
            + row("measured", parity="1", out=A, ref=A) + "\n")
        command = needle.replace("${here}/stdout_operands.py", str(HERE / "stdout_operands.py")) \
                        .replace('"${here}"', str(tmp))
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("UNMEASURED  : 1", result.stdout)
        self.assertIn("HELD        : 1", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
