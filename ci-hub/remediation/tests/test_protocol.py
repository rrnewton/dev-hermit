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
            {
                "databaseId": 1,
                "headSha": SHA,
                "workflowName": protocol.DEFAULT_WORKFLOW,
                "createdAt": "2026-08-03T01:00:00Z",
            },
            {
                "databaseId": 2,
                "headSha": NEXT_SHA,
                "workflowName": protocol.DEFAULT_WORKFLOW,
                "createdAt": "2026-08-03T02:00:00Z",
            },
            {
                "databaseId": 3,
                "headSha": SHA,
                "workflowName": "Docs",
                "createdAt": "2026-08-03T03:00:00Z",
            },
        ]
        runs = protocol._parse_github_runs(json.dumps(payload), SHA)
        self.assertEqual([run["databaseId"] for run in runs], [1])

    def test_estimate_uses_only_recorded_history(self) -> None:
        ledger = self.root / "ledger.jsonl"
        ledger.write_text(
            "\n".join(
                json.dumps(
                    {
                        "profile": "full",
                        "real_seconds": wall,
                        "user_seconds": cpu,
                        "sys_seconds": 1,
                    }
                )
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

    def test_lone_local_red_is_provisional_not_an_immediate_revert(self) -> None:
        # Regression (task obligation-path-must-consume-no-result-taxonomy): a
        # single local red whose GitHub leg has not corroborated it once drove an
        # automated revert of a healthy main tip (e8a0d8d3); re-validation passed.
        # An uncorroborated, un-reproduced local red must NOT remediate.
        self.create()
        self.transition(
            {
                "local": {
                    "state": "red",
                    "finished_at": "2026-08-03T01:00:00Z",
                    "exit_code": 1,
                    "log_path": "/tmp/local.log",
                    "redispatch_count": 0,
                },
                "github": {"state": "running"},
            }
        )
        with redirect_stderr(io.StringIO()):
            record = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        self.assertNotEqual(record["overall_state"], "remediation_required")

    def test_reproduced_local_red_requires_revert_at_tip(self) -> None:
        # Once the local red survives the whole re-dispatch budget it is confirmed,
        # not a flake, and a still-at-tip failing land must revert.
        self.create()
        self.transition(
            {
                "local": {
                    "state": "red",
                    "finished_at": "2026-08-03T01:00:00Z",
                    "exit_code": 1,
                    "log_path": "/tmp/local.log",
                    "redispatch_count": protocol.DEFAULT_LOCAL_REDISPATCH_LIMIT,
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
        self.assertEqual(record["remediation"]["state"], "triggered")
        self.assertEqual(record["remediation"]["dispatch"]["target"], "hermit-lander")
        self.assertEqual(record["alert"]["state"], "pending")
        self.assertIn("HARD WARNING", stderr.getvalue())
        self.assertIn("REMEDIATION TRIGGERED", stderr.getvalue())

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
        self.assertEqual(record["remediation"]["state"], "triggered")

    def test_remediation_trigger_is_idempotent(self) -> None:
        self.create()
        # A corroborated GitHub red is remediation-ready without re-dispatch; a
        # lone local red is provisional (see the redispatch tests).
        self.transition({"github": {"state": "red"}})
        with redirect_stderr(io.StringIO()):
            first = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
            second = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        self.assertEqual(first["event_id"], second["event_id"])
        events = [json.loads(line) for line in self.store.read_text().splitlines()]
        self.assertEqual(
            sum(event["event_type"] == "remediation-triggered" for event in events), 1
        )

    def test_wake_delivery_is_unhandled_until_reader_acknowledges(self) -> None:
        self.create()
        self.transition({"github": {"state": "red"}})
        with redirect_stderr(io.StringIO()):
            protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        with redirect_stdout(io.StringIO()):
            protocol.record_wake_sent(
                store_path=self.store, target="hermit-lander", source="test-orc"
            )
        sent = obligations.get_record("test-obligation", self.store)
        dispatch = sent["remediation"]["dispatch"]
        self.assertEqual(dispatch["state"], "sent_unacknowledged")
        self.assertEqual(dispatch["wake_attempt"], 1)
        self.assertIsNone(dispatch["acknowledged_at"])

        with redirect_stdout(io.StringIO()):
            first = protocol.inherit_actionable(
                store_path=self.store, agent="hermit-lander", session="replacement-1"
            )
            second = protocol.inherit_actionable(
                store_path=self.store, agent="hermit-lander", session="replacement-1"
            )
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        acknowledged = obligations.get_record("test-obligation", self.store)
        dispatch = acknowledged["remediation"]["dispatch"]
        self.assertEqual(dispatch["state"], "acknowledged")
        self.assertEqual(dispatch["acknowledged_by"], "hermit-lander")
        self.assertEqual(dispatch["acknowledged_session"], "replacement-1")
        events = [json.loads(line) for line in self.store.read_text().splitlines()]
        self.assertEqual(
            sum(event["event_type"] == "remediation-inherited" for event in events), 1
        )

    def test_new_lander_session_rediscovers_acknowledged_obligation(self) -> None:
        self.create()
        self.transition({"github": {"state": "red"}})
        with redirect_stderr(io.StringIO()):
            protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=NEXT_SHA
            )
        with redirect_stdout(io.StringIO()):
            protocol.inherit_actionable(
                store_path=self.store, agent="hermit-lander", session="old-session"
            )
            protocol.inherit_actionable(
                store_path=self.store, agent="hermit-lander", session="fresh-session"
            )
        record = obligations.get_record("test-obligation", self.store)
        self.assertEqual(
            record["remediation"]["dispatch"]["acknowledged_session"],
            "fresh-session",
        )
        self.assertEqual(record["overall_state"], "remediation_required")

        # A later advisory wake must not turn already-handled durable work back
        # into an unhandled state.
        with redirect_stdout(io.StringIO()):
            protocol.record_wake_sent(
                store_path=self.store, target="hermit-lander", source="late-orc"
            )
        record = obligations.get_record("test-obligation", self.store)
        self.assertEqual(record["remediation"]["dispatch"]["state"], "acknowledged")
        self.assertEqual(
            record["remediation"]["dispatch"]["acknowledged_session"],
            "fresh-session",
        )

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
                protocol.print_status(
                    self.store,
                    include_closed=False,
                    json_output=False,
                    gate=True,
                    actionable_only=False,
                ),
                1,
            )
        self.transition({"github": {"state": "red"}})
        with redirect_stderr(io.StringIO()):
            protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                protocol.print_status(
                    self.store,
                    include_closed=False,
                    json_output=False,
                    gate=True,
                    actionable_only=True,
                ),
                2,
            )
        self.assertIn("state=remediation-required", output.getvalue())
        self.assertIn("sent_unacknowledged_count=0", output.getvalue())

    def test_empty_one_shot_watch_reports_a_domain_result(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                protocol.watch(
                    store_path=self.store,
                    obligation_id=None,
                    once=True,
                    poll_seconds=1,
                ),
                0,
            )
        self.assertEqual(
            output.getvalue().strip(),
            "WATCH OBLIGATIONS: checked=0 unresolved=0 remediation_required=0",
        )

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
                    "cost": {
                        "estimate": estimate,
                        "actual": None,
                        "record_path": str(cost_path),
                    },
                }
            }
        )

        def fake_run(command, **_kwargs):
            command = list(command)
            if command[:2] == ["git", "clone"]:
                (self.root / "ignored/ci-hub/obligations/test-obligation/hermit").mkdir(
                    parents=True
                )
            if "rev-parse" in command:
                return subprocess.CompletedProcess(
                    command, 0, stdout=SHA + "\n", stderr=""
                )
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


class GithubStateClassificationTest(unittest.TestCase):
    """A run conclusion is not a truth value: cancelled/absent != red."""

    def _state(self, status: str, conclusion: str) -> str:
        return protocol._github_state({"status": status, "conclusion": conclusion})

    def test_success_and_neutral_are_green(self) -> None:
        self.assertEqual(self._state("completed", "success"), "green")
        self.assertEqual(self._state("completed", "neutral"), "green")

    def test_genuine_failures_are_red(self) -> None:
        for conclusion in ("failure", "timed_out", "startup_failure"):
            self.assertEqual(self._state("completed", conclusion), "red", conclusion)

    def test_cancelled_is_no_result_not_red(self) -> None:
        # Regression (task cancelled-run-classified-as-red): a cancelled run misread
        # as red nearly reverted a healthy main. no_result never triggers
        # remediation (only red/error do), so a locally-green land survives.
        self.assertEqual(self._state("completed", "cancelled"), "no_result")

    def test_absence_and_unknown_are_no_result(self) -> None:
        for conclusion in ("skipped", "stale", "action_required", "", "brand_new"):
            self.assertEqual(
                self._state("completed", conclusion), "no_result", conclusion
            )

    def test_incomplete_run_is_running(self) -> None:
        self.assertEqual(self._state("in_progress", ""), "running")
        self.assertEqual(self._state("queued", ""), "running")

    def test_no_result_github_leg_does_not_remediate_a_green_local(self) -> None:
        # (local=green, github=no_result) must be neither satisfied nor a
        # remediation trigger — it stays armed, awaiting a resolved GitHub run,
        # instead of reverting a locally-green tip whose hosted run was throttled.
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store.jsonl"
            obligations.create_obligation(
                repo="rrnewton/hermit",
                landed_sha=SHA,
                land_mode="admin",
                actor="tester",
                obligation_id="ob-green-local-noresult",
                path=store,
            )
            obligations.transition(
                "ob-green-local-noresult",
                "legs",
                {
                    "local": {"state": "green", "exit_code": 0},
                    "github": {"state": "no_result"},
                },
                store,
            )
            with mock.patch.object(
                protocol, "github_main_sha", return_value=SHA
            ):
                record = protocol.evaluate_obligation(
                    "ob-green-local-noresult", store_path=store
                )
            self.assertNotEqual(record.get("overall_state"), "remediation_required")
            self.assertNotEqual(record.get("overall_state"), "satisfied")


class LocalStateClassificationTest(unittest.TestCase):
    """A local validate exit code is not a truth value: OOM/SIGKILL != red."""

    def test_clean_exit_is_green(self) -> None:
        self.assertEqual(protocol._local_state(0), "green")

    def test_clean_nonzero_exit_without_a_test_verdict_is_no_result(self) -> None:
        # DERIVED discriminator (task cancellation_taxonomy_distinguish_self): a
        # bare nonzero exit with no visible failing-test verdict is NOT a red.
        # We could not read a product test failure, so the failure came from the
        # build/harness/sandbox layer (or is unrecognised) -> re-dispatch, never
        # revert. Putting the unknown on the safe side is what stops a growing
        # list of environmental wordings from ever manufacturing a false revert.
        for code in (1, 2, 3, 42):
            self.assertEqual(protocol._local_state(code), "no_result", code)

    def test_nonzero_exit_with_a_test_verdict_is_red(self) -> None:
        # The one thing that DOES make a local leg red: a genuine failing test.
        for code in (1, 101):
            self.assertEqual(
                protocol._local_state(code, "test result: FAILED. 1 failed"),
                "red",
                code,
            )

    def test_environment_kill_is_no_result_not_red(self) -> None:
        # Regression (task obligation-path-must-consume-no-result-taxonomy): an
        # OOM/SIGKILL (137) sub-profile build was misread as red and helped drive
        # an automated revert of a healthy main tip. A killed process never
        # delivered a verdict; it is a hole to re-dispatch.
        for code in (137, 143, -9, -15):
            self.assertEqual(protocol._local_state(code), "no_result", code)


class EnvironmentalLocalClassificationTest(unittest.TestCase):
    """Plant every direction: a HARNESS-caused red is no_result, never a revert.

    Regression (task cancellation_taxonomy_distinguish_self): three environmental
    failures tonight (a sandbox EPERM re-validate, a BpfJailer `.o.d` denial, a
    cold-build link flake with zero tests) each read as a product red and were
    one automated step from reverting a healthy tip.
    """

    def test_sandbox_eperm_with_zero_test_failures_is_no_result(self) -> None:
        # #1576: a re-validate died on a sandbox EPERM, no test ever ran.
        output = "make: *** [foo.o] Error 1\nopenat(...) = -1 EPERM (Operation not permitted)\n"
        state, reason = protocol._classify_local(1, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:sandbox-denied")

    def test_bpfjailer_dep_write_denial_is_no_result(self) -> None:
        # step-21: BpfJailer denied a `.o.d` dependency-file write.
        output = "cc1: fatal error: could not open dr_config.h.o.d: Permission denied\n"
        state, reason = protocol._classify_local(2, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:sandbox-denied")

    def test_cold_build_link_flake_is_no_result(self) -> None:
        # DynamoRIO cold-build link flake, ZERO test failures.
        output = "/usr/bin/ld: cannot find -lfoo\ncollect2: error: ld returned 1 exit status\n"
        state, reason = protocol._classify_local(1, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:cold-build-flake")

    def test_network_proxy_drop_is_no_result(self) -> None:
        output = "error: failed to get `serde`\nCaused by: could not resolve host: github.com\n"
        state, _ = protocol._classify_local(101, output)
        self.assertEqual(state, "no_result")

    def test_disk_exhaustion_is_no_result(self) -> None:
        output = "error: No space left on device (os error 28)\n"
        state, _ = protocol._classify_local(1, output)
        self.assertEqual(state, "no_result")

    def test_a_real_test_failure_is_red_even_with_a_sandbox_line(self) -> None:
        # A real test failure is NEVER swallowed: it wins over any infra line.
        output = (
            "some background noise: permission denied on /proc\n"
            "test tests::determinism_holds ... FAILED\n"
            "test result: FAILED. 41 passed; 1 failed; 0 ignored\n"
        )
        state, reason = protocol._classify_local(101, output)
        self.assertEqual(state, "red")
        self.assertEqual(reason, "test-failure")

    def test_pytest_style_failure_count_is_red(self) -> None:
        state, reason = protocol._classify_local(1, "=== 3 failed, 200 passed in 4.2s ===")
        self.assertEqual(state, "red")
        self.assertEqual(reason, "test-failure")

    def test_local_compile_break_is_no_result_not_a_local_revert(self) -> None:
        # A post-land compile break of code that ALREADY compiled+tested before
        # arming is overwhelmingly environmental (cold cache/toolchain), so the
        # LOCAL leg does not revert on it: no failing test verdict -> no_result,
        # re-dispatch. A genuine regression the environment did not cause still
        # reverts via the authoritative GitHub leg, which sees it too.
        output = "error[E0425]: cannot find value `foo` in this scope\nerror: could not compile `hermit`\n"
        state, reason = protocol._classify_local(101, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:unclassified")

    def test_never_before_seen_environmental_wording_is_no_result(self) -> None:
        # THE derivation the growing-list trap demands: an environmental failure
        # whose wording is in NONE of our signature categories must STILL be a
        # no_result, because the discriminator is "no failing test verdict", not
        # "matches a known infra string". A missing signature costs only a vague
        # log label ("unclassified"), never a false red / revert.
        output = "quux-jailer: request refused by frobnicator policy 0x9\nBuild step failed.\n"
        state, reason = protocol._classify_local(1, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:unclassified")

    def test_environmental_local_red_never_remediates(self) -> None:
        # End-to-end: an environmental local leg stays no_result through the
        # actuator, so no obligation ever recommends a revert for it.
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store.jsonl"
            obligations.create_obligation(
                repo="rrnewton/hermit",
                landed_sha=SHA,
                land_mode="speculative",
                actor="tester",
                obligation_id="ob-env",
                path=store,
            )
            obligations.transition(
                "ob-env",
                "legs",
                {
                    "local": {
                        "state": "no_result",
                        "exit_code": 1,
                        "redispatch_count": protocol.DEFAULT_LOCAL_REDISPATCH_LIMIT,
                        "source": None,
                    },
                    "github": {"state": "running"},
                },
                store,
            )
            with mock.patch.object(protocol, "github_main_sha", return_value=SHA):
                record = protocol.evaluate_obligation("ob-env", store_path=store)
            self.assertNotEqual(record.get("overall_state"), "remediation_required")


class LocalRedispatchTest(unittest.TestCase):
    """A no_result / uncorroborated red re-dispatches instead of remediating."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / "obligations.jsonl"
        self.source = self.root / "hermit"
        self.source.mkdir()
        obligations.create_obligation(
            repo="rrnewton/hermit",
            landed_sha=SHA,
            land_mode="speculative",
            actor="tester",
            obligation_id="ob",
            path=self.store,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed(self, patch: dict) -> None:
        obligations.transition("ob", "seed", patch, self.store)

    def test_no_result_local_re_dispatches_and_never_remediates(self) -> None:
        self._seed(
            {
                "local": {
                    "state": "no_result",
                    "exit_code": 137,
                    "source": str(self.source),
                    "redispatch_count": 0,
                    "pid": None,
                },
                "github": {"state": "running"},
            }
        )
        with mock.patch.object(
            protocol, "_spawn_detached", return_value=4321
        ) as spawn, mock.patch.object(protocol, "github_runs", return_value=[]):
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                record = protocol.poll_obligation("ob", self.store)
        spawn.assert_called_once()
        self.assertEqual(record["local"]["state"], "running")
        self.assertEqual(record["local"]["redispatch_count"], 1)
        self.assertNotEqual(record["overall_state"], "remediation_required")

    def test_provisional_local_red_re_dispatches_not_reverts(self) -> None:
        self._seed(
            {
                "local": {
                    "state": "red",
                    "exit_code": 1,
                    "source": str(self.source),
                    "redispatch_count": 0,
                    "pid": None,
                },
                "github": {"state": "running"},
            }
        )
        with mock.patch.object(
            protocol, "_spawn_detached", return_value=4321
        ) as spawn, mock.patch.object(protocol, "github_runs", return_value=[]):
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                record = protocol.poll_obligation("ob", self.store)
        spawn.assert_called_once()
        self.assertEqual(record["local"]["state"], "running")
        self.assertNotEqual(record["overall_state"], "remediation_required")

    def test_spent_budget_red_does_not_re_dispatch(self) -> None:
        self._seed(
            {
                "local": {
                    "state": "red",
                    "exit_code": 1,
                    "source": str(self.source),
                    "redispatch_count": protocol.DEFAULT_LOCAL_REDISPATCH_LIMIT,
                    "pid": None,
                },
                "github": {"state": "running"},
            }
        )
        with mock.patch.object(
            protocol, "_spawn_detached", return_value=4321
        ) as spawn, mock.patch.object(protocol, "github_runs", return_value=[]):
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                record = protocol.poll_obligation("ob", self.store)
        spawn.assert_not_called()
        self.assertEqual(record["overall_state"], "remediation_required")


if __name__ == "__main__":
    unittest.main()
