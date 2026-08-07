#!/usr/bin/env python3
"""Both directions for the tier-evidence check.

The negative half is the point of the module, but the positive half is what
stops it being useless: a checker that refused every row would satisfy every
negative test below and destroy the tier. So every refusal here is paired with a
row that differs ONLY in the field under test and MUST be upheld.

The planted case the task named -- a cell claiming FULL while missing stdout --
is `test_NEGATIVE_full_missing_stdout_is_rejected`. It is not hypothetical: six
such rows were live in `scorecard.csv` on 2026-08-07 and the vocabulary gate
scored all six as qualified green.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("te", HERE / "tier_evidence.py")
te = importlib.util.module_from_spec(_spec)
# See the note in tier_evidence._load: a dataclass in a module that is not in
# sys.modules cannot resolve __module__ and fails to construct.
sys.modules["te"] = te
_spec.loader.exec_module(te)

NOW = _dt.datetime(2026, 8, 7, tzinfo=_dt.timezone.utc)

HEADER = ("test_id,test_mode,backend,outcome,stdout_parity,bitwise_parity,"
          "compared_log_messages,stack_parity,heap_parity,duration_ms,comparison_tier")
FULL = te.FULL
SPOT = te.SPOT_CHECK


def run(rows, ledger_rows=(), *, header=HEADER, now=NOW, cadence_days=14):
    """Check `rows` in a throwaway root; returns the Report."""
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "probe-scorecard.csv").write_text("\n".join((header, *rows)) + "\n")
    ledger = root / "ledger.csv"
    ledger.write_text("\n".join((
        "test_id,test_mode,backend,duration_ms,spot_check_utc,hermit_sha,result,detail",
        *ledger_rows)) + "\n")
    return te.check(root, now=now, cadence_days=cadence_days, ledger_path=ledger)


def full_row(name, stdout="1", info_verdict="1", info="348|348",
             stack="1", heap="1"):
    return (f"{name},verify,ptrace,pass,{stdout},{info_verdict},{info},"
            f"{stack},{heap},120,{FULL}")


def spot_row(name):
    return f"{name},verify,ptrace,pass,1,1,348|348,,,9000,{SPOT}"


def receipt(name, when="2026-08-05T00:00:00Z", sha="abc1234def"):
    return f"{name},verify,ptrace,9000,{when},{sha},PASS,receipt"


class FullTier(unittest.TestCase):
    def test_POSITIVE_a_complete_full_claim_is_upheld(self):
        """Without this the negatives below prove nothing."""
        r = run([full_row("ok")])
        self.assertEqual((r.claims, r.upheld, len(r.violations)), (1, 1, 0))

    def test_NEGATIVE_full_missing_stdout_is_rejected(self):
        """THE PLANTED CASE. Six of these were live and scored qualified green."""
        r = run([full_row("no-stdout", stdout="")])
        self.assertEqual(r.upheld, 0)
        self.assertEqual(len(r.violations), 1)
        self.assertIn("missing:stdout", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_full_missing_info_log_is_rejected(self):
        r = run([full_row("no-info", info="")])
        self.assertIn("missing:info_log", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_stdout_divergence_is_evidence_but_not_green(self):
        r = run([full_row("stdout-diverged", stdout="0")])
        self.assertIn("diverged:stdout", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_info_divergence_reaches_the_tier(self):
        """The task's planted `bitwise_parity=0` must make this cell non-green."""
        r = run([full_row("info-diverged", info_verdict="0", info="169|186")])
        self.assertIn("diverged:info_log", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_heap_divergence_is_not_merely_nonblank_evidence(self):
        r = run([full_row("heap-diverged", heap="fail")])
        self.assertIn("diverged:heap", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_an_info_comparison_of_zero_records_is_not_evidence(self):
        """`0|0` is a comparison that compared nothing; non-blank must not mean measured."""
        r = run([full_row("zero-info", info="0|0")])
        self.assertIn("empty-comparison:info_log", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_every_missing_component_is_named_not_just_the_first(self):
        """A reader must learn everything to fix, not discover it one run at a time."""
        r = run([full_row("nothing", stdout="", info="", stack="", heap="")])
        joined = "; ".join(r.violations[0].reasons)
        for component in ("stdout", "info_log", "stack", "heap"):
            self.assertIn(component, joined)

    def test_NEGATIVE_a_missing_COLUMN_is_distinguished_from_a_blank_VALUE(self):
        """Schema-cannot-express and producer-did-not-measure need different fixes."""
        narrow = ("test_id,test_mode,backend,outcome,stdout_parity,bitwise_parity,"
                  "compared_log_messages,duration_ms,comparison_tier")
        r = run([f"no-cols,verify,ptrace,pass,1,1,348|348,120,{FULL}"], header=narrow)
        joined = "; ".join(r.violations[0].reasons)
        self.assertIn("schema-cannot-express:stack", joined)
        self.assertIn("schema-cannot-express:heap", joined)
        self.assertNotIn("missing:stdout", joined)   # stdout WAS expressible and present


class SpotCheckTier(unittest.TestCase):
    def test_POSITIVE_current_clean_receipt_is_upheld(self):
        r = run([spot_row("good")], [receipt("good")])
        self.assertEqual((r.claims, r.upheld), (1, 1))

    def test_NEGATIVE_a_dirty_receipt_is_refused(self):
        r = run([spot_row("d")], [receipt("d", sha="gf89c69766371-dirty")])
        self.assertIn("dirty-receipt", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_a_blank_sha_is_also_dirty(self):
        """A receipt naming no tree is no more reproducible than one naming a dirty one."""
        r = run([spot_row("b")], [receipt("b", sha="")])
        self.assertIn("dirty-receipt", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_a_stale_receipt_is_refused(self):
        r = run([spot_row("s")], [receipt("s", when="2026-06-01T00:00:00Z")])
        self.assertIn("cadence:STALE", "; ".join(r.violations[0].reasons))

    def test_NEGATIVE_no_receipt_is_NEVER_not_STALE(self):
        """NEVER must not report as STALE: that would imply a measurement once existed."""
        r = run([spot_row("n")], [])
        joined = "; ".join(r.violations[0].reasons)
        self.assertIn("cadence:NEVER", joined)
        self.assertNotIn("STALE", joined)

    def test_dirty_is_tested_BEFORE_age_so_it_never_reports_as_stale(self):
        """An old dirty receipt is dirty, not stale -- ageing it implies it was once valid."""
        r = run([spot_row("od")], [receipt("od", when="2026-01-01T00:00:00Z",
                                           sha="deadbeef-dirty")])
        joined = "; ".join(r.violations[0].reasons)
        self.assertIn("dirty-receipt", joined)
        self.assertNotIn("STALE", joined)

    def test_spot_check_still_requires_stdout_and_info_EVERY_run(self):
        """The cheaper tier relaxes stack/heap to a cadence -- not stdout and INFO."""
        row = f"x,verify,ptrace,pass,,1,348|348,,,9000,{SPOT}"
        r = run([row], [receipt("x")])
        self.assertIn("missing:stdout", "; ".join(r.violations[0].reasons))


class ScopeAndCounting(unittest.TestCase):
    def test_a_non_qualifying_tier_is_not_a_claim(self):
        """legacy-unqualified asserts nothing, so it is neither upheld nor violated."""
        r = run([f"leg,verify,ptrace,pass,,,,,,120,legacy-unqualified"])
        self.assertEqual((r.rows, r.claims, len(r.violations)), (1, 0, 0))

    def test_counts_carry_their_denominator(self):
        r = run([full_row("a"), full_row("b", stdout=""),
                 f"c,verify,ptrace,pass,,,,,120,legacy-unqualified"])
        self.assertEqual(r.rows, 3)
        self.assertEqual(r.claims, 2)          # the legacy row is not a claim
        self.assertEqual(r.upheld, 1)
        self.assertEqual(len(r.violations), 1)

    def test_an_empty_population_is_REFUSED_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(te.PopulationError):
                te.check(Path(tmp), now=NOW, cadence_days=14,
                         ledger_path=Path(tmp) / "nope.csv")


class DirtyPredicate(unittest.TestCase):
    def test_clean_shas_are_not_dirty(self):
        for sha in ("abc1234", "a" * 40, "g1234567890a"):
            self.assertFalse(te.is_dirty_sha(sha), sha)

    def test_dirty_spellings_are_caught(self):
        for sha in ("", "   ", "gf89c69766371-dirty", "abc-DIRTY", "dirty"):
            self.assertTrue(te.is_dirty_sha(sha), sha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
