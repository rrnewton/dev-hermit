#!/usr/bin/env python3

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("publish_receipt.py")
SPEC = importlib.util.spec_from_file_location("publish_receipt", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReceiptTests(unittest.TestCase):
    SHA = "a" * 40

    def row(self, root: Path, *, weak: bool = False):
        log = root / "validate.log"
        log.write_text("running 12 tests\ntest result: ok. 12 passed; 0 failed\n")
        return {
            "schema_version": 4,
            "started_at": "2026-08-04T12:00:00Z",
            "finished_at": ("2026-08-04T12:02:00Z" if weak else "2026-08-04T12:01:00Z"),
            "commit": self.SHA,
            "profile": "full",
            "selection_mode": "full",
            "commit_anchored": True,
            "tree_dirty": False,
            "result": "pass",
            "raw_result": "pass",
            "exit_code": 0,
            "checks": 0 if weak else 2,
            "gates_run": 0 if weak else 2,
            "gates_expected": 6 if weak else 2,
            "gates": (
                []
                if weak
                else [
                    {"name": "fmt", "result": "pass", "exit_code": 0},
                    {"name": "test", "result": "pass", "exit_code": 0},
                ]
            ),
            "failures": 0,
            "executed_tests": 1 if weak else 12,
            "filtered_tests": 0 if weak else 3,
            "log_file": str(log),
        }

    @staticmethod
    def canonical_row(row: dict) -> bytes:
        return json.dumps(row, separators=(",", ":")).encode()

    def args(self, root: Path, digest: str, *, dry_run: bool = True):
        return argparse.Namespace(
            repo="rrnewton/hermit",
            sha=self.SHA,
            ledger=root / "ledger.jsonl",
            selected_receipt_sha256=digest,
            canonicalization=MODULE.RECEIPT_CANONICALIZATION,
            receipt_repo=MODULE.RECEIPT_REPO,
            receipt_branch=MODULE.RECEIPT_BRANCH,
            dry_run=dry_run,
        )

    def test_selected_record_and_artifact_bind_exact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self.row(root)
            canonical_row = self.canonical_row(row)
            digest = hashlib.sha256(canonical_row).hexdigest()
            selected = MODULE.selected_record(
                canonical_row,
                sha=self.SHA,
                expected_digest=digest,
                canonicalization=MODULE.RECEIPT_CANONICALIZATION,
            )
            durable = MODULE.preserve_log(root / "ledger.jsonl", self.SHA, selected)
            receipt, body, artifact_digest = MODULE.build_receipt(
                "rrnewton/hermit",
                self.SHA,
                selected,
                durable,
                selected_digest=digest,
                canonicalization=MODULE.RECEIPT_CANONICALIZATION,
            )
            self.assertEqual(receipt["ledger_record"]["executed_tests"], 12)
            self.assertEqual(receipt["commit"], self.SHA)
            self.assertEqual(receipt["selected_receipt_identity"]["digest"], digest)
            self.assertEqual(len(receipt["log_sha256"]), 64)
            self.assertTrue(Path(receipt["durable_log_file"]).is_file())
            self.assertEqual(hashlib.sha256(body).hexdigest(), artifact_digest)

    def test_tampered_or_mismatched_selected_digest_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row(Path(directory))
            canonical_row = self.canonical_row(row)
            digest = hashlib.sha256(canonical_row).hexdigest()
            with self.assertRaises(SystemExit):
                MODULE.selected_record(
                    canonical_row,
                    sha=self.SHA,
                    expected_digest="0" * 64,
                    canonicalization=MODULE.RECEIPT_CANONICALIZATION,
                )
            tampered = canonical_row.replace(
                b'"executed_tests":12', b'"executed_tests":1'
            )
            with self.assertRaises(SystemExit):
                MODULE.selected_record(
                    tampered,
                    sha=self.SHA,
                    expected_digest=digest,
                    canonicalization=MODULE.RECEIPT_CANONICALIZATION,
                )

    def test_strong_plus_newer_weak_publishes_selected_strong_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            strong = self.row(root)
            weak = self.row(root, weak=True)
            (root / "ledger.jsonl").write_text(
                json.dumps(strong) + "\n" + json.dumps(weak) + "\n"
            )
            canonical_strong = self.canonical_row(strong)
            digest = hashlib.sha256(canonical_strong).hexdigest()
            report = MODULE.execute(self.args(root, digest), canonical_strong)
            artifact = json.loads(report["artifact_body"])
            self.assertEqual(report["action"], "would-publish")
            self.assertEqual(report["receipt_identity_sha256"], digest)
            self.assertEqual(report["receipt_repository"], MODULE.RECEIPT_REPO)
            self.assertEqual(report["receipt_branch"], MODULE.RECEIPT_BRANCH)
            self.assertTrue(
                report["path"].endswith(f"/{report['artifact_sha256']}.json")
            )
            self.assertEqual(artifact["ledger_record"], strong)
            self.assertNotEqual(artifact["ledger_record"], weak)
            self.assertEqual(
                hashlib.sha256(report["artifact_body"].encode()).hexdigest(),
                report["artifact_sha256"],
            )

    def test_direct_execution_can_publish_but_cannot_bind_a_pr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self.row(root)
            canonical_row = self.canonical_row(row)
            digest = hashlib.sha256(canonical_row).hexdigest()
            with (
                mock.patch.object(MODULE, "publish", return_value="e" * 40) as publish,
                mock.patch.object(MODULE, "gh") as gh,
            ):
                report = MODULE.execute(
                    self.args(root, digest, dry_run=False), canonical_row
                )
            publish.assert_called_once()
            gh.assert_not_called()
            self.assertEqual(report["action"], "published")
            self.assertEqual(report["receipt_commit"], "e" * 40)

        source = SCRIPT.read_text()
        self.assertNotIn("qualifying_row", source)
        self.assertNotIn("read_rows", source)
        self.assertNotIn("--add-label", source)
        self.assertNotIn('"pr", "comment"', source)
        self.assertFalse(hasattr(MODULE, "bind_pr"))


if __name__ == "__main__":
    unittest.main()
