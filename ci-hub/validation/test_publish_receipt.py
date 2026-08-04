#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("publish_receipt.py")
SPEC = importlib.util.spec_from_file_location("publish_receipt", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReceiptTests(unittest.TestCase):
    def row(self, root: Path, executed: int = 12):
        log = root / "validate.log"
        log.write_text("running 12 tests\ntest result: ok. 12 passed; 0 failed\n")
        return {
            "schema_version": 1,
            "started_at": "2026-08-04T12:00:00Z",
            "finished_at": "2026-08-04T12:01:00Z",
            "commit": "a" * 40,
            "profile": "full",
            "selection_mode": "full",
            "commit_anchored": True,
            "tree_dirty": False,
            "result": "pass",
            "checks": 5,
            "failures": 0,
            "executed_tests": executed,
            "filtered_tests": 0,
            "log_file": str(log),
        }

    def test_counted_exact_head_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(Path(directory))
            selected = MODULE.qualifying_row([row], "a" * 40)
            receipt, body, digest = MODULE.build_receipt("rrnewton/hermit", "a" * 40, selected)
            self.assertEqual(receipt["ledger_record"]["executed_tests"], 12)
            self.assertEqual(receipt["commit"], "a" * 40)
            self.assertEqual(len(receipt["log_sha256"]), 64)
            self.assertEqual(MODULE.hashlib.sha256(body).hexdigest(), digest)

    def test_zero_executed_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                MODULE.qualifying_row([self.row(Path(directory), executed=0)], "a" * 40)

    def test_wrong_head_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                MODULE.qualifying_row([self.row(Path(directory))], "b" * 40)

    def test_count_capable_row_requires_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(Path(directory))
            row["schema_version"] = MODULE.COUNTS_SCHEMA
            with self.assertRaises(SystemExit):
                MODULE.qualifying_row([row], "a" * 40)
            row["coverage"] = {
                "planned_test_nodes": 3,
                "executed_test_nodes": 3,
                "zero_executed_nodes": [],
                "absent_nodes": [],
            }
            self.assertEqual(MODULE.qualifying_row([row], "a" * 40), row)


if __name__ == "__main__":
    unittest.main()
