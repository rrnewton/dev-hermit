#!/usr/bin/env python3
"""Tests for the prove-the-guard-by-deletion review standard.

Two halves: the checker behaves, and the policy text that the checker enforces
actually says what it enforces. A checker whose policy was quietly reworded is
the same defect one level up.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from guard_deletion_evidence import (  # noqa: E402
    DELETION,
    ELEMENTS,
    REPRODUCTION,
    RESTORATION,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "post-facto-review.md"
CHECKER = Path(__file__).resolve().parent / "guard_deletion_evidence.py"

COMPLETE = """
[adversarial-reviewer agent, opus-5]
I deleted the subscription guard at lines 88-94 and reran the suite.
The failure reproduced: unsubscribed RDTSCP delivery returned, exit 1,
'observed unexpected signal'. Restored the guard; suite green again, 42/42.
"""

INERT = """
[adversarial-reviewer agent, opus-5]
I removed the guard entirely and reran the suite. No failure returned --
the deletion changed nothing and the suite stayed green. Restored it.
Reporting INERT.
"""


class CompleteEvidenceTests(unittest.TestCase):
    def test_all_three_elements_detected(self):
        v = evaluate(COMPLETE)
        self.assertEqual(set(v.present), set(ELEMENTS))
        self.assertTrue(v.complete)
        self.assertTrue(v.ok)
        self.assertFalse(v.inert)

    def test_reading_only_review_is_incomplete(self):
        """The exact failure this standard exists to catch."""
        v = evaluate("I read the guard carefully. It looks correct. Approved.")
        self.assertFalse(v.ok)
        self.assertIn(DELETION, v.missing)
        self.assertIn(REPRODUCTION, v.missing)
        self.assertIn(RESTORATION, v.missing)

    def test_deletion_without_reproduction_is_incomplete(self):
        v = evaluate("I deleted the guard and restored it afterwards.")
        self.assertIn(REPRODUCTION, v.missing)
        self.assertFalse(v.ok)

    def test_reproduction_without_restoration_is_incomplete(self):
        v = evaluate("Removed it; the failure reproduced with exit 1.")
        self.assertIn(RESTORATION, v.missing)
        self.assertFalse(v.ok)

    def test_empty_comment_is_refused(self):
        with self.assertRaises(ValueError):
            evaluate("   ")


class InertTests(unittest.TestCase):
    def test_inert_is_detected_and_is_not_a_failure_of_form(self):
        v = evaluate(INERT)
        self.assertTrue(v.inert)
        self.assertTrue(v.complete, "an inert report is still complete evidence")
        self.assertTrue(v.ok)

    def test_approval_while_inert_is_contradictory(self):
        v = evaluate(INERT + "\npassed-review-claude")
        self.assertTrue(v.contradictory)
        self.assertFalse(v.ok)

    def test_require_proven_rejects_inert(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(INERT)
        p = subprocess.run([sys.executable, str(CHECKER), "--comment", fh.name,
                            "--require-proven"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("INERT", p.stdout)

    def test_inert_alone_without_require_proven_is_accepted(self):
        """Positive control: reporting inert honestly must not be punished."""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(INERT)
        p = subprocess.run([sys.executable, str(CHECKER), "--comment", fh.name],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)


class NoopMutationTests(unittest.TestCase):
    """A mutation that never applied is not a detection."""

    def test_noop_edit_with_approval_is_contradictory(self):
        v = evaluate(COMPLETE + "\nNote: the mutation did not apply. passed-review-claude")
        self.assertTrue(v.noop)
        self.assertTrue(v.contradictory)
        self.assertFalse(v.ok)


class CliTests(unittest.TestCase):
    def _run(self, text, *extra):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(text)
        return subprocess.run([sys.executable, str(CHECKER), "--comment", fh.name, *extra],
                              capture_output=True, text=True)

    def test_exit_0_on_complete(self):
        self.assertEqual(self._run(COMPLETE).returncode, 0)

    def test_exit_1_on_reading_only(self):
        self.assertEqual(self._run("Looks correct to me, approved.").returncode, 1)

    def test_exit_2_on_missing_file(self):
        p = subprocess.run([sys.executable, str(CHECKER), "--comment", "/nonexistent.md"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_incomplete_never_renders_as_proven(self):
        out = self._run("Looks correct to me, approved.").stdout
        self.assertIn("NOT-ESTABLISHED", out)
        self.assertNotIn("PROVEN", out)

    def test_complete_does_render_as_proven(self):
        self.assertIn("PROVEN", self._run(COMPLETE).stdout)

    def test_counts_are_reported(self):
        self.assertIn("elements present : 3 of 3", self._run(COMPLETE).stdout)


class PolicyTextTests(unittest.TestCase):
    """The skill must actually carry the standard the checker enforces."""

    def setUp(self):
        self.text = SKILL.read_text()

    def test_section_exists(self):
        self.assertIn("Prove the guard by deletion", self.text)

    def test_uses_the_owners_term_mutation_testing(self):
        self.assertIn("mutation testing", self.text.lower())

    def test_all_three_required_elements_are_named(self):
        for word in ("Delete", "Reproduce", "Restore"):
            with self.subTest(word):
                self.assertIn(word, self.text)

    def test_inert_outcome_is_specified_and_not_an_approval(self):
        self.assertIn("INERT", self.text)
        self.assertIn("reported, not approved", self.text)

    def test_guard_evidence_is_a_mandatory_pr_section(self):
        self.assertIn("**Guard Evidence**", self.text)

    def test_canonical_example_is_cited(self):
        self.assertIn("#403", self.text)

    def test_scope_is_bounded_so_it_is_not_a_tax_on_every_pr(self):
        self.assertIn("Not required for a PR that merely", self.text)

    def test_section_numbering_is_unique_and_contiguous(self):
        import re
        nums = [int(n) for n in re.findall(r"^## (\d+)\. ", self.text, re.M)]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(len(nums), len(set(nums)), "duplicate section numbers")
        self.assertEqual(nums, list(range(1, len(nums) + 1)), "numbering has a gap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
