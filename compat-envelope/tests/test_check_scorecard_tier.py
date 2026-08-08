#!/usr/bin/env python3
"""Both-direction fixtures for the untiered-scorecard guard.

Every fixture is a tmpdir; the real compat-envelope directory is never written.

The load-bearing case is `test_a_newly_added_scorecard_is_caught`: the guard's
whole purpose is to catch a scorecard that did not exist when the guard was
written, so a fixture that only re-checks today's four files would prove nothing.
"""

from __future__ import annotations

import importlib.util
import csv
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "check-scorecard-tier.py"
MIGRATE = MODULE.with_name("migrate-scorecard-schema.py")
README = MODULE.with_name("README.md")
SPEC = importlib.util.spec_from_file_location("cst", MODULE)
assert SPEC and SPEC.loader
cst = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cst)

TIERED = (
    "program,backend,comparison_tier,result\n"
    "p,ptrace,full-stdout-info-stack-heap,pass\n"
)
SPOT_CHECKED = (
    "program,backend,comparison_tier,result\n"
    "p,ptrace,stdout-info-stack-heap-spot-check,pass\n"
)
EXPLICITLY_UNQUALIFIED = (
    "program,backend,comparison_tier,result\n"
    "p,ptrace,legacy-unqualified,pass\n"
)
SELF_VERIFIED_ONLY = (
    "program,backend,comparison_tier,result\n"
    "p,ptrace,unqualified-self-verify-only,pass\n"
)
NO_COMPARISON = (
    "program,backend,comparison_tier,result\n"
    "p,ptrace,unqualified-no-comparison,gap\n"
)
UNKNOWN_TIER = (
    "program,backend,comparison_tier,result\n"
    "p,ptrace,unqualified-self-verify,pass\n"
)
UNTIERED_COLUMN = "program,backend,result\np,ptrace,pass\n"
BLANK_TIER = "program,backend,comparison_tier,result\np,ptrace,,pass\n"


class GuardTest(unittest.TestCase):
    def test_positive_all_tiered_passes(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a-scorecard.csv").write_text(TIERED)
            (Path(t) / "b-scorecard.csv").write_text(SPOT_CHECKED)
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))

    def test_explicit_legacy_is_not_silently_promoted(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a-scorecard.csv"
            p.write_text(EXPLICITLY_UNQUALIFIED)
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))
            with p.open() as fh:
                rows = list(csv.DictReader(fh))
            self.assertNotIn(rows[0]["comparison_tier"], cst.QUALIFYING)

    def test_self_verify_only_is_known_but_never_cross_backend_green(self):
        """Within-backend consistency is recorded without inventing parity."""
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a-scorecard.csv"
            p.write_text(SELF_VERIFIED_ONLY)
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))
            with p.open() as fh:
                tier = next(csv.DictReader(fh))["comparison_tier"]
            self.assertIn(tier, cst.UNQUALIFIED)
            self.assertNotIn(tier, cst.QUALIFYING)

    def test_nearby_unknown_self_verify_spelling_is_refused(self):
        """The new value is one exact provenance class, not a prefix escape."""
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a-scorecard.csv").write_text(UNKNOWN_TIER)
            self.assertEqual(1, cst.main(["--root", t, "--quiet"]))

    def test_no_comparison_is_known_but_never_green(self):
        """An unexecuted gap carries no invented comparison witness."""
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a-scorecard.csv"
            p.write_text(NO_COMPARISON)
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))
            with p.open() as fh:
                tier = next(csv.DictReader(fh))["comparison_tier"]
            self.assertIn(tier, cst.UNQUALIFIED)
            self.assertNotIn(tier, cst.QUALIFYING)

    def test_self_verify_only_has_schema_facing_provenance(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("`unqualified-self-verify-only`", text)
        self.assertIn("within-backend self-consistency", text)
        self.assertIn("makes no cross-backend stdout", text)
        self.assertIn("`unqualified-no-comparison`", text)
        self.assertIn("no comparison witness exists", text)

    def test_migration_marks_history_unqualified_without_inventing_a_strict_tier(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "old-scorecard.csv"
            path.write_text(
                "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,"
                "test_id,test_mode,backend,cell_state,outcome,deterministic,parity,"
                "output_hash,duration_ms,max_rss_kb,reason\n"
                "r,@0,h,v,false,regression,portable,b,t,verify,ptrace,enabled,pass,"
                "1,1,abc,1,,\n"
            )
            with path.open() as fh:
                before = list(csv.DictReader(fh))
            run = subprocess.run(
                ["python3", str(MIGRATE), str(path), "--apply"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, run.returncode, run.stderr)
            with path.open() as fh:
                after = list(csv.DictReader(fh))
            self.assertEqual("legacy-unqualified", after[0]["comparison_tier"])
            self.assertNotIn(after[0]["comparison_tier"], cst.QUALIFYING)
            for key in ("run_id", "test_id", "outcome", "deterministic", "output_hash"):
                self.assertEqual(before[0][key], after[0][key])
            first = path.read_bytes()
            rerun = subprocess.run(
                ["python3", str(MIGRATE), str(path), "--apply"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, rerun.returncode, rerun.stderr)
            self.assertEqual(first, path.read_bytes(), "migration must be idempotent")

    def test_a_newly_added_scorecard_is_caught(self):
        """PLANT THE VIOLATION. A scorecard the guard never heard of must fail it."""
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a-scorecard.csv").write_text(TIERED)
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]), "baseline must be clean")
            # Next month someone adds one, untiered:
            (Path(t) / "brand-new-backend-scorecard.csv").write_text(UNTIERED_COLUMN)
            self.assertEqual(1, cst.main(["--root", t, "--quiet"]), "new untiered must be REFUSED")
            # ...and removing it restores green, so the guard is not stuck-on.
            (Path(t) / "brand-new-backend-scorecard.csv").unlink()
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))

    def test_green_with_blank_tier_is_refused_not_defaulted(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a-scorecard.csv").write_text(BLANK_TIER)
            self.assertEqual(1, cst.main(["--root", t, "--quiet"]))

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
            (Path(t) / "a-scorecard.csv.pre-tier-migration").write_text(UNTIERED_COLUMN)
            self.assertEqual(1, len(cst.scorecards(Path(t))))
            self.assertEqual(0, cst.main(["--root", t, "--quiet"]))

    def test_no_scorecards_is_unverifiable_not_a_silent_pass(self):
        """An empty glob must not read as 'all clear'."""
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(2, cst.main(["--root", t, "--quiet"]))
