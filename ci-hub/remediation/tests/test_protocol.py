from __future__ import annotations

import argparse
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

    def test_rebase_merged_pr_resolves_to_replayed_main_sha(self) -> None:
        source = self.root / "source"
        source.mkdir()
        head = "c" * 40
        replay = "d" * 40

        def run(command, **_kwargs):
            command = tuple(command)
            if command[:2] == ("with-proxy", "git"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ("with-proxy", "gh", "pr"):
                payload = {
                    "state": "MERGED",
                    "headRefOid": head,
                    "mergeCommit": {"oid": replay},
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if "--is-ancestor" in command:
                self.assertEqual(command[-2:], (replay, "origin/main"))
                return subprocess.CompletedProcess(command, 0, "", "")
            self.fail(f"unexpected command: {command}")

        with mock.patch.object(protocol, "_run", side_effect=run):
            landed = protocol.resolve_landed_sha(
                source,
                "pre-rebase-head-not-fetched",
                repo="rrnewton/hermit",
                pr=1219,
            )
        self.assertEqual(landed, replay)

    def test_network_run_passes_one_explicit_command_to_herdr(self) -> None:
        command = (
            "with-proxy",
            "git",
            "-C",
            "/workspace/hermit",
            "fetch",
            "origin",
            "main",
        )
        completed = subprocess.CompletedProcess(command, 0, "", "")
        previous = protocol._HERDR_AGENT
        try:
            protocol._HERDR_AGENT = "hermit-coord"
            with (
                mock.patch.dict(
                    protocol.os.environ,
                    {"DEV_HERMIT_PARENT": str(self.root)},
                ),
                mock.patch.object(Path, "is_file", return_value=True),
                mock.patch.object(protocol, "_run", return_value=completed) as run,
            ):
                protocol._network_run(command, check=True, timeout=17)
        finally:
            protocol._HERDR_AGENT = previous

        wrapped = run.call_args.args[0]
        self.assertEqual(str(self.root / "agent-utils/bin/herdr-run"), wrapped[0])
        self.assertEqual(("--agent", "hermit-coord"), tuple(wrapped[1:3]))
        self.assertEqual(4, len(wrapped))
        self.assertEqual(
            "with-proxy git -C /workspace/hermit fetch origin main", wrapped[3]
        )

    def test_raw_rebase_head_failure_explains_pr_aware_check(self) -> None:
        source = self.root / "source"
        source.mkdir()
        head = "c" * 40

        def run(command, **_kwargs):
            command = tuple(command)
            if command[:2] == ("with-proxy", "git"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if "rev-parse" in command:
                return subprocess.CompletedProcess(command, 0, head + "\n", "")
            if "--is-ancestor" in command:
                return subprocess.CompletedProcess(command, 1, "", "")
            self.fail(f"unexpected command: {command}")

        with mock.patch.object(protocol, "_run", side_effect=run):
            with self.assertRaisesRegex(protocol.ProtocolError, "pass --pr"):
                protocol.resolve_landed_sha(source, head)

    def test_rebase_replay_sha_must_still_be_on_main(self) -> None:
        source = self.root / "source"
        source.mkdir()
        head = "c" * 40
        replay = "d" * 40

        def run(command, **_kwargs):
            command = tuple(command)
            if command[:2] == ("with-proxy", "git"):
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ("with-proxy", "gh", "pr"):
                payload = {
                    "state": "MERGED",
                    "headRefOid": head,
                    "mergeCommit": {"oid": replay},
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if "--is-ancestor" in command:
                return subprocess.CompletedProcess(command, 1, "", "")
            self.fail(f"unexpected command: {command}")

        with mock.patch.object(protocol, "_run", side_effect=run):
            with self.assertRaisesRegex(protocol.ProtocolError, "orphaned"):
                protocol.resolve_landed_sha(
                    source, head, repo="rrnewton/hermit", pr=1219
                )

    def test_verify_landing_pr_reports_replay_ancestry(self) -> None:
        source = self.root / "source"
        source.mkdir()
        replay = "d" * 40
        args = argparse.Namespace(
            source=source,
            reference="1219",
            target="main",
            repo="rrnewton/hermit",
            json=True,
            item=None,
            claimed_oid=None,
        )
        with (
            mock.patch.object(protocol, "_fetch_target", return_value="origin/main"),
            mock.patch.object(
                protocol,
                "_query_pr_landing",
                return_value=("MERGED", "c" * 40, replay),
            ),
            mock.patch.object(protocol, "_is_target_ancestor", return_value=True),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            rc = protocol.verify_landing(args)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["state"], "landed")
        self.assertEqual(payload["rc"], 0)
        self.assertEqual(payload["resolved_sha"], replay)

    def test_verify_landing_sha_reports_not_landed(self) -> None:
        source = self.root / "source"
        source.mkdir()
        args = argparse.Namespace(
            source=source,
            reference=SHA,
            target="main",
            repo="rrnewton/hermit",
            json=True,
            item=None,
            claimed_oid=None,
        )
        with (
            mock.patch.object(protocol, "_fetch_target", return_value="origin/main"),
            mock.patch.object(protocol, "_resolve_raw_sha", return_value=SHA),
            mock.patch.object(protocol, "_is_target_ancestor", return_value=False),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            rc = protocol.verify_landing(args)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual(payload["state"], "not-landed")
        self.assertEqual(payload["rc"], 1)
        self.assertEqual(payload["ancestry"], "not-ancestor")

    def test_verify_landing_pr_without_merge_commit_is_unverifiable(self) -> None:
        source = self.root / "source"
        source.mkdir()
        args = argparse.Namespace(
            source=source,
            reference="1558",
            target="main",
            repo="rrnewton/hermit",
            json=True,
            item=None,
            claimed_oid=None,
        )
        with (
            mock.patch.object(protocol, "_fetch_target", return_value="origin/main"),
            mock.patch.object(
                protocol,
                "_query_pr_landing",
                return_value=("OPEN", "e" * 40, ""),
            ),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            rc = protocol.verify_landing(args)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 2)
        self.assertEqual(payload["state"], "unverifiable")
        self.assertEqual(payload["rc"], 2)
        self.assertEqual(payload["reason"], "no mergeCommit.oid")

    def test_verify_landing_expands_abbreviated_direct_commit(self) -> None:
        source = self.root / "source"
        source.mkdir()
        args = argparse.Namespace(
            source=source,
            reference=SHA[:7],
            target="main",
            repo="rrnewton/hermit",
            json=True,
            item="direct main change",
            claimed_oid=None,
        )
        with (
            mock.patch.object(protocol, "_fetch_target", return_value="origin/main"),
            mock.patch.object(protocol, "_resolve_raw_sha", return_value=SHA),
            mock.patch.object(protocol, "_is_target_ancestor", return_value=True),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            rc = protocol.verify_landing(args)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["claimed_oid"], SHA[:7])
        self.assertEqual(payload["full_oid"], SHA)
        self.assertTrue(payload["resolves"])
        self.assertTrue(payload["change_present_on_main"])
        self.assertEqual(payload["claimed_ancestry_rc"], 0)

    def test_verify_landing_separates_dead_head_from_live_rebased_change(self) -> None:
        source = self.root / "source"
        source.mkdir()
        head = "abedbe29" + "c" * 32
        replay = "d" * 40
        args = argparse.Namespace(
            source=source,
            reference="1592",
            target="main",
            repo="rrnewton/hermit",
            json=True,
            item="PR #1592",
            claimed_oid="abedbe29",
        )

        def is_ancestor(_source, sha, _target):
            return sha == replay

        with (
            mock.patch.object(protocol, "_fetch_target", return_value="origin/main"),
            mock.patch.object(
                protocol,
                "_query_pr_landing",
                return_value=("MERGED", head, replay),
            ),
            mock.patch.object(
                protocol,
                "_resolve_claimed_oid",
                return_value=(head, True, True),
            ),
            mock.patch.object(protocol, "_is_target_ancestor", side_effect=is_ancestor),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            rc = protocol.verify_landing(args)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0, "the PR's rebased change is landed")
        self.assertEqual(payload["state"], "landed")
        self.assertEqual(payload["claimed_oid"], "abedbe29")
        self.assertEqual(payload["full_oid"], head)
        self.assertTrue(payload["resolves"])
        self.assertTrue(payload["change_present_on_main"])
        self.assertEqual(payload["claimed_ancestry_rc"], 1)
        self.assertEqual(payload["merge_commit_oid"], replay)

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

    def test_newest_no_result_never_falls_back_to_older_failure(self) -> None:
        runs = protocol._parse_github_runs(
            json.dumps(
                [
                    {
                        "databaseId": 10,
                        "headSha": SHA,
                        "workflowName": protocol.DEFAULT_WORKFLOW,
                        "status": "completed",
                        "conclusion": "failure",
                        "createdAt": "2026-08-04T15:12:05Z",
                    },
                    {
                        "databaseId": 11,
                        "headSha": SHA,
                        "workflowName": protocol.DEFAULT_WORKFLOW,
                        "status": "completed",
                        "conclusion": "cancelled",
                        "createdAt": "2026-08-04T15:24:36Z",
                    },
                ]
            ),
            SHA,
        )
        self.assertEqual(runs[0]["databaseId"], 11)
        self.assertIsNone(protocol._latest_resolved_github_run(runs))

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

    def test_budget_spent_local_red_with_no_hosted_answer_investigates_not_reverts(
        self,
    ) -> None:
        # NEGATIVE bracket (task obligation_revert_path_lone): a local red whose
        # authoritative GitHub leg NEVER reported (no_result: never admitted /
        # cancelled below the concurrency cap / superseded -- the COMMON case under
        # admission-limited hosted CI) must NOT auto-revert a HEALTHY tip, EVEN
        # after the whole re-dispatch budget is spent. A load/environment flake
        # reproduces across cold re-dispatches, so budget-spent is not corroboration
        # -- only an authoritative github=="red" is. Three such lone-red revert
        # recommendations fired 2026-08-04 (e8a0d8d3, 0f891e43 x2), all caught by
        # humans. The tip here (main_sha == SHA == landed_sha) is HEALTHY.
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
                "github": {"state": "no_result"},
            }
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            record = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        # No revert of the healthy tip: no remediation, no recommendation, no
        # triggered dispatch. Surfaced for a human instead.
        self.assertNotEqual(record["overall_state"], "remediation_required")
        self.assertEqual(record["overall_state"], "investigation_required")
        self.assertIsNone(record.get("recommendation"))
        self.assertEqual(record["remediation"]["state"], "not_required")
        self.assertIn("UNCORROBORATED local", stderr.getvalue())
        self.assertNotIn("REMEDIATION TRIGGERED", stderr.getvalue())

    def test_spent_local_red_waits_while_github_still_running(self) -> None:
        # SEQUENCING regression (task cancellation_taxonomy_distinguish_self,
        # obligation 20260804-025419-0f891e43): a local red that had spent its whole
        # re-dispatch budget armed action=revert while the GitHub verify run was
        # STILL RUNNING -- the tool's own exoneration path in flight, acted on
        # before it was read. A running leg must be waited for: NOTHING arms until
        # both legs report.
        self.create()
        self.transition(
            {
                "local": {
                    "state": "red",
                    "finished_at": "2026-08-04T03:04:59Z",
                    "exit_code": 1,
                    "log_path": "/tmp/local.log",
                    "redispatch_count": protocol.DEFAULT_LOCAL_REDISPATCH_LIMIT,
                },
                "github": {"state": "running"},
            }
        )
        with redirect_stderr(io.StringIO()):
            record = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        self.assertNotEqual(record["overall_state"], "remediation_required")
        self.assertIsNone(record.get("recommendation"))

    def test_github_red_waits_while_local_leg_still_running(self) -> None:
        # The sequencing guard is symmetric: an authoritative GitHub red must not
        # arm while the local leg is still in flight -- wait for the second leg to
        # report ("one leg red, one leg still running -> nothing arms").
        self.create()
        self.transition(
            {
                "local": {"state": "running"},
                "github": {"state": "red", "finished_at": "2026-08-04T03:00:00Z"},
            }
        )
        with redirect_stderr(io.StringIO()):
            record = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        self.assertNotEqual(record["overall_state"], "remediation_required")

    def test_local_red_and_github_green_is_a_disagreement_not_a_revert(self) -> None:
        # THIRD outcome (obligation 20260804-025419-0f891e43): local red beside a
        # GREEN authoritative GitHub leg for the same SHA is a DISAGREEMENT to
        # investigate, never an auto-revert. Even a budget-spent local red cannot
        # overrule a green hosted leg.
        self.create()
        self.transition(
            {
                "local": {
                    "state": "red",
                    "finished_at": "2026-08-04T03:04:59Z",
                    "exit_code": 1,
                    "log_path": "/tmp/local.log",
                    "redispatch_count": protocol.DEFAULT_LOCAL_REDISPATCH_LIMIT,
                },
                "github": {"state": "green", "finished_at": "2026-08-04T03:20:00Z"},
            }
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            record = protocol.evaluate_obligation(
                "test-obligation", store_path=self.store, main_sha=SHA
            )
        self.assertEqual(record["overall_state"], "investigation_required")
        self.assertNotEqual(record["overall_state"], "remediation_required")
        self.assertIsNone(record.get("recommendation"))
        self.assertEqual(record["remediation"]["state"], "not_required")
        self.assertIn("DISAGREE", stderr.getvalue())

    def test_authoritative_github_red_still_reverts_after_lone_local_guard(
        self,
    ) -> None:
        # POSITIVE, COUNTED bracket (task obligation_revert_path_lone): the guard
        # against a lone local red must NOT make the actuator inert. Every
        # AUTHORITATIVE hosted red -- N == 4 distinct red-producing conclusions
        # (failure, timed_out, error, startup_failure), which _github_state maps to the
        # taxonomy "red" -- STILL arms an immediate revert of a still-at-tip land.
        # A path that never recommends is disabled, not fixed.
        red_conclusions = ("failure", "timed_out", "error", "startup_failure")
        self.assertEqual(len(red_conclusions), 4)  # N stated: N == 4
        self.assertEqual(set(red_conclusions), set(protocol._RED_CONCLUSIONS))
        reverted = 0
        for index, conclusion in enumerate(red_conclusions):
            store = self.root / f"positive-{index}.jsonl"
            obligation_id = f"positive-{index}"
            obligations.create_obligation(
                repo="rrnewton/hermit",
                landed_sha=SHA,
                land_mode="speculative",
                actor="test",
                obligation_id=obligation_id,
                path=store,
            )
            # The hosted leg reports a genuine red via the real classifier, so this
            # is not a hand-forced "red" string but the taxonomy value _github_state
            # derives from an authoritative failing conclusion.
            self.assertEqual(
                protocol._github_state(
                    {"status": "completed", "conclusion": conclusion}
                ),
                "red",
            )
            obligations.transition(
                obligation_id,
                "github-red",
                {
                    "local": {"state": "no_result"},
                    "github": {
                        "state": "red",
                        "finished_at": "2026-08-04T03:00:00Z",
                        "workflow_name": protocol.DEFAULT_WORKFLOW,
                        "urls": ["https://example/run"],
                    },
                },
                store,
            )
            with redirect_stderr(io.StringIO()):
                record = protocol.evaluate_obligation(
                    obligation_id, store_path=store, main_sha=SHA
                )
            self.assertEqual(record["overall_state"], "remediation_required", conclusion)
            self.assertEqual(record["recommendation"]["action"], "revert", conclusion)
            self.assertEqual(record["remediation"]["state"], "triggered", conclusion)
            reverted += 1
        # All N genuine hosted failures still revert.
        self.assertEqual(reverted, 4)

    def test_no_result_is_distinct_from_both_pass_and_fail_for_a_local_red(
        self,
    ) -> None:
        # DISTINCTNESS bracket (task obligation_revert_path_lone): for the SAME
        # budget-spent local red, the hosted leg's terminal value selects THREE
        # different outcomes -- no_result must never be folded into either pass or
        # fail. This is the rc=2 UNVERIFIABLE vs rc=1 NOT-LANDED discipline applied
        # to verification legs.
        #   github green      -> investigation_required (DISAGREEMENT, no revert)
        #   github no_result  -> investigation_required (UNCORROBORATED, no revert)
        #   github red        -> remediation_required   (authoritative revert)
        cases = {
            "green": ("investigation_required", None),
            "no_result": ("investigation_required", None),
            "red": ("remediation_required", "revert"),
        }
        outcomes = {}
        for github_state, (expected_overall, expected_action) in cases.items():
            store = self.root / f"distinct-{github_state}.jsonl"
            obligation_id = f"distinct-{github_state}"
            obligations.create_obligation(
                repo="rrnewton/hermit",
                landed_sha=SHA,
                land_mode="speculative",
                actor="test",
                obligation_id=obligation_id,
                path=store,
            )
            github_leg = {"state": github_state}
            if github_state in ("green", "red"):
                github_leg["finished_at"] = "2026-08-04T03:00:00Z"
            obligations.transition(
                obligation_id,
                "legs-report",
                {
                    "local": {
                        "state": "red",
                        "finished_at": "2026-08-04T03:04:59Z",
                        "exit_code": 1,
                        "log_path": "/tmp/local.log",
                        "redispatch_count": protocol.DEFAULT_LOCAL_REDISPATCH_LIMIT,
                    },
                    "github": github_leg,
                },
                store,
            )
            with redirect_stderr(io.StringIO()):
                record = protocol.evaluate_obligation(
                    obligation_id, store_path=store, main_sha=SHA
                )
            self.assertEqual(record["overall_state"], expected_overall, github_state)
            action = (record.get("recommendation") or {}).get("action")
            self.assertEqual(action, expected_action, github_state)
            outcomes[github_state] = (record["overall_state"], action)
        # no_result is the same as neither the pass-side (green) recommendation nor
        # the fail-side (red) recommendation: it never reverts (like green) yet is
        # reached by its OWN path (uncorroborated), distinct from the disagreement.
        self.assertIsNone(outcomes["no_result"][1])
        self.assertNotEqual(outcomes["no_result"][1], outcomes["red"][1])

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

    def test_only_success_is_green(self) -> None:
        self.assertEqual(self._state("completed", "success"), "green")
        self.assertEqual(self._state("completed", "neutral"), "no_result")

    def test_genuine_failures_are_red(self) -> None:
        for conclusion in ("failure", "timed_out", "error", "startup_failure"):
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

    def test_incomplete_run_is_no_result(self) -> None:
        self.assertEqual(self._state("in_progress", ""), "no_result")
        self.assertEqual(self._state("queued", ""), "no_result")

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

    def test_build_script_panic_dag_summary_is_no_result_not_red(self) -> None:
        # REAL incident log (obligation 20260804-025419-0f891e43), planted verbatim:
        # a reverie-dbi/build.rs:339 cold-build panic that the DAG runner rendered
        # with "N failed" + "panicked at". Both the "panicked at" marker and the
        # "\bN failed\b" count regex fire, so the OLD classifier called it a red and
        # armed a revert of a healthy tip. A build.rs panic is a BUILD-phase flake,
        # never a test verdict -> no_result, re-dispatch.
        output = (
            "❌ portable CI DAG manifest (0 passed, 1 failed, exit 1: "
            "[build.dbi_release] thread 'main' (3550207) panicked at "
            ".cargo/git/checkouts/reverie-2fc770f7a9c80803/d973a85/"
            "reverie-dbi/build.rs:339:5:; full log: /tmp/hermit-validate.4bKorf.log)\n"
            "❌ Validation summary [full] (3 passed, 2 failed; full log: "
            "/tmp/hermit-validate.4bKorf.log)\n"
        )
        state, reason = protocol._classify_local(1, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:build-script")

    def test_cargo_custom_build_command_failure_is_no_result(self) -> None:
        output = (
            "error: failed to run custom build command for `reverie-dbi v0.1.0`\n"
            "  process didn't exit successfully (exit status: 101)\n"
            "test summary: 0 passed, 1 failed\n"
        )
        state, reason = protocol._classify_local(101, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:build-script")

    def test_genuine_test_panic_outside_build_rs_stays_red(self) -> None:
        # Guard against over-broadening: a panic in PRODUCT source (not build.rs)
        # with a failing test verdict is a real red and MUST still revert.
        output = (
            "thread 'tests::determinism' panicked at src/detcore/sched.rs:88:5:\n"
            "test result: FAILED. 40 passed; 1 failed; 0 ignored\n"
        )
        state, reason = protocol._classify_local(101, output)
        self.assertEqual(state, "red")
        self.assertEqual(reason, "test-failure")

    def test_inner_memorymax_oom_in_test_node_is_no_result_not_red(self) -> None:
        # Sixth env signature (task cancellation_taxonomy_distinguish_self note
        # 2026-08-04, validate SHA 37f8ef3c): an inner-cgroup MemoryMax OOM inside a
        # boxed TEST node SIGKILLs a test process, so the node surfaces a genuine
        # test-failure VERDICT ("N failed"/panic). Build-script recognition alone
        # does NOT catch this (no build.rs panic), so without the OOM signature
        # _has_test_failures would fire -> false red. The OOM string is the tell:
        # this is a cap breach to re-dispatch (fix-forward = raise the cap), never a
        # tip to revert. Runner strings planted verbatim (model.rs / scheduler.rs).
        output = (
            "[test.hermit_integration] ▲ MEMORY CAP HIT: OOM-killed at its inner "
            "cgroup MemoryMax (4.0 GiB); peak 4.0 GiB, 14 oom_kill event(s)\n"
            "❌ portable CI DAG manifest (0 passed, 1 failed, exit 1: "
            "[test.hermit_integration] OOM-KILLED (hit inner MemoryMax; 14 oom_kill "
            "event(s)))\n"
            "thread 'detcore::sched::tests::t' panicked at src/lib.rs:9:1: SIGKILL\n"
            "test result: FAILED. 20 passed; 1 failed; 0 ignored\n"
        )
        state, reason = protocol._classify_local(1, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:inner-memorymax-oom")

    def test_inner_memorymax_oom_that_also_panicked_build_rs_labels_oom(self) -> None:
        # When an OOM reaps a child rustc/cc1plus and reverie-dbi/build.rs then
        # panics, BOTH the OOM string and the build.rs panic are present. The OOM
        # signature is checked FIRST because "raise this node's cap" is the
        # actionable label; the outcome is no_result either way, never a revert.
        output = (
            "[build.dbi_release] ▲ MEMORY CAP HIT: OOM-killed at its inner cgroup "
            "MemoryMax (8.0 GiB); peak 8.0 GiB, 1 oom_kill event(s)\n"
            "thread 'main' panicked at reverie-dbi/build.rs:339:5: rustc killed\n"
            "❌ Validation summary [full] (3 passed, 2 failed)\n"
        )
        state, reason = protocol._classify_local(1, output)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "non-test-failure:inner-memorymax-oom")

    def test_genuine_test_failure_without_oom_stays_red(self) -> None:
        # Symmetric guard: a real failing test verdict with NO OOM string is still a
        # red that must revert -- the OOM signature never swallows a genuine failure.
        output = "test result: FAILED. 40 passed; 1 failed; 0 ignored\n"
        state, reason = protocol._classify_local(101, output)
        self.assertEqual(state, "red")
        self.assertEqual(reason, "test-failure")

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
        # A budget-spent local red no longer re-dispatches (spawn not called). With
        # the hosted leg having given no answer (no_result) it is UNCORROBORATED, so
        # it is surfaced for investigation -- NOT an auto-revert of the tip (task
        # obligation_revert_path_lone). (github="no_result" not "running": a running
        # hosted leg must be WAITED for -- see
        # test_spent_budget_red_with_github_running_waits.)
        self._seed(
            {
                "local": {
                    "state": "red",
                    "exit_code": 1,
                    "source": str(self.source),
                    "redispatch_count": protocol.DEFAULT_LOCAL_REDISPATCH_LIMIT,
                    "pid": None,
                },
                "github": {"state": "no_result"},
            }
        )
        with mock.patch.object(
            protocol, "_spawn_detached", return_value=4321
        ) as spawn, mock.patch.object(protocol, "github_runs", return_value=[]):
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                record = protocol.poll_obligation("ob", self.store)
        spawn.assert_not_called()
        self.assertEqual(record["overall_state"], "investigation_required")
        self.assertNotEqual(record["overall_state"], "remediation_required")

    def test_spent_budget_red_with_github_running_waits(self) -> None:
        # SEQUENCING guard end-to-end through poll: budget-spent local red while the
        # GitHub leg is still running neither re-dispatches nor arms -- it waits for
        # the in-flight hosted verdict (obligation 20260804-025419-0f891e43).
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
        self.assertNotEqual(record["overall_state"], "remediation_required")


if __name__ == "__main__":
    unittest.main()
