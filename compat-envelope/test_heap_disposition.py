#!/usr/bin/env python3
"""Both directions for the heap disposition.

The point of the rule is that a cell which does not exercise heap reads
NO-RESULT rather than a value. The positive half matters just as much: a
disposition that returned NO-RESULT for everything would satisfy every negative
below and destroy the dimension.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "hd", Path(__file__).resolve().parent / "heap_disposition.py")
hd = importlib.util.module_from_spec(_s)
_s.loader.exec_module(hd)

CONST = "74518f204d46de660dff3ed003e92476bad8c691"
G1 = "20d8f4e6d2ff358ec9d14eda3355e3a0f6c18c6e"
G2 = "e1382fb36cf67141a0b6766c0cb0ccca375a2675"


def log(*hashes: str) -> str:
    return "\n".join(
        f"INFO detcore: DETLOG [memory][dtid 3] 0x405000-0x426000 MMPermissions(READ) 0 0:0 0 [heap]->{h}"
        for h in hashes)


class Dispositions(unittest.TestCase):
    # ---- positive: real activity is still measured, and counted correctly ----

    def test_exercised_cell_reports_its_guest_records(self):
        d = hd.disposition(log(CONST, G1, G2))
        self.assertEqual(d["heap_disposition"], "exercised")
        self.assertEqual(d["heap_guest_records"], 2)      # the constant excluded
        self.assertEqual(d["heap_records_raw"], 3)
        self.assertTrue(hd.is_measurement(d))

    def test_the_common_heap_equals_2_cell_counts_as_ONE(self):
        """66.7% of measured cells look like this: constant + one guest record.
        It IS a measurement, but of one allocation, not two."""
        d = hd.disposition(log(CONST, G1))
        self.assertEqual(d["heap_disposition"], "exercised")
        self.assertEqual(d["heap_guest_records"], 1)
        self.assertEqual(d["heap_records_raw"], 2)
        self.assertTrue(hd.is_measurement(d))

    # ---- negative: absence is typed, never a number -------------------------

    def test_no_records_is_NO_RESULT_not_zero(self):
        d = hd.disposition("")
        self.assertEqual(d["heap_disposition"], "no-heap-activity")
        self.assertIsNone(d["heap_guest_records"])
        self.assertNotEqual(d["heap_guest_records"], 0)   # 0 would be a value
        self.assertFalse(hd.is_measurement(d))

    def test_constant_only_is_NO_RESULT(self):
        """The whole point: a cell carrying ONLY the universal record has
        measured nothing about the guest, however non-zero it looks."""
        d = hd.disposition(log(CONST))
        self.assertEqual(d["heap_disposition"], "heap-constant-only")
        self.assertIsNone(d["heap_guest_records"])
        self.assertEqual(d["heap_records_raw"], 1)
        self.assertFalse(hd.is_measurement(d))

    def test_repeated_constant_is_still_NO_RESULT(self):
        d = hd.disposition(log(CONST, CONST, CONST))
        self.assertEqual(d["heap_disposition"], "heap-constant-only")
        self.assertIsNone(d["heap_guest_records"])

    # ---- the correction is auditable, not a silent overwrite ----------------

    def test_raw_count_is_retained_beside_the_corrected_one(self):
        d = hd.disposition(log(CONST, G1))
        self.assertEqual(d["heap_records_raw"], 2)
        self.assertEqual(d["heap_constant_records"], 1)
        self.assertEqual(d["heap_guest_records"], 1)
        self.assertEqual(d["heap_records_raw"],
                         d["heap_constant_records"] + d["heap_guest_records"])

    def test_a_cell_with_no_constant_at_all_is_still_exercised(self):
        """Do not require the constant: a future guest that never touches the
        initial image must not be refused for it."""
        d = hd.disposition(log(G1, G2))
        self.assertEqual(d["heap_disposition"], "exercised")
        self.assertEqual(d["heap_guest_records"], 2)
        self.assertEqual(d["heap_constant_records"], 0)


if __name__ == "__main__":
    unittest.main()
