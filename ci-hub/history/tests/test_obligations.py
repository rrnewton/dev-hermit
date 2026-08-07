from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HISTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HISTORY))

import obligations

SHA = "a" * 40
POLICY = {
    "schema_version": 2,
    "repo": "rrnewton/hermit",
    "github": {
        "required_jobs": [
            {
                "workflow_file": ".github/workflows/ci-portable.yml",
                "workflow_name": "CI (GitHub-managed portable)",
                "job_name": "Regular tests (GitHub-managed portable)",
            },
            {
                "workflow_file": ".github/workflows/ci-privileged.yml",
                "workflow_name": "CI (privileged)",
                "job_name": "Privileged capability and E2E tests",
            },
        ],
        "required_positive_count": 2,
    },
}


class ObligationStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Path(self.temporary.name) / "obligations.jsonl"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self) -> dict:
        return obligations.create_obligation(
            repo="rrnewton/hermit",
            landed_sha=SHA,
            land_mode="admin",
            verification_policy=POLICY,
            actor="test",
            obligation_id="test-obligation",
            path=self.store,
        )

    def test_create_has_join_keys_and_cost_slots(self) -> None:
        record = self.create()
        self.assertEqual(record["landed_sha"], SHA)
        self.assertEqual(record["repo"], "rrnewton/hermit")
        self.assertEqual(record["overall_state"], "open")
        self.assertEqual(record["verification_policy"], POLICY)
        self.assertEqual(record["local"]["cost"]["actual"], None)
        self.assertEqual(record["github"]["run_ids"], [])

    def test_transitions_append_full_latest_projection(self) -> None:
        self.create()
        updated = obligations.transition(
            "test-obligation",
            "github-observed",
            {"github": {"state": "running", "run_ids": [123]}},
            self.store,
        )
        self.assertEqual(updated["github"]["run_ids"], [123])
        self.assertEqual(updated["local"]["state"], "not_started")
        lines = [json.loads(line) for line in self.store.read_text().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertNotEqual(lines[0]["event_id"], lines[1]["event_id"])
        self.assertEqual(obligations.get_record("test-obligation", self.store), updated)

    def test_compare_and_append_allows_only_one_launch_claim(self) -> None:
        opened = self.create()
        first = obligations.transition_if_matches(
            "test-obligation",
            "launch-claimed",
            {"launch": {"state": "launching", "token": "winner"}},
            {"event_id": opened["event_id"]},
            self.store,
        )
        self.assertIsNotNone(first)
        lost = obligations.transition_if_matches(
            "test-obligation",
            "launch-claimed",
            {"launch": {"state": "launching", "token": "loser"}},
            {"event_id": opened["event_id"]},
            self.store,
        )
        self.assertIsNone(lost)
        latest = obligations.get_record("test-obligation", self.store)
        self.assertEqual(latest["launch"]["token"], "winner")

    def test_duplicate_open_sha_is_rejected(self) -> None:
        record = self.create()
        with self.assertRaises(obligations.DuplicateOpenObligation) as context:
            obligations.create_obligation(
                repo="rrnewton/hermit",
                landed_sha=SHA,
                land_mode="speculative",
                path=self.store,
            )
        self.assertEqual(context.exception.record["obligation_id"], record["obligation_id"])

    def test_closed_sha_can_open_a_new_obligation(self) -> None:
        self.create()
        obligations.transition(
            "test-obligation", "satisfied", {"overall_state": "satisfied"}, self.store
        )
        reopened = obligations.create_obligation(
            repo="rrnewton/hermit",
            landed_sha=SHA,
            land_mode="speculative",
            obligation_id="second-obligation",
            path=self.store,
        )
        self.assertEqual(reopened["overall_state"], "open")
        self.assertEqual([r["obligation_id"] for r in obligations.unresolved_records(self.store)], ["second-obligation"])

    def test_identity_is_immutable(self) -> None:
        self.create()
        with self.assertRaises(obligations.StoreError):
            obligations.transition(
                "test-obligation", "bad", {"landed_sha": "b" * 40}, self.store
            )

    def test_bound_policy_is_in_initial_event_and_immutable(self) -> None:
        self.create()
        opened = json.loads(self.store.read_text().splitlines()[0])
        self.assertEqual(opened["event_type"], "opened")
        self.assertEqual(opened["verification_policy"], POLICY)
        with self.assertRaisesRegex(obligations.StoreError, "bound verification"):
            obligations.transition(
                "test-obligation",
                "bad-policy",
                {"verification_policy": {**POLICY, "schema_version": 3}},
                self.store,
            )

    def test_legacy_policy_can_be_bound_exactly_once(self) -> None:
        obligations.create_obligation(
            repo="rrnewton/hermit",
            landed_sha=SHA,
            land_mode="admin",
            actor="test",
            obligation_id="legacy",
            path=self.store,
        )
        bound = obligations.transition(
            "legacy",
            "verification-policy-bound",
            {"verification_policy": POLICY},
            self.store,
        )
        self.assertEqual(bound["verification_policy"], POLICY)
        repeated = obligations.transition(
            "legacy",
            "verification-policy-bound",
            {"verification_policy": POLICY},
            self.store,
        )
        self.assertEqual(repeated["verification_policy"], POLICY)
        with self.assertRaisesRegex(obligations.StoreError, "bound verification"):
            obligations.transition(
                "legacy",
                "policy-rebound",
                {"verification_policy": {**POLICY, "schema_version": 3}},
                self.store,
            )


if __name__ == "__main__":
    unittest.main()
