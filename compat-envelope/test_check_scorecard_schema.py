#!/usr/bin/env python3
"""Tests for the scorecard schema checker's population discipline.

The bug being regressed is not "the schema check is wrong" -- it is "the
population was implicit, so the count could not be quoted". Most of these tests
therefore assert on the POPULATION, not on the verdict.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_scorecard_schema import (  # noqa: E402
    CORE_COLUMNS,
    PopulationError,
    check,
    enumerate_population,
)

CHECKER = Path(__file__).resolve().parent / "check_scorecard_schema.py"


def make_tree(root: Path, names, columns=None) -> None:
    """Build a fake tree with a known population, so N is asserted not assumed."""
    columns = list(columns or CORE_COLUMNS)
    (root / "compat-envelope").mkdir(parents=True, exist_ok=True)
    for name in names:
        path = root / "compat-envelope" / name
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerow(["x"] * len(columns))


class PopulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_population_is_exactly_the_matching_files(self):
        make_tree(self.tmp, ["a-scorecard.csv", "b-scorecard.csv", "notes.txt", "other.csv"])
        report = check(self.tmp)
        self.assertEqual(report.population, 2)
        self.assertEqual(
            sorted(f.path for f in report.files),
            ["compat-envelope/a-scorecard.csv", "compat-envelope/b-scorecard.csv"],
        )

    def test_population_is_sorted_for_reproducibility(self):
        make_tree(self.tmp, ["z-scorecard.csv", "a-scorecard.csv", "m-scorecard.csv"])
        paths = [p.name for p in enumerate_population(self.tmp)]
        self.assertEqual(paths, sorted(paths))

    def test_report_states_root_and_pattern(self):
        make_tree(self.tmp, ["a-scorecard.csv"])
        text = check(self.tmp).render()
        self.assertIn(str(self.tmp.resolve()), text)
        self.assertIn("compat-envelope/*scorecard*.csv", text)
        self.assertIn("population    : 1 file(s) enumerated", text)

    def test_count_travels_with_its_denominator(self):
        make_tree(self.tmp, ["a-scorecard.csv", "b-scorecard.csv"])
        self.assertIn("0 of 2 scorecard(s)", check(self.tmp).render())

    def test_missing_directory_is_refused_not_reported_as_zero(self):
        """An undefined population must REFUSE, never report a clean zero."""
        with self.assertRaises(PopulationError):
            check(self.tmp)  # no compat-envelope/ at all

    def test_empty_population_is_zero_of_zero_not_a_pass_claim(self):
        (self.tmp / "compat-envelope").mkdir()
        report = check(self.tmp)
        self.assertEqual(report.population, 0)
        self.assertIn("0 of 0", report.render())


class InvocationDirectoryTests(unittest.TestCase):
    """The regression proper: the population must not follow the caller."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        make_tree(self.tmp, ["a-scorecard.csv", "b-scorecard.csv", "c-scorecard.csv"])
        # Decoys named exactly like the real thing, in the directories a
        # careless glob would have swept. If the population follows the caller,
        # these change the count.
        self.decoys = Path(tempfile.mkdtemp())
        make_tree(self.decoys, [f"decoy{i}-scorecard.csv" for i in range(7)])
        self.addCleanup(shutil.rmtree, self.decoys, ignore_errors=True)

    def _run(self, cwd):
        proc = subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(self.tmp), "--json"],
            capture_output=True, text=True, cwd=cwd,
        )
        return json.loads(proc.stdout)

    def test_identical_population_from_two_working_directories(self):
        a = self._run(cwd=str(self.tmp))
        b = self._run(cwd=str(self.decoys))
        self.assertEqual(a["population"], 3)
        self.assertEqual(a, b, "population changed with the invocation directory")

    def test_decoy_directory_with_seven_files_does_not_leak_in(self):
        """The literal 7-vs-4 shape: 7 decoys next door must not be counted."""
        self.assertEqual(len(list((self.decoys / "compat-envelope").glob("*scorecard*.csv"))), 7)
        result = self._run(cwd=str(self.decoys / "compat-envelope"))
        self.assertEqual(result["population"], 3)

    def test_copied_script_refuses_rather_than_inventing_a_population(self):
        """Copying the checker out of the tree must REFUSE, not re-glob."""
        copied = Path(tempfile.mkdtemp()) / "copied-checker.py"
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(CHECKER, copied)
        self.addCleanup(shutil.rmtree, copied.parent, ignore_errors=True)
        proc = subprocess.run(
            [sys.executable, str(copied)], capture_output=True, text=True, cwd=str(copied.parent),
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("REFUSED", proc.stderr)

    def test_explicit_root_overrides_everything(self):
        result = self._run(cwd="/")
        self.assertEqual(result["population"], 3)
        self.assertEqual(result["root"], str(self.tmp.resolve()))


class DefaultRootDiscoveryTests(unittest.TestCase):
    """The regression the task names: with NO --root, the population must come
    from the checker's own tree, not from whatever repo the caller stands in."""

    def test_default_root_ignores_a_different_repo_as_cwd(self):
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        subprocess.run(["git", "init", "-q", str(other)], check=True,
                       capture_output=True)
        # Seven decoys, the literal count from the incident.
        make_tree(other, [f"decoy{i}-scorecard.csv" for i in range(7)])

        proc = subprocess.run(
            [sys.executable, str(CHECKER), "--json"],
            capture_output=True, text=True, cwd=str(other),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(
            result["root"], str(Path(CHECKER).resolve().parent.parent),
            "root followed the caller's repository instead of the checker's tree",
        )
        self.assertEqual(
            result["population"], 4,
            f"population followed the invocation directory: got {result['population']} "
            "(7 would be the decoy repo -- the original defect)",
        )


class ViolationDetectionTests(unittest.TestCase):
    """The checker must still catch what it is for."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_clean_tree_is_clean(self):
        """Positive control: no false violation, so a catch means something."""
        make_tree(self.tmp, ["a-scorecard.csv"])
        report = check(self.tmp)
        self.assertEqual(report.violations, [])

    def test_planted_missing_core_column_is_caught(self):
        make_tree(self.tmp, ["a-scorecard.csv"])
        make_tree(
            self.tmp, ["bad-scorecard.csv"], columns=[c for c in CORE_COLUMNS if c != "backend"]
        )
        report = check(self.tmp)
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].path, "compat-envelope/bad-scorecard.csv")
        self.assertIn("backend", report.violations[0].missing)
        self.assertIn("1 of 2 scorecard(s)", report.render())

    def test_every_core_column_is_individually_load_bearing(self):
        """Dropping any one core column must be caught -- none is decorative."""
        for column in CORE_COLUMNS:
            with self.subTest(column=column):
                root = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                make_tree(root, ["s-scorecard.csv"],
                          columns=[c for c in CORE_COLUMNS if c != column])
                self.assertEqual(len(check(root).violations), 1)

    def test_extra_columns_are_allowed(self):
        """Newer producers append columns; that is not a violation."""
        make_tree(self.tmp, ["a-scorecard.csv"], columns=list(CORE_COLUMNS) + ["tier", "run_flags"])
        self.assertEqual(check(self.tmp).violations, [])

    def test_exit_code_1_on_violation_0_on_clean(self):
        make_tree(self.tmp, ["a-scorecard.csv"])
        clean = subprocess.run([sys.executable, str(CHECKER), "--root", str(self.tmp)],
                               capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0)
        make_tree(self.tmp, ["bad-scorecard.csv"],
                  columns=[c for c in CORE_COLUMNS if c != "run_id"])
        dirty = subprocess.run([sys.executable, str(CHECKER), "--root", str(self.tmp)],
                               capture_output=True, text=True)
        self.assertEqual(dirty.returncode, 1)


class RealTreeTests(unittest.TestCase):
    """Against the committed tree, the population is 4 -- never 7."""

    def test_committed_population_is_four(self):
        root = Path(__file__).resolve().parent.parent
        if not (root / ".git").exists():
            self.skipTest("not a git checkout")
        report = check(root)
        self.assertEqual(report.population, 4, f"enumerated {[f.path for f in report.files]}")
        self.assertEqual(report.violations, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
