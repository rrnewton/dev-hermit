#!/usr/bin/env python3
"""Tests for the executed-cell-count requirement on headlines.

The rule under test is a refusal, so every case is bracketed: the violating
input must be refused AND the qualifying input must still render, otherwise a
gate that refuses everything would pass this file.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from headline import NO_RESULT, Headline, HeadlineError, render_all  # noqa: E402


class ZeroExecutedTests(unittest.TestCase):
    """'A run that measured nothing is indistinguishable from one that
    measured everything and passed' -- this is the class being closed."""

    def test_zero_executed_is_never_a_pass(self):
        self.assertFalse(Headline("sabre", 0, 0, 72).is_pass)

    def test_zero_executed_renders_no_result_not_a_percentage(self):
        text = Headline("sabre", 0, 0, 72).render()
        self.assertIn(NO_RESULT, text)
        self.assertIn("executed=0/72", text)
        self.assertNotIn("%", text)

    def test_zero_executed_zero_denominator_still_no_result(self):
        self.assertIn(NO_RESULT, Headline("empty", 0, 0, 0).render())
        self.assertFalse(Headline("empty", 0, 0, 0).is_pass)

    def test_measured_nothing_and_measured_everything_render_differently(self):
        """The literal defect sentence, as an assertion."""
        nothing = Headline("x", 0, 0, 72).render()
        everything = Headline("x", 72, 72, 72).render()
        self.assertNotEqual(nothing, everything)
        self.assertIn(NO_RESULT, nothing)
        self.assertNotIn(NO_RESULT, everything)

    def test_full_pass_is_still_a_pass(self):
        """Positive control: the guard must not refuse everything."""
        h = Headline("ptrace", 72, 72, 72)
        self.assertTrue(h.is_pass)
        self.assertIn("100% of 72 executed", h.render())


class PartialCoverageTests(unittest.TestCase):
    """The measured dbi case: 8/8 executed, published as 11%."""

    def setUp(self):
        self.dbi = Headline("dbi", passed=8, executed=8, denominator=72)

    def test_both_rates_are_shown(self):
        text = self.dbi.render()
        self.assertIn("11% of 72 denominator", text)
        self.assertIn("100% of 8 executed", text)

    def test_shortfall_is_explicit(self):
        self.assertIn("64 unmeasured", self.dbi.render())
        self.assertEqual(self.dbi.unmeasured, 64)

    def test_partial_sweep_is_not_a_pass_even_when_all_of_it_passed(self):
        """8/8 passing must not wear a green while 64 cells never ran."""
        self.assertFalse(self.dbi.is_pass)

    def test_executed_count_distinguishes_equal_percentages(self):
        """Same headline %, different executed counts -> different render."""
        thin = Headline("a", 8, 8, 72)
        thick = Headline("a", 8, 72, 72)
        self.assertAlmostEqual(thin.rate_of_denominator, thick.rate_of_denominator)
        self.assertNotEqual(thin.render(), thick.render())


class MalformedTests(unittest.TestCase):
    def test_passed_exceeding_executed_is_refused(self):
        with self.assertRaises(HeadlineError):
            Headline("x", passed=9, executed=8, denominator=72)

    def test_executed_exceeding_denominator_is_refused(self):
        with self.assertRaises(HeadlineError):
            Headline("x", passed=1, executed=80, denominator=72)

    def test_negative_is_refused(self):
        for kwargs in ({"passed": -1}, {"executed": -1}, {"denominator": -1}):
            base = {"passed": 0, "executed": 0, "denominator": 0}
            base.update(kwargs)
            with self.subTest(**kwargs), self.assertRaises(HeadlineError):
                Headline("x", **base)


class RenderAllTests(unittest.TestCase):
    def test_empty_summary_is_refused_not_printed(self):
        with self.assertRaises(HeadlineError):
            render_all([])

    def test_total_carries_its_own_executed_count(self):
        text = render_all([Headline("dbi", 8, 8, 72), Headline("sabre", 0, 0, 72)])
        self.assertIn("TOTAL", text)
        self.assertIn("of 8 executed", text)      # 8 executed across both
        self.assertIn("144 denominator", text)

    def test_all_backends_empty_totals_to_no_result(self):
        text = render_all([Headline("a", 0, 0, 10), Headline("b", 0, 0, 10)])
        self.assertIn("TOTAL: " + NO_RESULT, text)


class MeasuredScorecardTests(unittest.TestCase):
    """Regression on the real numbers, so the fixture is not invented."""

    CASES = [("dbi", 8, 8, 72), ("kvm", 15, 27, 72), ("sabre", 0, 0, 72),
             ("liteinst", 27, 36, 72)]

    def test_no_backend_currently_qualifies_as_a_pass(self):
        for label, p, e, d in self.CASES:
            with self.subTest(label):
                self.assertFalse(Headline(label, p, e, d).is_pass)

    def test_sabre_is_no_result_not_zero_percent(self):
        self.assertIn(NO_RESULT, Headline("sabre", 0, 0, 72).render())

    def test_dbi_of_measured_rate_is_visible(self):
        self.assertIn("100% of 8 executed", Headline("dbi", 8, 8, 72).render())


if __name__ == "__main__":
    unittest.main(verbosity=2)
