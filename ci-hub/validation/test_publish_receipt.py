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
            "schema_version": 6,
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
            "coverage": {
                "planned_test_nodes": 1,
                "executed_test_nodes": 1,
                "zero_executed_nodes": [],
                "absent_nodes": [],
            },
            "reverie_binding": {
                "repository": "rrnewton/reverie",
                "ref": "refs/heads/main",
                "pinned_sha": "d" * 40,
                "resolved_sha": "d" * 40,
            },
            "host": "test-host",
            "log_file": str(log),
        }

    def report(self, row, *, verdict="VALIDATED", sha="a" * 40):
        return {
            "sha": sha,
            "verdict": verdict,
            "qualifying_count": 1 if verdict == "VALIDATED" else 0,
            "newest_qualifying_record": row if verdict == "VALIDATED" else None,
        }

    def test_counted_exact_head_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(Path(directory))
            selected = MODULE.qualifying_row(self.report(row), "a" * 40)
            durable = MODULE.preserve_log(Path(directory) / "ledger.jsonl", "a" * 40, selected)
            receipt, body, digest = MODULE.build_receipt(
                "rrnewton/hermit", "a" * 40, selected, durable
            )
            self.assertEqual(receipt["ledger_record"]["executed_tests"], 12)
            self.assertEqual(receipt["ledger_record"]["reverie_binding"]["resolved_sha"], "d" * 40)
            self.assertEqual(receipt["commit"], "a" * 40)
            self.assertEqual(len(receipt["log_sha256"]), 64)
            self.assertTrue(Path(receipt["durable_log_file"]).is_file())
            self.assertEqual(MODULE.hashlib.sha256(body).hexdigest(), digest)

    def test_run_id_binds_host_same_sha_and_started_at(self):
        # Host-in-identity (Req2): two runs of the SAME sha at the SAME started_at
        # on DIFFERENT hosts must mint DISTINCT run_ids -- before host was in the
        # identity there was no collision guard between them at all.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            durable = root / "durable.log"
            durable.write_text("log\n")
            row_a = self.row(root)
            row_a["host"] = "host-a"
            row_b = dict(row_a)
            row_b["host"] = "host-b"
            self.assertEqual(row_a["started_at"], row_b["started_at"])
            receipt_a, _, _ = MODULE.build_receipt("rrnewton/hermit", "a" * 40, row_a, durable)
            receipt_b, _, _ = MODULE.build_receipt("rrnewton/hermit", "a" * 40, row_b, durable)
            self.assertNotEqual(receipt_a["run_id"], receipt_b["run_id"])
            self.assertTrue(receipt_a["run_id"].endswith("@host-a"))
            self.assertTrue(receipt_b["run_id"].endswith("@host-b"))

    def test_hostless_row_is_refused_for_publish(self):
        # The publish guard requires host present so a receipt can never be minted
        # with an identity that omits where it was produced.
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(Path(directory))
            del row["host"]
            with self.assertRaises(SystemExit):
                MODULE.qualifying_row(self.report(row), "a" * 40)

    def test_zero_executed_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                MODULE.qualifying_row(
                    self.report(self.row(Path(directory), executed=0), verdict="NOT-VALIDATED"),
                    "a" * 40,
                )

    def test_wrong_head_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                MODULE.qualifying_row(self.report(self.row(Path(directory))), "b" * 40)

    def test_missing_binding_is_not_extractable_when_verifier_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(Path(directory))
            del row["reverie_binding"]
            with self.assertRaises(SystemExit):
                MODULE.qualifying_row(
                    self.report(row, verdict="NOT-VALIDATED"), "a" * 40
                )


if __name__ == "__main__":
    unittest.main()
