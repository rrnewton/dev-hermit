from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

REMEDIATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REMEDIATION))

import land_and_arm

SHA = "a" * 40


class LandAndArmTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.intent_dir = self.root / "intents"
        self.store = self.root / "obligations.jsonl"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self) -> Namespace:
        return Namespace(
            repo="rrnewton/hermit",
            pr=123,
            source=self.root,
            land_mode="admin",
            actor="test-lander",
            command_timeout=5,
            observe_timeout=5,
            github_wait_seconds=5,
            poll_seconds=1,
            store=self.store,
            intent_dir=self.intent_dir,
            command=["--", "/bin/true"],
        )

    def intent(self) -> dict:
        path = land_and_arm._intent_path(self.intent_dir, "rrnewton/hermit", 123)
        return json.loads(path.read_text())

    def test_success_records_intent_before_land_and_arms_merged_sha(self) -> None:
        observed_states: list[str] = []

        def run_command(_command, _timeout):
            observed_states.append(self.intent()["state"])
            return 0

        with mock.patch.object(
            land_and_arm, "_run_land_command", side_effect=run_command
        ), mock.patch.object(
            land_and_arm, "observe_merged_sha", return_value=SHA
        ), mock.patch.object(
            land_and_arm, "arm_sha", return_value=(0, "obligation-1")
        ):
            self.assertEqual(land_and_arm.run(self.args()), 0)
        self.assertEqual(observed_states, ["prepared"])
        self.assertEqual(self.intent()["state"], "armed")
        self.assertEqual(self.intent()["landed_sha"], SHA)
        self.assertEqual(self.intent()["obligation_id"], "obligation-1")

    def test_failed_land_is_not_armed(self) -> None:
        with mock.patch.object(
            land_and_arm, "_run_land_command", return_value=7
        ), mock.patch.object(
            land_and_arm, "_pr_state", return_value=("OPEN", None)
        ), mock.patch.object(
            land_and_arm, "arm_sha"
        ) as arm:
            self.assertEqual(land_and_arm.run(self.args()), 7)
        arm.assert_not_called()
        self.assertEqual(self.intent()["state"], "land-command-failed")

    def test_nonzero_merge_command_still_arms_when_pr_actually_merged(self) -> None:
        with mock.patch.object(
            land_and_arm, "_run_land_command", return_value=1
        ), mock.patch.object(
            land_and_arm, "_pr_state", return_value=("MERGED", SHA)
        ), mock.patch.object(
            land_and_arm, "arm_sha", return_value=(0, "obligation-after-race")
        ):
            self.assertEqual(land_and_arm.run(self.args()), 0)
        self.assertEqual(self.intent()["state"], "armed")
        self.assertEqual(self.intent()["obligation_id"], "obligation-after-race")

    def test_recovery_arms_a_merge_left_between_merge_and_arm(self) -> None:
        args = self.args()
        intent = land_and_arm._new_intent(args, ["/bin/true"])
        path = land_and_arm._intent_path(self.intent_dir, args.repo, args.pr)
        land_and_arm._atomic_json(path, intent)
        with mock.patch.object(
            land_and_arm, "_pr_state", return_value=("MERGED", SHA)
        ), mock.patch.object(
            land_and_arm, "arm_sha", return_value=(0, "obligation-recovered")
        ):
            self.assertEqual(land_and_arm.recover_intent(path, observe_timeout=1), 0)
        recovered = self.intent()
        self.assertEqual(recovered["state"], "armed")
        self.assertEqual(recovered["obligation_id"], "obligation-recovered")

    def test_prepare_then_complete_supports_self_wrapped_lander(self) -> None:
        args = self.args()
        with mock.patch.object(
            land_and_arm, "observe_merged_sha", return_value=SHA
        ), mock.patch.object(
            land_and_arm, "arm_sha", return_value=(0, "obligation-external")
        ):
            self.assertEqual(land_and_arm.prepare(args), 0)
            complete_args = Namespace(
                repo=args.repo,
                pr=args.pr,
                intent_dir=args.intent_dir,
                observe_timeout=1,
            )
            self.assertEqual(land_and_arm.complete(complete_args), 0)
        self.assertEqual(self.intent()["state"], "armed")
        self.assertEqual(self.intent()["command"], ["external-bounded-lander"])


if __name__ == "__main__":
    unittest.main()
