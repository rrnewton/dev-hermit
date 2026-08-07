#!/usr/bin/env python3
"""Both-direction tests for the tier default policy.

Each guard is bracketed: the violating case must be REFUSED, and the legitimate
case must still PASS. A guard that only ever refuses is indistinguishable from
one that is broken shut, so every negative below has a positive beside it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tier_policy import (
    UNKNOWN,
    RaggedRow,
    TierDefaultRefused,
    is_recorded,
    iter_rows_strict,
    read_tier,
    survey,
)


class TestBlankReadsAsUnknown(unittest.TestCase):
    def test_missing_column_reads_unknown(self):
        # 3 of the 4 scorecards have no tier column at all.
        self.assertEqual(read_tier({"backend": "liteinst"}), UNKNOWN)

    def test_empty_string_reads_unknown(self):
        self.assertEqual(read_tier({"tier": ""}), UNKNOWN)

    def test_whitespace_reads_unknown(self):
        self.assertEqual(read_tier({"tier": "   "}), UNKNOWN)

    def test_placeholder_spellings_read_unknown(self):
        for blank in ("-", "n/a", "none", "null", "NONE"):
            with self.subTest(blank=blank):
                self.assertEqual(read_tier({"tier": blank}), UNKNOWN)

    def test_POSITIVE_a_recorded_tier_is_returned_unchanged(self):
        # The guard must not swallow real values.
        self.assertEqual(read_tier({"tier": "stripped-uncounted"}), "stripped-uncounted")
        self.assertEqual(read_tier({"tier": "bitwise"}), "bitwise")

    def test_is_recorded_splits_the_two_cases(self):
        self.assertFalse(is_recorded({"tier": ""}))
        self.assertTrue(is_recorded({"tier": "bitwise"}))


class TestPassingDefaultIsRefused(unittest.TestCase):
    """Item 4: the defect that would make every unmeasured cell claim a standard."""

    def test_NEGATIVE_defaulting_to_a_passing_tier_is_refused(self):
        for planted in ("bitwise", "full", "canonical", "pass", "stripped-uncounted"):
            with self.subTest(planted=planted):
                with self.assertRaises(TierDefaultRefused):
                    read_tier({"tier": ""}, default=planted)

    def test_NEGATIVE_refused_even_when_the_row_has_a_real_tier(self):
        # The unsafe default is a bug at the call site regardless of this row.
        with self.assertRaises(TierDefaultRefused):
            read_tier({"tier": "bitwise"}, default="bitwise")

    def test_NEGATIVE_a_novel_tier_name_is_also_refused(self):
        # Deliberately not in any vocabulary: refusing every non-unknown default
        # means a new producer tier cannot sneak in as a default.
        with self.assertRaises(TierDefaultRefused):
            read_tier({"tier": ""}, default="tier-invented-next-week")

    def test_POSITIVE_the_permitted_default_still_works(self):
        self.assertEqual(read_tier({"tier": ""}, default=UNKNOWN), UNKNOWN)

    def test_refusal_message_names_the_reason(self):
        with self.assertRaises(TierDefaultRefused) as caught:
            read_tier({"tier": ""}, default="bitwise")
        self.assertIn("unknown", str(caught.exception))
        self.assertIn("bitwise", str(caught.exception))


class TestRaggedRowsAreRefused(unittest.TestCase):
    """Item 1: an unquoted comma silently misaligns every later column."""

    def _write(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "s.csv"
        p.write_text(text, encoding="utf-8")
        return p

    def test_NEGATIVE_extra_field_is_refused(self):
        p = self._write("a,reason,tier\n1,broke, and also broke,\n")
        with self.assertRaises(RaggedRow):
            list(iter_rows_strict(p))

    def test_NEGATIVE_short_row_is_refused(self):
        p = self._write("a,reason,tier\n1,broke\n")
        with self.assertRaises(RaggedRow):
            list(iter_rows_strict(p))

    def test_POSITIVE_a_properly_quoted_comma_parses(self):
        # The fix for item 1 is quoting, so quoted commas must NOT be refused.
        p = self._write('a,reason,tier\n1,"broke, and also broke",\n')
        rows = list(iter_rows_strict(p))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "broke, and also broke")
        self.assertEqual(read_tier(rows[0]), UNKNOWN)

    def test_POSITIVE_clean_file_parses_and_counts(self):
        p = self._write("a,reason,tier\n1,ok,bitwise\n2,ok,\n")
        rows, recorded, ragged = survey(p)
        self.assertEqual((rows, recorded, ragged), (2, 1, 0))

    def test_ragged_row_is_counted_not_silently_realigned(self):
        p = self._write("a,reason,tier\n1,broke, extra,\n2,ok,bitwise\n")
        rows, recorded, ragged = survey(p)
        self.assertEqual(ragged, 1)
        self.assertEqual(rows, 2)
        # The ragged row must not contribute a phantom recorded tier.
        self.assertEqual(recorded, 1)


class TestCountsCarryDenominators(unittest.TestCase):
    def test_empty_file_is_zero_of_zero_not_a_pass(self):
        d = Path(tempfile.mkdtemp())
        p = d / "s.csv"
        p.write_text("a,reason,tier\n", encoding="utf-8")
        self.assertEqual(survey(p), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
