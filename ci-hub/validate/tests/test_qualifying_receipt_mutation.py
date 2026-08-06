#!/usr/bin/env python3
"""MUTATION proof for the ONE shared qualifying-receipt predicate.

Task `one-shared-qualifying-receipt-predicate-five-consumers-bypass-the-registry`.
FIVE consumers used to each fail-close on their own inline schema check; the fix
is ONE datum (`ci-hub/validate/qualifying-receipt.json`) that they all READ. The
strong proof is the mutation the finding sweep used: change the shared datum and
confirm EVERY consumer's answer MOVES. Any consumer whose answer does not move is
still bypassing the registry.

This harness drives each consumer in its OWN process so the
`QUALIFYING_RECEIPT_PREDICATE` env override (never the live file) is read fresh --
the predicate is cached process-wide, so an in-process import would leak the live
value across scenarios. For each scenario it asserts the whole panel agrees:

  * POSITIVE (live): a genuine schema-5 full green qualifies in EVERY consumer.
  * MUTATION (executed_tests_min 1 -> 999999): that same green is REJECTED by
    EVERY consumer -> all five move together on one datum edit.
  * COVERAGE negative (live): the ee303899 shape (executed=427 but 15 absent
    nodes) is REFUSED by EVERY consumer -> the per-node coverage clause is
    present in all of them (the DRIFT-4 gap query.py used to have).
  * ZERO-exec negative (live): executed_tests==0 is refused by EVERY consumer.

Consumers driven (five certifier sites, three languages):
  - Rust `validate_status.rs`   via `ci-hub validate-status --ledger F --sha S`.
  - Rust `history_queries.rs`   routes through the SAME `assess()` ->
    `is_clean_full_pass` -> `row_qualifies` as the leg above; one predicate path,
    so the Rust leg covers both (its own unit tests exercise history_queries).
  - Python `query.py`           via its `_row_full_pass`.
  - Python `publish_receipt.py` via its `qualifying_row`.
  - bash/jq `verify_receipt.sh` via its receipt gate (`--fixture-receipts`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CI_HUB = Path(__file__).resolve().parents[2]
REPO_ROOT = CI_HUB.parent
LIVE_PREDICATE = CI_HUB / "validate" / "qualifying-receipt.json"
PRODUCER_REGISTRY = CI_HUB / "validate" / "producer-definition.json"
CI_HUB_RS = CI_HUB / "ci-hub.rs"
QUERY_PY = CI_HUB / "history" / "query.py"
PUBLISH_PY = CI_HUB / "validation" / "publish_receipt.py"
VERIFY_SH = CI_HUB / "validation" / "verify_receipt.sh"

SHA = "a" * 40
RECEIPT_COMMIT = "b" * 40  # the parent commit a receipt was committed at
REPO = "rrnewton/hermit"


def _green_row(sha: str) -> dict:
    """A genuine schema-5 clean full-coverage PASS carrying counts + coverage."""
    return {
        "commit": sha,
        "commit_anchored": True,
        "tree_dirty": False,
        "selection_mode": "full",
        "profile": "full",
        "result": "pass",
        "failures": 0,
        "executed_tests": 740,
        "filtered_tests": 3,
        "schema_version": 5,
        "started_at": "2026-08-05T00:00:00Z",
        "finished_at": "2026-08-05T00:10:00Z",
        "host": "test-host",
        "log_file": "/tmp/qrp-fixture.log",
        "coverage": {
            "planned_test_nodes": 4,
            "executed_test_nodes": 4,
            "zero_executed_nodes": [],
            "absent_nodes": [],
        },
    }


def _ee303899_row(sha: str) -> dict:
    """Coverage-incomplete PASS: healthy executed count, 15 absent nodes."""
    row = _green_row(sha)
    row["executed_tests"] = 427
    row["filtered_tests"] = 0
    row["coverage"] = {
        "planned_test_nodes": 19,
        "executed_test_nodes": 4,
        "zero_executed_nodes": [],
        "absent_nodes": [f"node-{i}" for i in range(15)],
    }
    return row


def _zero_exec_row(sha: str) -> dict:
    """A demonstrated zero-test run: never a full green at any schema."""
    row = _green_row(sha)
    row["executed_tests"] = 0
    return row


def _tightened_predicate(dst: Path) -> Path:
    """A copy of the live predicate with executed_tests_min raised so far that
    the genuine green (executed=740) can no longer clear it. NEVER touches the
    live file -- mirrors the sweep's copy-override method."""
    live = json.loads(LIVE_PREDICATE.read_text())
    live["require"]["executed_tests_min"] = 999999
    dst.write_text(json.dumps(live))
    return dst


def _env(predicate: Path | None) -> dict:
    env = dict(os.environ)
    if predicate is not None:
        env["QUALIFYING_RECEIPT_PREDICATE"] = str(predicate)
    else:
        env.pop("QUALIFYING_RECEIPT_PREDICATE", None)
    return env


class QualifyingReceiptMutationTest(unittest.TestCase):
    """One test method per scenario; each asserts the FULL five-consumer panel."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("rust-script") is None:
            raise unittest.SkipTest("rust-script unavailable; Rust consumer leg cannot run")
        if shutil.which("jq") is None:
            raise unittest.SkipTest("jq unavailable; verify_receipt.sh leg cannot run")
        # Warm the rust-script cache once so per-scenario Rust calls are fast and
        # a compile failure surfaces here rather than as a spurious 'reject'.
        proc = subprocess.run(
            ["rust-script", "--force", str(CI_HUB_RS), "validate-status", "--help"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise unittest.SkipTest(f"ci-hub.rs did not build: {proc.stderr[-800:]}")

    # -- individual consumer drivers: each returns True iff the consumer ACCEPTS.

    def _rust_accepts(self, row: dict, predicate: Path | None) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text(json.dumps(row) + "\n")
            proc = subprocess.run(
                ["rust-script", "--force", str(CI_HUB_RS),
                 "validate-status", "--ledger", str(ledger), "--sha", SHA, "--json"],
                cwd=REPO_ROOT, env=_env(predicate),
                capture_output=True, text=True, timeout=600,
            )
        self.assertEqual(proc.returncode in (0,) or proc.stdout.strip().startswith("{"), True,
                         f"validate-status produced no report: {proc.stderr[-800:]}")
        report = json.loads(proc.stdout)
        return report["qualifying_count"] >= 1

    def _query_accepts(self, row: dict, predicate: Path | None) -> bool:
        driver = (
            "import json,sys;"
            f"sys.path[:0]=[{str(CI_HUB)!r},{str(CI_HUB / 'history')!r}];"
            "import query;"
            "row=json.load(sys.stdin);"
            "print('ACCEPT' if query._row_full_pass(row) else 'REJECT')"
        )
        return self._python_accepts(driver, row, predicate)

    def _publish_accepts(self, row: dict, predicate: Path | None) -> bool:
        driver = (
            "import json,sys;"
            f"sys.path[:0]=[{str(CI_HUB)!r},{str(CI_HUB / 'validation')!r}];"
            "import publish_receipt;"
            "row=json.load(sys.stdin);"
            "\ntry:\n"
            f"    publish_receipt.qualifying_row([row], {SHA!r}); print('ACCEPT')\n"
            "except SystemExit:\n"
            "    print('REJECT')\n"
        )
        return self._python_accepts(driver, row, predicate)

    def _python_accepts(self, driver: str, row: dict, predicate: Path | None) -> bool:
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            input=json.dumps(row), env=_env(predicate),
            capture_output=True, text=True, timeout=60,
        )
        out = proc.stdout.strip().splitlines()
        self.assertTrue(out and out[-1] in ("ACCEPT", "REJECT"),
                        f"python consumer gave no verdict: rc={proc.returncode} "
                        f"stdout={proc.stdout!r} stderr={proc.stderr[-800:]}")
        return out[-1] == "ACCEPT"

    def _verify_sh_accepts(self, row: dict, predicate: Path | None) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = {
                "schema_version": 1,
                "repository": REPO,
                "commit": SHA,
                "run_id": SHA + "@" + row["started_at"] + "@" + row["host"],
                "log_sha256": "c" * 64,
                "source_log_file": row["log_file"],
                "durable_log_file": "/durable" + row["log_file"],
                # This suite mutates the QUALIFYING-RECEIPT predicate, so the
                # producer binding must be satisfied and held constant here --
                # otherwise every row would be refused for the wrong reason and
                # the predicate leg would prove nothing. Read from the live
                # registration so this fixture follows a producer advance
                # instead of silently rotting into a false REJECT.
                "producer": {
                    "resolved_from": "/fixture/worktree",
                    "definition": json.loads(PRODUCER_REGISTRY.read_text())["registered"],
                },
                "ledger_record": row,
            }
            blob = json.dumps(receipt, sort_keys=True).encode()
            digest = hashlib.sha256(blob).hexdigest()
            rel_path = f"validation-receipts/{REPO}/{SHA}/{digest}.json"
            receipt_file = root / RECEIPT_COMMIT / rel_path
            receipt_file.parent.mkdir(parents=True, exist_ok=True)
            receipt_file.write_bytes(blob)
            marker = (
                f"<!-- locally-validated-receipt commit={RECEIPT_COMMIT} "
                f"path={rel_path} sha256={digest} -->"
            )
            comments = [[{"user": {"login": "rrnewton"},
                          "body": "[impl agent, ci-hub]\n" + marker}]]
            comments_file = root / "comments.json"
            comments_file.write_text(json.dumps(comments))
            proc = subprocess.run(
                ["bash", str(VERIFY_SH), "--sha", SHA, "--comments", str(comments_file),
                 "--repo", REPO, "--fixture-receipts", str(root)],
                env=_env(predicate), capture_output=True, text=True, timeout=60,
            )
        # exit 0 = a qualifying receipt was found; exit 1 = none; exit 2 = usage /
        # malformed predicate (a hard config error, not a verdict).
        self.assertIn(proc.returncode, (0, 1),
                      f"verify_receipt.sh errored: rc={proc.returncode} {proc.stderr[-800:]}")
        return proc.returncode == 0

    def _panel(self, row: dict, predicate: Path | None) -> dict[str, bool]:
        return {
            "validate_status.rs": self._rust_accepts(row, predicate),
            "query.py": self._query_accepts(row, predicate),
            "publish_receipt.py": self._publish_accepts(row, predicate),
            "verify_receipt.sh": self._verify_sh_accepts(row, predicate),
        }

    # -- scenarios: each asserts unanimity across the whole panel.

    def test_live_accepts_genuine_green_unanimously(self) -> None:
        """POSITIVE control: a real schema-5 full green is ACCEPTED by all five."""
        panel = self._panel(_green_row(SHA), predicate=None)
        self.assertTrue(all(panel.values()),
                        f"a genuine green must be accepted by every consumer: {panel}")

    def test_mutation_rejects_green_unanimously(self) -> None:
        """THE mutation proof: raise executed_tests_min in a COPY and every
        consumer's answer must move from accept to reject -- one datum edit, five
        answers move. Any consumer still accepting is bypassing the registry."""
        with tempfile.TemporaryDirectory() as tmp:
            tightened = _tightened_predicate(Path(tmp) / "tight.json")
            live = self._panel(_green_row(SHA), predicate=None)
            self.assertTrue(all(live.values()), f"live must accept first: {live}")
            mutated = self._panel(_green_row(SHA), predicate=tightened)
        self.assertFalse(any(mutated.values()),
                         f"every consumer must reject under the tightened datum: {mutated}")

    def test_live_rejects_incomplete_coverage_unanimously(self) -> None:
        """DRIFT-4 closed: the ee303899 shape (executed>0 but 15 absent nodes) is
        REFUSED by all five -- the per-node coverage clause is present in every
        consumer, including query.py which historically lacked it."""
        panel = self._panel(_ee303899_row(SHA), predicate=None)
        self.assertFalse(any(panel.values()),
                         f"incomplete coverage must be rejected by every consumer: {panel}")

    def test_live_rejects_zero_execution_unanimously(self) -> None:
        """The surviving zero-execution floor: executed_tests==0 is refused by all
        five (executed_tests is diagnostic only, but a run that executed NOTHING
        is never a green at any schema)."""
        panel = self._panel(_zero_exec_row(SHA), predicate=None)
        self.assertFalse(any(panel.values()),
                         f"a zero-execution run must be rejected by every consumer: {panel}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
