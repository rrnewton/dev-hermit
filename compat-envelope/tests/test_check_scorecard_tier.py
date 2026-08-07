#!/usr/bin/env python3
"""Both-direction fixtures for the untiered-scorecard guard.

Every fixture is a tmpdir; the real compat-envelope directory is never written.

The load-bearing case is `test_a_newly_added_scorecard_is_caught`: the guard's
whole purpose is to catch a scorecard that did not exist when the guard was
written, so a fixture that only re-checks today's four files would prove nothing.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "check-scorecard-tier.py"
SPEC = importlib.util.spec_from_file_location("cst", MODULE)
assert SPEC and SPEC.loader
cst = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cst)

TIERED = "program,backend,tier,result\np,ptrace,T1,pass\n"
UNTIERED = "program,backend,result\np,ptrace,pass\n"


class GuardTest(unittest.TestCase):
    def test_positive_all_tiered_passes(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a-scorecard.csv").write_text(TIERED)
            (Path(t) / "b-scorecard.csv").write_text(TIERED)
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))

    def test_a_newly_added_scorecard_is_caught(self):
        """PLANT THE VIOLATION. A scorecard the guard never heard of must fail it."""
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a-scorecard.csv").write_text(TIERED)
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]), "baseline must be clean")
            # Next month someone adds one, untiered:
            (Path(t) / "brand-new-backend-scorecard.csv").write_text(UNTIERED)
            self.assertEqual(1, cst.main(["--root", t, "--quiet"]), "new untiered must be REFUSED")
            # ...and removing it restores green, so the guard is not stuck-on.
            (Path(t) / "brand-new-backend-scorecard.csv").unlink()
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))

    def test_the_set_is_derived_not_hardcoded(self):
        """A guard that hardcoded today's four names would return 4 here, not 3."""
        with tempfile.TemporaryDirectory() as t:
            for n in ("x-scorecard.csv", "y-scorecard.csv", "z-scorecard.csv"):
                (Path(t) / n).write_text(TIERED)
            self.assertEqual(3, len(cst.scorecards(Path(t))))

    def test_pre_tier_migration_backups_are_excluded(self):
        """Failing on a deliberate backup trains people to ignore the check."""
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a-scorecard.csv").write_text(TIERED)
            (Path(t) / "a-scorecard.csv.pre-tier-migration").write_text(UNTIERED)
            self.assertEqual(1, len(cst.scorecards(Path(t))))
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))

    def test_no_scorecards_is_unverifiable_not_a_silent_pass(self):
        """An empty glob must not read as 'all clear'."""
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(2, cst.main(["--root", t, "--quiet"]))
