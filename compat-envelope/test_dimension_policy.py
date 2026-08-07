#!/usr/bin/env python3
"""Both-direction tests for the dimension column policy.

Every guard is bracketed: the violating case is REFUSED and the legitimate case
still PASSES. A guard that only refuses is indistinguishable from one broken shut.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from dimension_policy import (
    COLUMN,
    UNSPECIFIED,
    VALID_DIMENSIONS,
    DimensionDefaultRefused,
    UnknownDimension,
    is_recorded,
    read_dimension,
    widen,
)


def _rows(p: Path) -> list[list[str]]:
    with p.open(newline="", encoding="utf-8") as h:
        return list(csv.reader(h))


def _write(text: str) -> Path:
    p = Path(tempfile.mkdtemp()) / "s.csv"
    p.write_text(text, encoding="utf-8")
    return p


class TestBlankReadsUnspecified(unittest.TestCase):
    def test_missing_column_is_unspecified(self):
        self.assertEqual(read_dimension({"backend": "liteinst"}), UNSPECIFIED)

    def test_blank_spellings_are_unspecified(self):
        for blank in ("", "   ", "-", "n/a", "none", "NULL", "unspecified"):
            with self.subTest(blank=blank):
                self.assertEqual(read_dimension({COLUMN: blank}), UNSPECIFIED)

    def test_POSITIVE_each_valid_dimension_round_trips(self):
        for dim in VALID_DIMENSIONS:
            with self.subTest(dim=dim):
                self.assertEqual(read_dimension({COLUMN: dim}), dim)
                self.assertTrue(is_recorded({COLUMN: dim}))

    def test_blank_is_not_recorded(self):
        self.assertFalse(is_recorded({COLUMN: ""}))


class TestConcreteDefaultIsRefused(unittest.TestCase):
    def test_NEGATIVE_defaulting_to_a_real_dimension_is_refused(self):
        for planted in VALID_DIMENSIONS:
            with self.subTest(planted=planted):
                with self.assertRaises(DimensionDefaultRefused):
                    read_dimension({COLUMN: ""}, default=planted)

    def test_NEGATIVE_refused_even_when_the_row_records_one(self):
        with self.assertRaises(DimensionDefaultRefused):
            read_dimension({COLUMN: "stack"}, default="stack")

    def test_POSITIVE_the_permitted_default_works(self):
        self.assertEqual(read_dimension({COLUMN: ""}, default=UNSPECIFIED), UNSPECIFIED)


class TestClosedVocabulary(unittest.TestCase):
    def test_NEGATIVE_a_typo_is_refused_not_admitted(self):
        for typo in ("stak", "Stack", "heap ordinals", "STDOUT"):
            with self.subTest(typo=typo):
                with self.assertRaises(UnknownDimension):
                    read_dimension({COLUMN: typo})

    def test_POSITIVE_the_vocabulary_itself_is_admitted(self):
        for dim in VALID_DIMENSIONS:
            self.assertEqual(read_dimension({COLUMN: dim}), dim)


class TestWiden(unittest.TestCase):
    def test_adds_an_empty_column_and_invents_nothing(self):
        p = _write("a,backend\n1,liteinst\n2,ptrace\n")
        changed, n = widen(p, backup=False)
        self.assertTrue(changed)
        self.assertEqual(n, 2)
        rows = _rows(p)
        self.assertEqual(rows[0][-1], COLUMN)
        for r in rows[1:]:
            self.assertEqual(r[-1], "")  # empty, not guessed
            self.assertEqual(read_dimension(dict(zip(rows[0], r))), UNSPECIFIED)

    def test_is_idempotent(self):
        p = _write("a,backend\n1,liteinst\n")
        widen(p, backup=False)
        changed, n = widen(p, backup=False)
        self.assertFalse(changed)
        self.assertEqual(n, 0)

    def test_NEGATIVE_refuses_a_headerless_file(self):
        p = _write("")
        with self.assertRaises(ValueError):
            widen(p, backup=False)

    def test_NEGATIVE_refuses_an_already_misaligned_file(self):
        # Genuinely ragged (unquoted extra field), not a quoted comma.
        p = _write("a,backend\n1,liteinst,extra\n")
        with self.assertRaises(ValueError):
            widen(p, backup=False)

    def test_POSITIVE_a_quoted_comma_is_not_mistaken_for_raggedness(self):
        p = _write('a,reason\n1,"broke, and also broke"\n')
        changed, n = widen(p, backup=False)
        self.assertTrue(changed)
        rows = _rows(p)
        self.assertEqual(rows[1][1], "broke, and also broke")
        self.assertEqual(rows[1][-1], "")

    def test_existing_values_are_untouched(self):
        p = _write("a,backend\n1,liteinst\n")
        widen(p, backup=False)
        rows = _rows(p)
        self.assertEqual(rows[1][:2], ["1", "liteinst"])


if __name__ == "__main__":
    unittest.main()
