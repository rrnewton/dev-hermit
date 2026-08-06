#!/usr/bin/env python3

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("publish_receipt.py")
SPEC = importlib.util.spec_from_file_location("publish_receipt", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_producer_checkout(root: Path) -> tuple[Path, str]:
    """A REAL one-commit git repo carrying the registered producer files.

    The producer blobs are resolved with `git rev-parse <sha>:<path>`, so the
    mint-side tests use a genuine repository and a genuine commit sha rather
    than a stubbed resolver.  Deliberately no test-only override hook in
    publish_receipt.py: an env var that let a caller declare its own producer
    identity would be a forgery path straight through the guard this adds.
    """
    repo = root / "producer-checkout"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "validate.sh").write_text("#!/usr/bin/env bash\necho validate\n")
    (repo / ".github" / "workflows" / "ci-portable.yml").write_text("name: CI\n")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    }
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env=env
    )
    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "producer fixture")
    sha = run("rev-parse", "HEAD").stdout.strip()
    return repo, sha


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
            "host": "test-host",
            "log_file": str(log),
        }

    def test_counted_exact_head_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, sha = make_producer_checkout(Path(directory))
            row = self.row(Path(directory))
            row["commit"] = sha
            row["cwd"] = str(repo)
            selected = MODULE.qualifying_row([row], sha)
            durable = MODULE.preserve_log(Path(directory) / "ledger.jsonl", sha, selected)
            receipt, body, digest = MODULE.build_receipt(
                "rrnewton/hermit", sha, selected, durable
            )
            self.assertEqual(receipt["ledger_record"]["executed_tests"], 12)
            self.assertEqual(receipt["commit"], sha)
            self.assertEqual(len(receipt["log_sha256"]), 64)
            self.assertTrue(Path(receipt["durable_log_file"]).is_file())
            self.assertEqual(MODULE.hashlib.sha256(body).hexdigest(), digest)

    def test_receipt_carries_the_producing_definition(self):
        # Mint-side half of the producer binding: the receipt must name WHICH
        # check definition produced it, and each blob must be the one git
        # resolves at the validated commit -- not a value the producer chose.
        with tempfile.TemporaryDirectory() as directory:
            repo, sha = make_producer_checkout(Path(directory))
            row = self.row(Path(directory))
            row["commit"] = sha
            row["cwd"] = str(repo)
            durable = Path(directory) / "durable.log"
            durable.write_text("log\n")
            receipt, _, _ = MODULE.build_receipt("rrnewton/hermit", sha, row, durable)
            definition = receipt["producer"]["definition"]
            self.assertEqual(sorted(definition), sorted(MODULE.registered_producer()))
            for relative, blob in definition.items():
                expected = subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", f"{sha}:{relative}"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                self.assertEqual(blob, expected, relative)
                self.assertRegex(blob, r"^[0-9a-f]{40}$")

    def test_receipt_without_resolvable_producer_is_refused(self):
        # Fail closed at MINT time too: a receipt that cannot name its producer
        # must not be created, because a producer-less receipt is precisely what
        # the consumer refuses. Two ways to be unresolvable, both fatal.
        with tempfile.TemporaryDirectory() as directory:
            repo, sha = make_producer_checkout(Path(directory))
            durable = Path(directory) / "durable.log"
            durable.write_text("log\n")

            no_cwd = self.row(Path(directory))
            no_cwd["commit"] = sha
            with self.assertRaises(SystemExit):
                MODULE.build_receipt("rrnewton/hermit", sha, no_cwd, durable)

            # cwd is a real repo, but the commit is not in it -- the blobs of a
            # commit this checkout never had cannot be vouched for.
            foreign = self.row(Path(directory))
            foreign["commit"] = "a" * 40
            foreign["cwd"] = str(repo)
            with self.assertRaises(SystemExit):
                MODULE.build_receipt("rrnewton/hermit", "a" * 40, foreign, durable)

    def test_run_id_binds_host_same_sha_and_started_at(self):
        # Host-in-identity (Req2): two runs of the SAME sha at the SAME started_at
        # on DIFFERENT hosts must mint DISTINCT run_ids -- before host was in the
        # identity there was no collision guard between them at all.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, sha = make_producer_checkout(root)
            durable = root / "durable.log"
            durable.write_text("log\n")
            row_a = self.row(root)
            row_a["commit"] = sha
            row_a["cwd"] = str(repo)
            row_a["host"] = "host-a"
            row_b = dict(row_a)
            row_b["host"] = "host-b"
            self.assertEqual(row_a["started_at"], row_b["started_at"])
            receipt_a, _, _ = MODULE.build_receipt("rrnewton/hermit", sha, row_a, durable)
            receipt_b, _, _ = MODULE.build_receipt("rrnewton/hermit", sha, row_b, durable)
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
                MODULE.qualifying_row([row], "a" * 40)

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
            # PRE-EXISTING BREAKAGE, fixed in passing: 19a219f moved the
            # count-schema boundary into the shared qualifying-receipt predicate
            # and deleted publish_receipt.COUNTS_SCHEMA, but left this reference
            # behind, so this test had been erroring (not failing) ever since.
            # Read it from the one canonical source, as that refactor intended.
            row["schema_version"] = MODULE.qualifying_receipt.active()["counts_schema"]
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
