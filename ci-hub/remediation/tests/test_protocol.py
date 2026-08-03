from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REMEDIATION = Path(__file__).resolve().parents[1]
HISTORY = REMEDIATION.parents[0] / "history"
sys.path.insert(0, str(REMEDIATION))
sys.path.insert(0, str(HISTORY))

import obligations
import protocol

SHA = "a" * 40
NEXT_SHA = "b" * 40


class ProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / "obligations.jsonl"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self) -> dict:
        return obligations.create_obligation(
            repo="rrnewton/hermit",
            landed_sha=SHA,
            land_mode="speculative",
            actor="test",
            obligation_id="test-obligation",
            path=self.store,
        )

    def transition(self, patch: dict) -> dict:
        return obligations.transition(
            "test-obligation", "test-transition", patch, self.store
        )

    def test_github_parser_requires_exact_sha_and_workflow(self) -> None:
        payload = [
            {"databaseId": 1, "headSha": SHA, "workflowName": protocol.DEFAULT_WORKFLOW, "createdAt": "2026-08-03T01:00:00Z"},
            {"databaseId": 2, "headSha": NEXT_SHA, "workflowName": protocol.DEFAULT_WORKFLOW, "createdAt": "2026-08-03T02:00:00Z"},
            {"databaseId": 3, "headSha": SHA, "workflowName": "Docs", "createdAt": "2026-08-03T03:00:00Z"},
        ]
        runs = protocol._parse_github_runs(json.dumps(payload), SHA)
        self.assertEqual([run["databaseId"] for run in runs], [1])

    def test_estimate_uses_only_recorded_history(self) -> None:
        ledger = self.root / "ledger.jsonl"
        ledger.write_text(
            "\n".join(
                json.dumps({"profile": "full", "real_seconds": wall, "user_seconds": cpu, "sys_seconds": 1})
                for wall, cpu in ((100, 200), (5000, 9000), (2000, 8000))
            )
            + "\n"
        )
        estimate = protocol.estimate_local_validate_cost(ledger)
        self.assertEqual(estimate["kind"], "derived")
        self.assertEqual(estimate["wall_seconds"], 5000)
        self.assertEqual(estimate["cpu_seconds"], 9001)
        self.assertIn("last 3 usable", estimate["basis"])

    def test_estimate_is_unknown_without_usable_history(self) -> None:
        estimate = protocol.estimate_local_validate_cost(self.root / "missing.jsonl")
        self.assertEqual(estimate["kind"], "unknown")
        self.assertIsNone(estimate["wall_seconds"])
        self.assertIsNone(estimate["cpu_seconds"])
        self.assertTrue(estimate["basis"].startswith("not measured:"))

    def test_first_failure_immediately_requires_revert_at_tip(self) -> None:
        self.create()
        self.transition(
            {
                "local": {
                    "state": "red",
                    "finished_at": "2026-08-03T01:00:00Z",
                    "exit_code": 1,
                    "log_path": "/tmp/local.log",
                },
                "github": {"state": "running"},
            }
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            record = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        self.assertEqual(record["overall_state"], "remediation_required")
        self.assertEqual(record["recommendation"]["action"], "revert")
        self.assertEqual(record["alert"]["state"], "raised")
        self.assertIn("HARD WARNING", stderr.getvalue())

    def test_failure_recommends_fix_forward_after_main_advances(self) -> None:
        self.create()
        self.transition(
            {"github": {"state": "red", "finished_at": "2026-08-03T01:00:00Z"}}
        )
        with redirect_stderr(io.StringIO()):
            record = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=NEXT_SHA
            )
        self.assertEqual(record["recommendation"]["action"], "fix-forward")

    def test_two_green_verifiers_satisfy_obligation(self) -> None:
        self.create()
        self.transition({"local": {"state": "green"}, "github": {"state": "green"}})
        record = protocol.evaluate_obligation(
            "test-obligation", store_path=self.store, main_sha=SHA
        )
        self.assertEqual(record["overall_state"], "satisfied")
        self.assertIsNotNone(record["satisfied_at"])

    def test_health_codes_distinguish_open_and_remediation(self) -> None:
        self.create()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                protocol.print_status(self.store, include_closed=False, json_output=False, gate=True),
                1,
            )
        self.transition({"local": {"state": "red", "exit_code": 1}})
        with redirect_stderr(io.StringIO()):
            protocol.evaluate_obligation("test-obligation", store_path=self.store, main_sha=SHA)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                protocol.print_status(self.store, include_closed=False, json_output=False, gate=True),
                2,
            )
        self.assertIn("state=remediation-required", output.getvalue())

    def test_local_run_persists_tool_cost_payload(self) -> None:
        self.create()
        workspace = self.root / "ignored/ci-hub/obligations/test-obligation"
        cost_path = workspace / "cost.json"
        log_path = workspace / "local.log"
        estimate = {
            "kind": "derived",
            "wall_seconds": 1800.0,
            "cpu_seconds": 7200.0,
            "basis": "derived from test fixture history, n=3",
        }
        self.transition(
            {
                "local": {
                    "state": "running",
                    "log_path": str(log_path),
                    "cost": {"estimate": estimate, "actual": None, "record_path": str(cost_path)},
                }
            }
        )

        def fake_run(command, **_kwargs):
            command = list(command)
            if command[:2] == ["git", "clone"]:
                (self.root / "ignored/ci-hub/obligations/test-obligation/hermit").mkdir(parents=True)
            if "rev-parse" in command:
                return subprocess.CompletedProcess(command, 0, stdout=SHA + "\n", stderr="")
            if "tool-cost" in command[0]:
                payload = {
                    "schema_version": 1,
                    "tool": "speculative-land/local-validate",
                    "estimate": estimate,
                    "actual": {
                        "wall_seconds": 12.5,
                        "cpu_seconds": 20.0,
                        "cpu_user_seconds": 15.0,
                        "cpu_system_seconds": 5.0,
                        "exit": "0",
                    },
                }
                cost_path.parent.mkdir(parents=True, exist_ok=True)
                cost_path.write_text(json.dumps(payload))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        source = self.root / "source"
        source.mkdir()
        with mock.patch.object(protocol, "ROOT", self.root), mock.patch.object(
            protocol, "_run", side_effect=fake_run
        ):
            result = protocol._local_run("test-obligation", source, self.store)
        self.assertEqual(result, 0)
        record = obligations.get_record("test-obligation", self.store)
        self.assertEqual(record["local"]["cost"]["actual"]["cpu_seconds"], 20.0)
        self.assertEqual(record["local"]["cost"]["record_path"], str(cost_path))


if __name__ == "__main__":
    unittest.main()
