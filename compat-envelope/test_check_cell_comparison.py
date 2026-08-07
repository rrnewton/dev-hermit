#!/usr/bin/env python3
"""The guard must REFUSE a verdict carrying no reference — and must still PASS one
that carries a reference.

WHY BOTH HALVES ARE ASSERTED HERE. This guard was tightened after 1,317 cells were
found green with no recorded reference, and the schema was then widened (2026-08-07)
with `exit_code_parity` / `detlog_parity` / `oracle_verdict`. A schema change is
exactly the kind of edit that can silently defeat a guard: if the widening had broken
the capability check, EVERY file would fail and the refusal below would still look
correct while testing nothing. So the positive control is load-bearing — it proves the
refusal is caused by the missing reference and not by a broken schema.

The fixtures are built FROM `scorecard-schema.json`, never from a hardcoded column
list, so this test cannot drift from the schema it is checking.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD = HERE / "check_cell_comparison.py"
CORE = json.loads((HERE / "scorecard-schema.json").read_text())["core"]

REF = "a" * 64


def row(**kw: str) -> str:
    idx = {c: n for n, c in enumerate(CORE)}
    cells = [""] * len(CORE)
    for key, value in kw.items():
        cells[idx[key]] = value
    return ",".join(cells)


def run_guard(*rows: str) -> subprocess.CompletedProcess:
    """Run the guard over a throwaway root containing exactly these rows."""
    with tempfile.TemporaryDirectory() as td:
        envelope = Path(td) / "compat-envelope"
        envelope.mkdir()
        (envelope / "planted-scorecard.csv").write_text(
            ",".join(CORE) + "\n" + "".join(r + "\n" for r in rows)
        )
        return subprocess.run(
            [sys.executable, str(GUARD), "--root", td],
            capture_output=True, text=True, check=False,
        )


class PlantedVerdictWithoutReference(unittest.TestCase):
    def test_a_GREEN_verdict_with_no_reference_is_REFUSED(self) -> None:
        got = run_guard(row(test_id="planted", backend="ptrace", stdout_parity="1"))
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("carry NO reference", got.stdout)

    def test_a_RED_verdict_with_no_reference_is_ALSO_REFUSED(self) -> None:
        """"It differed from something I did not record" is not a reproducible
        refusal any more than an unrecorded match is a reproducible pass."""
        got = run_guard(row(test_id="planted", backend="ptrace", stdout_parity="0"))
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("carry NO reference", got.stdout)

    def test_THE_POSITIVE_CONTROL_a_referenced_verdict_PASSES(self) -> None:
        """Without this the suite cannot tell a working guard from a broken one."""
        got = run_guard(
            row(test_id="ok", backend="ptrace", stdout_parity="1", ref_output_hash=REF)
        )
        self.assertEqual(got.returncode, 0, got.stdout)
        self.assertIn("0 of 1 verdict(s) lack a reference", got.stdout)

    def test_a_BLANK_verdict_is_a_NO_RESULT_and_never_reads_green(self) -> None:
        """Zero verdicts over a non-empty population is refused, not passed."""
        got = run_guard(row(test_id="blank", backend="ptrace", ref_output_hash=REF))
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("REFUSED (non-empty population)", got.stdout)


class TheWidenedSchemaStillSatisfiesTheCapabilityCheck(unittest.TestCase):
    """The 2026-08-07 columns exist so a verdict can be RECORDED at all."""

    def test_core_declares_every_required_capability_column(self) -> None:
        sys.path.insert(0, str(HERE))
        import check_cell_comparison as ccc  # noqa: PLC0415

        for name, column in ccc.REQUIRED_SCHEMA_CAPABILITIES.items():
            self.assertIn(column, CORE, f"{name} has no column to be recorded in")

    def test_no_file_reports_SCHEMA_CANNOT_EXPRESS(self) -> None:
        got = run_guard(
            row(test_id="ok", backend="ptrace", stdout_parity="1", ref_output_hash=REF)
        )
        self.assertNotIn("SCHEMA CANNOT EXPRESS", got.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
