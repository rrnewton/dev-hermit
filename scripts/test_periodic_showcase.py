#!/usr/bin/env python3
"""Tests for the recurring `Periodic showcase` selection policy and workflow.

INERT BY CONSTRUCTION. Nothing here sends a wake, a GChat message, or any other
user-facing signal, and nothing here starts ORC. The selection policy is a pure
function over CSV fixtures plus a state file, and the workflow is checked by
reading its source. That is the whole reason the policy lives in a script
instead of inside the workflow body.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest

from scripts import periodic_showcase as ps


HEADER = (
    "run_id,run_utc,hermit_sha,test_id,test_mode,backend,cell_state,outcome\n"
)
PLUGIN = (
    Path(__file__).resolve().parent.parent
    / ".orc" / "plugins" / "periodic-showcase" / "index.ts"
)


def row(run: str, utc: int, sha: str, test: str, backend: str, outcome: str,
        mode: str = "verify") -> str:
    return "{},@{},{},{},{},{},enabled,{}\n".format(
        run, utc, sha, test, mode, backend, outcome
    )


class SelectionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "compat-envelope").mkdir()
        self.primary = Path("compat-envelope/scorecard.csv")
        self.secondary = Path("compat-envelope/fullcorpus-scorecard.csv")
        self.state = Path("ignored/periodic-showcase-state.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, relative: Path, body: str) -> None:
        (self.root / relative).write_text(HEADER + body)

    def select(self, sources=None):
        return ps.select(
            self.root, sources or (self.primary, self.secondary), self.state
        )

    # -- the delta must be a real improvement ------------------------------

    def test_fail_to_pass_is_a_capability_delta(self) -> None:
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c-programs/x", "dbi", "fail")
            + row("r2", 200, "b" * 40, "c-programs/x", "dbi", "pass")
        ))
        code, payload = self.select()
        self.assertEqual(code, ps.EXIT_SHOWCASE_DUE)
        self.assertEqual(payload["selected"]["kind"], ps.CAPABILITY)
        self.assertEqual(payload["selected"]["hermit_sha"], "b" * 40)

    def test_diverge_to_pass_is_also_a_capability_delta(self) -> None:
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c-programs/x", "dbi", "diverge")
            + row("r2", 200, "b" * 40, "c-programs/x", "dbi", "pass")
        ))
        code, payload = self.select()
        self.assertEqual(code, ps.EXIT_SHOWCASE_DUE)
        self.assertEqual(payload["selected"]["kind"], ps.CAPABILITY)

    def test_unavailable_to_pass_is_only_a_coverage_delta(self) -> None:
        # `unavailable` means the cell never ran -- most often the backend was
        # simply not compiled in. Presenting that as a capability win is the
        # manufactured novelty this workflow exists to avoid.
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c-programs/x", "sabre", "unavailable")
            + row("r2", 200, "b" * 40, "c-programs/x", "sabre", "pass")
        ))
        code, payload = self.select()
        self.assertEqual(code, ps.EXIT_SHOWCASE_DUE)
        self.assertEqual(payload["selected"]["kind"], ps.COVERAGE)
        text = ps.instruction(payload["selected"], payload["also_new"])
        self.assertIn("VERIFY THE CAUSE", text)
        self.assertIn("DID NOT RUN", text)
        self.assertIn("--features third-party-backends", text)
        self.assertIn("is NOT", text)
        # The headline must not assert a capability it has not established.
        self.assertNotIn("newly works.", text.split("\n")[0])

    def test_capability_outranks_coverage(self) -> None:
        # Two cells, one of each kind. The coverage one sorts first
        # alphabetically, so only the ranking can put capability ahead.
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "aaa/coverage", "sabre", "unavailable")
            + row("r2", 200, "b" * 40, "aaa/coverage", "sabre", "pass")
            + row("r1", 100, "a" * 40, "zzz/capability", "dbi", "fail")
            + row("r2", 200, "b" * 40, "zzz/capability", "dbi", "pass")
        ))
        code, payload = self.select()
        self.assertEqual(code, ps.EXIT_SHOWCASE_DUE)
        self.assertEqual(payload["selected"]["test_id"], "zzz/capability")
        self.assertEqual(payload["selected"]["kind"], ps.CAPABILITY)

    # -- the regressions that actually bit ---------------------------------

    def test_same_cell_in_two_scorecards_is_not_a_delta(self) -> None:
        # THE MEASURED REGRESSION. On the real corpus every one of the 6
        # "newly green" cells had exactly one observation in each of two
        # different scorecards (scorecard.csv=unavailable,
        # fullcorpus=pass) and no within-file history at all. Interleaving
        # them by timestamp reports two corpora disagreeing as change over time,
        # and would wake the coordinator to showcase an improvement that never
        # happened.
        self.write(self.primary, row("r1", 100, "a" * 40, "c/x", "sabre", "unavailable"))
        self.write(self.secondary, row("r2", 200, "b" * 40, "c/x", "sabre", "pass"))
        code, payload = self.select()
        self.assertEqual(code, ps.EXIT_NOTHING_NEW, payload)

    def test_first_ever_observation_is_not_a_delta(self) -> None:
        # A widened corpus is not an improvement.
        self.write(self.primary, row("r1", 100, "a" * 40, "c/x", "dbi", "pass"))
        code, _ = self.select()
        self.assertEqual(code, ps.EXIT_NOTHING_NEW)

    def test_steady_and_regressing_states_are_not_deltas(self) -> None:
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c/steady-pass", "dbi", "pass")
            + row("r2", 200, "b" * 40, "c/steady-pass", "dbi", "pass")
            + row("r1", 100, "a" * 40, "c/steady-fail", "dbi", "fail")
            + row("r2", 200, "b" * 40, "c/steady-fail", "dbi", "fail")
            + row("r1", 100, "a" * 40, "c/regressed", "dbi", "pass")
            + row("r2", 200, "b" * 40, "c/regressed", "dbi", "fail")
        ))
        code, _ = self.select()
        self.assertEqual(code, ps.EXIT_NOTHING_NEW)

    def test_no_history_is_distinct_from_nothing_new(self) -> None:
        # "I cannot decide" must not be rendered as "nothing improved".
        code, _ = self.select()
        self.assertEqual(code, ps.EXIT_NO_HISTORY)

    # -- persistence: never showcase the same thing twice -------------------

    def test_recording_a_showcase_suppresses_the_repeat(self) -> None:
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c/x", "dbi", "fail")
            + row("r2", 200, "b" * 40, "c/x", "dbi", "pass")
        ))
        code, payload = self.select()
        self.assertEqual(code, ps.EXIT_SHOWCASE_DUE)
        selected = payload["selected"]

        ps.record_shown(self.root, selected["key"], selected["hermit_sha"], self.state)

        repeat_code, repeat = self.select()
        self.assertEqual(repeat_code, ps.EXIT_NOTHING_NEW)
        self.assertIn("already showcased", repeat["reason"])

    def test_persisted_state_survives_a_fresh_read(self) -> None:
        ps.record_shown(self.root, "c/x|verify|dbi", "b" * 40, self.state)
        reloaded = ps.load_state(self.root, self.state)
        self.assertEqual(
            reloaded["shown"], [{"key": "c/x|verify|dbi", "hermit_sha": "b" * 40}]
        )
        self.assertTrue((self.root / self.state).is_file())

    def test_recording_is_idempotent(self) -> None:
        ps.record_shown(self.root, "c/x|verify|dbi", "b" * 40, self.state)
        ps.record_shown(self.root, "c/x|verify|dbi", "b" * 40, self.state)
        self.assertEqual(len(ps.load_state(self.root, self.state)["shown"]), 1)

    def test_same_cell_green_again_at_a_new_sha_is_a_new_showcase(self) -> None:
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c/x", "dbi", "fail")
            + row("r2", 200, "b" * 40, "c/x", "dbi", "pass")
        ))
        ps.record_shown(self.root, "c/x|verify|dbi", "b" * 40, self.state)
        self.assertEqual(self.select()[0], ps.EXIT_NOTHING_NEW)

        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c/x", "dbi", "fail")
            + row("r2", 200, "b" * 40, "c/x", "dbi", "pass")
            + row("r3", 300, "c" * 40, "c/x", "dbi", "fail")
            + row("r4", 400, "d" * 40, "c/x", "dbi", "pass")
        ))
        code, payload = self.select()
        self.assertEqual(code, ps.EXIT_SHOWCASE_DUE)
        self.assertEqual(payload["selected"]["hermit_sha"], "d" * 40)

    def test_corrupt_state_file_does_not_crash_or_suppress(self) -> None:
        (self.root / "ignored").mkdir()
        (self.root / self.state).write_text("{ not json")
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c/x", "dbi", "fail")
            + row("r2", 200, "b" * 40, "c/x", "dbi", "pass")
        ))
        self.assertEqual(self.select()[0], ps.EXIT_SHOWCASE_DUE)

    # -- the generated instruction carries the contract ---------------------

    def test_instruction_demands_sha_real_commands_and_real_output(self) -> None:
        self.write(self.primary, (
            row("r1", 100, "a" * 40, "c-programs/x", "dbi", "fail")
            + row("r2", 200, "b" * 40, "c-programs/x", "dbi", "pass")
        ))
        _, payload = self.select()
        text = ps.instruction(payload["selected"], payload["also_new"])
        self.assertIn("b" * 40, text)
        self.assertIn("exact commit hash", text)
        self.assertIn("copy-pastable", text)
        self.assertIn("ACTUALLY RUN", text)
        self.assertIn("REAL captured output", text)
        self.assertIn("if you did not run it", text)
        self.assertIn("FAILED", text)
        # Plain language must come BEFORE the shell, and be demanded explicitly.
        self.assertIn("PLAIN-LANGUAGE REQUIREMENT", text)
        self.assertIn("what the observed output means", text)
        self.assertIn("Shell is reproducible evidence, not the explanation.", text)
        # Reuse, do not manufacture.
        self.assertIn("do not invent a new demo", text)
        self.assertIn("hermit/demos/", text)
        # And it must tell the doer how to stop it repeating.
        self.assertIn("record-shown", text)

    def test_cli_reports_no_showcase_without_inventing_one(self) -> None:
        self.write(self.primary, row("r1", 100, "a" * 40, "c/x", "dbi", "pass"))
        out = StringIO()
        code = ps.main(["--root", str(self.root), "select"], out=out)
        self.assertEqual(code, ps.EXIT_NOTHING_NEW)
        self.assertIn("no periodic showcase is due", out.getvalue())
        self.assertNotIn("PLAIN-LANGUAGE", out.getvalue())


class WorkflowSourceTests(unittest.TestCase):
    """Static checks on the workflow. Reading it is the only inert way to test it.

    These are the properties whose violation killed the previous workflow, so
    they are asserted against the source rather than trusted.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PLUGIN.read_text()
        # Comments legitimately NAME the dead agent and the old workflow while
        # explaining why this rewrite exists. "No hardcoded agent" is a claim
        # about what the code DOES, so the agent-name assertions below run
        # against code only -- otherwise documenting the bug would fail the test
        # that the bug is fixed.
        cls.code = "\n".join(
            line.split("//")[0] if line.lstrip().startswith("//") else line
            for line in cls.source.split("\n")
        )

    def test_no_hardcoded_agent_target(self) -> None:
        # The crash was a wake aimed at a dead `hermit-codex-probe`.
        self.assertIn("hermit-codex-probe", self.source, "history must stay documented")
        self.assertNotIn("hermit-codex-probe", self.code)
        for name in ("hermit-lander", "hermit-coord", "orc-coord", "hermit-w"):
            self.assertNotIn(name, self.code)

    def test_every_wake_targets_the_empty_list(self) -> None:
        wakes = [
            index for index in range(len(self.source))
            if self.source.startswith("orc.sendWakeup(", index)
        ]
        self.assertGreater(len(wakes), 0, "the workflow sends no wake at all")
        for index in wakes:
            self.assertTrue(
                self.source[index:].startswith("orc.sendWakeup(\n        [],")
                or self.source[index:].startswith("orc.sendWakeup([],"),
                "a wake does not target the empty (coordinator) list",
            )

    def test_owner_facing_title_is_periodic_showcase(self) -> None:
        self.assertIn('SHOWCASE_TITLE = "Periodic showcase"', self.source)
        # The old name may appear ONLY as a retirement target. Anywhere else in
        # the code it would be an emitted title or a live dependency, which is
        # what the naming policy forbids -- so excise the retirement list and
        # require the rest of the file to be clean.
        start = self.code.index("const RETIRED_WORKFLOW_NAMES")
        end = self.code.index("];", start) + 2
        without_retirement_list = self.code[:start] + self.code[end:]
        self.assertNotIn("demo-presentation", without_retirement_list)
        self.assertNotIn("demo-run-owner-pitch-watch", without_retirement_list)

    def test_registration_is_inside_the_guarded_surface(self) -> None:
        # Anything registered at module scope re-runs in the reduced
        # workflow-restart context and crash-loops the workflow.
        surface = self.source.index("function registerPluginSurface()")
        for effect in ("orc.registerScript(", "orc.workflow("):
            first = self.source.index(effect)
            self.assertGreater(
                first, surface, effect + " is registered outside the guard"
            )
        self.assertLess(
            self.source.index("orc.registerScript("),
            self.source.index("orc.workflow("),
            "registerScript must come first so a restart aborts on it",
        )

    def test_registration_failure_is_loud_but_never_escapes(self) -> None:
        # config.js imports this plugin alongside hermit-dev, so an exception
        # escaping module scope could abort config evaluation and take the
        # POLICY plugin down to report a bug in a showcase.
        self.assertIn("SHOWCASE_ABSENT_NAME_SIGNATURE", self.code)
        self.assertNotIn("throw err", self.code)
        # ...but a silent swallow would recreate the "dead and says nothing"
        # failure this workflow exists to repair.
        tail = self.code[self.code.index("try {"):]
        self.assertIn('orc.log(', tail)
        self.assertIn('"error"', tail)

    def test_nothing_new_sends_no_wake(self) -> None:
        branch = self.source[self.source.index("SHOWCASE_NOTHING_NEW) {"):]
        branch = branch[: branch.index("} else if")]
        self.assertNotIn("sendWakeup", branch)

    def test_workflow_is_registered_restartable_with_backoff(self) -> None:
        self.assertIn("maxRestarts: 100", self.source)
        self.assertIn("backoffMs: 5_000", self.source)

    def test_config_loads_this_plugin(self) -> None:
        config = (PLUGIN.parent.parent.parent / "config.js").read_text()
        self.assertIn('import "./plugins/periodic-showcase/index.ts";', config)

    # -- retirement of the two superseded runtime workflows -----------------

    def test_both_superseded_workflows_are_retired(self) -> None:
        self.assertIn('"demo-presentation",', self.code)
        self.assertIn('"demo-run-owner-pitch-watch",', self.code)
        self.assertIn("orc.killWorkflow(name)", self.code)

    def test_retirement_runs_after_the_replacement_is_registered(self) -> None:
        # If retirement ran first and registration then failed, the old
        # workflows would be gone with nothing covering their responsibility.
        surface = self.code.index("function registerPluginSurface()")
        register = self.code.index("orc.workflow(", surface)
        retire = self.code.index("RETIRED_WORKFLOW_NAMES", register)
        self.assertGreater(
            retire, register, "retirement must come after the replacement registers"
        )
        # ...and inside the guarded surface, not at module scope.
        self.assertGreater(retire, surface)

    def test_a_failed_retirement_cannot_take_down_the_replacement(self) -> None:
        loop = self.code.index("for (const name of RETIRED_WORKFLOW_NAMES)")
        body = self.code[loop:loop + 600]
        self.assertIn("try {", body)
        self.assertIn("catch", body)
        self.assertNotIn("throw", body)

    def test_retirement_does_not_probe_an_unenumerable_api(self) -> None:
        # registerStartup works but is absent from orc.listEffects() on this
        # build, so a capability probe would report a false negative and
        # silently skip the retirement.
        self.assertIn("orc.registerStartup(", self.code)
        self.assertNotIn('hasOrcSurface("registerStartup")', self.code)
        self.assertNotIn('hasOrcSurface("killWorkflow")', self.code)


if __name__ == "__main__":
    unittest.main()
