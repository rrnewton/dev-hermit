#!/usr/bin/env python3
"""Tests for the owner tooling directive ancestry gate."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "check.py"
SPEC = importlib.util.spec_from_file_location("directive_check", MODULE_PATH)
assert SPEC and SPEC.loader
directive_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = directive_check
SPEC.loader.exec_module(directive_check)


def completed(command, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, rc, stdout, stderr)


class FakeRunner:
    def __init__(self, known_tasks: set[str]):
        self.known_tasks = known_tasks
        self.verifier_calls: list[str] = []

    def __call__(self, command, **_kwargs):
        command = tuple(str(item) for item in command)
        if command[:2] == ("tg", "sql"):
            return completed(command, stdout="\n".join(sorted(self.known_tasks)) + "\n")
        if "protocol.py" in " ".join(command):
            identity = command[command.index("verify-landing") + 1]
            self.verifier_calls.append(identity)
            if identity.startswith("a"):
                payload = {
                    "state": "landed",
                    "ancestry": "ancestor",
                    "resolved_sha": identity,
                }
                return completed(command, stdout=json.dumps(payload))
            if identity.startswith("b"):
                payload = {
                    "state": "not-landed",
                    "ancestry": "not-ancestor",
                    "resolved_sha": identity,
                }
                return completed(command, rc=1, stdout=json.dumps(payload))
            if identity.startswith("f"):
                # The verifier reached the checkout but its fetch failed, exactly
                # as `fetch --no-tags origin <sha>: fatal: remote` did in the
                # field. The checker must not read this as a clean pass.
                payload = {
                    "state": "unverifiable",
                    "reason": (
                        "command failed: with-proxy git -C /checkout "
                        f"fetch --no-tags origin {identity}: fatal: remote error"
                    ),
                }
                return completed(command, rc=2, stdout=json.dumps(payload))
            payload = {"state": "unverifiable", "reason": "no mergeCommit.oid"}
            return completed(command, rc=2, stdout=json.dumps(payload))
        if command[:3] == ("git", "-C", str(directive_check.ROOT)) or (
            len(command) > 3 and command[0] == "git" and command[1] == "-C"
        ):
            return completed(command, stdout="d" * 40 + "\n")
        raise AssertionError(f"unexpected command: {command}")


def directive(
    item_id: str,
    *,
    identity: str | None,
    task: str | None = None,
    owner: str = "agent",
    parent_id: str | None = None,
    gate: str | None = None,
):
    record = {
        "id": item_id,
        "summary": item_id.replace("-", " "),
        "requested_at": "2026-08-04",
        "repository": "rrnewton/dev-hermit",
        "checkout": ".",
        "target": "main",
        "task": task if task is not None else f"task-{item_id}",
        "owner": owner,
        "source_row": item_id,
        "parent_id": parent_id,
        "implementation": None
        if identity is None
        else {"kind": "commit", "identity": identity},
    }
    if gate is not None:
        record["gate"] = gate
    return record


class DirectiveCheckTest(unittest.TestCase):
    def evaluate(self, directives):
        tasks = {item["task"] for item in directives if item.get("task")}
        runner = FakeRunner(tasks)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "ledger.json"
            ledger.write_text(json.dumps({"schema_version": 1, "directives": directives}))
            report = directive_check.evaluate(
                ledger_path=ledger,
                run=runner,
                checked_at="2026-08-04T00:00:00+00:00",
            )
        return report, runner

    def test_planted_nonancestor_is_refused_and_three_landed_stay_satisfied(self):
        directives = [
            directive("landed-one", identity="a" * 40),
            directive("landed-two", identity="a" * 39 + "1"),
            directive("landed-three", identity="a" * 39 + "2"),
            directive("planted-not-landed", identity="b" * 40),
        ]
        report, runner = self.evaluate(directives)

        self.assertEqual(1, report.exit_code)
        self.assertEqual(3, report.counts["satisfied"])
        self.assertEqual(1, report.counts["not_landed"])
        planted = next(
            item for item in report.directives if item.id == "planted-not-landed"
        )
        self.assertEqual("not-ancestor", planted.ancestry)
        self.assertEqual(4, len(runner.verifier_calls))

    def test_missing_task_and_owner_cannot_be_satisfied(self):
        directives = [
            directive("missing-task", identity="a" * 40, task=""),
            directive("missing-owner", identity="a" * 39 + "1", owner=""),
        ]
        report, _runner = self.evaluate(directives)

        self.assertEqual(1, report.exit_code)
        self.assertEqual(1, report.counts["missing_task"])
        self.assertEqual(1, report.counts["needs_owner"])
        self.assertEqual(0, report.counts.get("satisfied", 0))
        self.assertEqual(1, report.issue_counts["missing_task"])
        self.assertEqual(1, report.issue_counts["missing_owner"])

    def test_landed_parent_stays_partial_while_child_has_no_implementation(self):
        directives = [
            directive("parent", identity="a" * 40),
            directive("child", identity=None, parent_id="parent"),
        ]
        report, _runner = self.evaluate(directives)

        states = {item.id: item.state for item in report.directives}
        self.assertEqual({"parent": "partial", "child": "open"}, states)
        self.assertEqual(1, report.issue_counts["no_implementation"])

    def test_documentation_without_identity_remains_open(self):
        report, runner = self.evaluate([directive("quoted-only", identity=None)])

        self.assertEqual(1, report.exit_code)
        self.assertEqual(1, report.counts["open"])
        self.assertEqual([], runner.verifier_calls)

    def test_named_gate_is_gated_not_drift(self):
        report, runner = self.evaluate(
            [directive("deferred", identity=None, gate="zero open PRs")]
        )

        gated = next(item for item in report.directives if item.id == "deferred")
        self.assertEqual("gated", gated.state)
        self.assertEqual("zero open PRs", gated.gate)
        self.assertIn("zero open PRs", gated.reason)
        # A gated directive is deferred on a named condition, never counted as
        # drift, and never triggers an ancestry verification.
        self.assertNotIn(gated.state, directive_check.DRIFT_STATES)
        self.assertEqual(1, report.counts["gated"])
        self.assertEqual([], runner.verifier_calls)

    def test_unnamed_gate_is_invalid(self):
        report, _runner = self.evaluate(
            [directive("bare-gate", identity=None, gate="   ")]
        )

        item = next(item for item in report.directives if item.id == "bare-gate")
        self.assertEqual("invalid", item.state)
        self.assertIn("unnamed_gate", item.issues)

    def test_gate_on_implemented_is_invalid(self):
        report, _runner = self.evaluate(
            [directive("landed-but-gated", identity="a" * 40, gate="zero open PRs")]
        )

        item = next(
            item for item in report.directives if item.id == "landed-but-gated"
        )
        self.assertEqual("invalid", item.state)
        self.assertIn("gate_on_implemented", item.issues)

    def test_fetch_failure_is_distinct_from_not_checked_and_never_clean(self):
        # Positive side: a fetch failure while verifying a claimed commit must
        # land in `fetch_failed` — distinct from `not_checked` (never verified)
        # and from a clean pass — and must drive the overall verdict to unknown.
        directives = [
            directive("landed", identity="a" * 40),
            directive("fetch-broke", identity="f" * 40),
        ]
        report, _runner = self.evaluate(directives)

        broke = next(item for item in report.directives if item.id == "fetch-broke")
        self.assertEqual("fetch_failed", broke.state)
        self.assertEqual("fetch_failed", broke.ancestry)
        self.assertNotEqual("not_checked", broke.ancestry)
        self.assertIn("fetch failed", broke.reason)
        self.assertIn(broke.state, directive_check.DRIFT_STATES)
        # Never green, and unknown (exit 2) rather than a confirmed not_landed red.
        self.assertEqual("unknown", report.overall_state)
        self.assertEqual(2, report.exit_code)
        self.assertEqual(1, report.counts["fetch_failed"])

    def test_plain_unverifiable_is_not_misread_as_fetch_failure(self):
        # Negative side: an ambiguous verdict that is NOT a fetch failure (here
        # "no mergeCommit.oid") stays `unverifiable`, so `fetch_failed` fires
        # only on genuine fetch/network failures, not on every unknown.
        report, _runner = self.evaluate([directive("ambiguous", identity="c" * 40)])

        item = next(item for item in report.directives if item.id == "ambiguous")
        self.assertEqual("unverifiable", item.state)
        self.assertEqual(0, report.counts.get("fetch_failed", 0))


if __name__ == "__main__":
    unittest.main()
